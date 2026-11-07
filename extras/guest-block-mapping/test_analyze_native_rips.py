"""Synthetic-only tests; no Quake code or assets."""
import json
import tempfile
import unittest
from pathlib import Path

from analyze_trace import SAMPLE, events_from_csv, read_samples
from analyze_native_rips import analyze, load_snapshots, safe_blob

HEADER = 'event,monotonic_ns,pid,generation,guest_cs,guest_eip,guest_linear_begin,guest_linear_end,host_begin,host_end\n'


def event(kind, t, gen, eip=0x100, host=0x5000):
    return f'{kind},{t},222,{gen},0x30,0x{eip:x},0x{eip:x},0x{eip+4:x},0x{host:x},0x{host+32:x}\n'


class NativeRipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snap = self.root / 'snaps'
        self.snap.mkdir()

    def trace(self, text):
        f = self.root / 'trace.csv'
        f.write_text(HEADER + text)
        return events_from_csv(f)

    def snapshot(self, gen, eip=0x100, host=0x5000):
        prefix = f'jit-222-gen{gen}-cs48-eip{eip}'
        guest = prefix + '.guest.bin'
        native = prefix + '.host.bin'
        (self.snap / guest).write_bytes(bytes([0x90] * 4))
        (self.snap / native).write_bytes(bytes([0x90] * 32))
        meta = dict(schema=1, pid=222, generation=gen, guest_cs='0x30',
                    guest_eip=hex(eip), guest_linear_begin=hex(eip),
                    guest_linear_end=hex(eip + 4), host_begin=hex(host),
                    host_end=hex(host + 32), host_bytes=32,
                    guest_expected_bytes=4, guest_captured_bytes=4,
                    guest_complete=True, native_includes_deferred_slow_paths=True,
                    guest_filename=guest, host_filename=native)
        (self.snap / (prefix + '.json')).write_text(json.dumps(meta))
        return self.snap / (prefix + '.json')

    def test_generation_reuse_and_half_open_bounds(self):
        events = self.trace(event('C', 100, 1) + event('D', 200, 1) +
                            event('C', 300, 2, 0x200))
        self.snapshot(1)
        self.snapshot(2, eip=0x200)
        snaps = load_snapshots(self.snap, events)
        rows = [(150, 0x5000), (151, 0x500f), (152, 0x5010),
                (153, 0x501f), (154, 0x5020), (250, 0x5000),
                (300, 0x5001), (301, 0x501f)]
        summary, bins, exact = analyze(events, rows, snaps, bucket_size=16)
        self.assertEqual(summary['all_jit_samples'], 6)
        self.assertEqual(summary['non_jit_samples_unattributed'], 2)
        self.assertEqual(summary['selected_snapshot_samples'], 6)
        self.assertEqual([b['samples'] for b in summary['blocks']], [4, 2])
        self.assertEqual([(x['generation'], x['offset_start'], x['samples'])
                          for x in bins], [(1, 0, 2), (1, 16, 2), (2, 0, 1), (2, 16, 1)])
        self.assertEqual(len(exact), 6)
        self.assertNotIn('host_begin', summary['blocks'][0])

    def test_unselected_jit_not_counted_in_selected(self):
        evs = self.trace(event('C', 100, 1) + event('C', 110, 2, 0x200, 0x6000))
        self.snapshot(1)
        summary, _, _ = analyze(evs, [(120, 0x6002), (130, 0x5002)], load_snapshots(self.snap, evs))
        self.assertEqual(summary['all_jit_samples'], 2)
        self.assertEqual(summary['other_jit_samples'], 1)
        self.assertEqual(summary['selected_snapshot_samples'], 1)

    def test_reject_bad_metadata_and_blob_length(self):
        evs = self.trace(event('C', 100, 1))
        path = self.snapshot(1)
        data = json.loads(path.read_text())
        data['host_end'] = '0x5021'
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'host_end differs'):
            load_snapshots(self.snap, evs)
        data['host_end'] = '0x5020'
        path.write_text(json.dumps(data))
        (self.snap / data['host_filename']).write_bytes(b'X')
        with self.assertRaisesRegex(ValueError, 'blob size'):
            load_snapshots(self.snap, evs)

    def test_reject_filename_traversal(self):
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            safe_blob(self.snap, '../secret.host.bin', '.host.bin')

    def test_main_thread_filter_and_demo_window(self):
        path = self.root / 'samples.bin'
        path.write_bytes(b''.join([
            SAMPLE.pack(110, 0x5001, 222, 0),
            SAMPLE.pack(120, 0x5002, 223, 0),
            SAMPLE.pack(130, 0x5003, 222, 0),
        ]))
        self.assertEqual(read_samples(path, tid=222, start_ns=120, end_ns=140),
                         [(130, 0x5003)])

    def test_reject_bad_buckets(self):
        evs = self.trace(event('C', 100, 1))
        self.snapshot(1)
        with self.assertRaisesRegex(ValueError, 'power of two'):
            analyze(evs, [], load_snapshots(self.snap, evs), bucket_size=12)


if __name__ == '__main__':
    unittest.main()
