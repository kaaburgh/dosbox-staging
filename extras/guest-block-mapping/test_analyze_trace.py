"""Lifecycle correctness, especially an executable host-address being reused."""
import tempfile
import unittest
from pathlib import Path
from analyze_trace import events_from_csv, classify, read_samples, aggregate_by_guest, SAMPLE

HEADER = 'event,monotonic_ns,pid,generation,guest_cs,guest_eip,guest_linear_begin,guest_linear_end,host_begin,host_end\n'


def ev(kind, t, gen, g=0x100, begin=0x5000, end=0x5100):
    return f'{kind},{t},222,{gen},0x30,0x{g:x},0x{g:x},0x{g+10:x},0x{begin:x},0x{end:x}\n'


class TraceTests(unittest.TestCase):
    def write(self, contents):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name) / 'trace.csv'
        p.write_text(HEADER + contents)
        return p

    def test_reused_host_address_is_attributed_to_correct_generation(self):
        rows = ev('C', 100, 1, 0x100) + ev('D', 200, 1) + ev('C', 300, 2, 0x900)
        events = events_from_csv(self.write(rows))
        summary, ranked = classify(events, [(150, 0x5040), (250, 0x5040),
                                            (350, 0x5040), (350, 0x6000)])
        self.assertEqual(summary['jit_samples'], 2)
        self.assertEqual(summary['non_jit_samples'], 2)
        self.assertEqual({b.guest_eip for b, count in ranked}, {0x100, 0x900})
        self.assertEqual(summary['max_simultaneously_live'], 1)

    def test_exclusive_host_end(self):
        events = events_from_csv(self.write(ev('C', 100, 1)))
        summary, ranked = classify(events, [(100, 0x5000), (101, 0x50ff), (102, 0x5100)])
        self.assertEqual(summary['jit_samples'], 2)
        self.assertEqual(summary['non_jit_samples'], 1)

    def test_overlapping_live_intervals_rejected(self):
        events = events_from_csv(self.write(ev('C', 100, 1) + ev('C', 200, 2, 0x200, 0x5080, 0x5200)))
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            classify(events, [(201, 0x5080)])

    def test_duplicate_generation_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            events_from_csv(self.write(ev('C', 100, 1) + ev('D', 150, 1) + ev('C', 200, 1)))

    def test_clear_without_create_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown or already cleared'):
            classify(events_from_csv(self.write(ev('D', 100, 1))), [])

    def test_nonmonotonic_events_rejected(self):
        with self.assertRaisesRegex(ValueError, 'non-monotonic'):
            events_from_csv(self.write(ev('C', 200, 1) + ev('D', 100, 1)))

    def test_guest_aggregation_across_generations(self):
        rows = ev('C', 100, 1, 0x100) + ev('D', 200, 1) + ev('C', 300, 2, 0x100)
        _, ranked = classify(events_from_csv(self.write(rows)),
                             [(150, 0x5040), (350, 0x5040), (351, 0x5041)])
        self.assertEqual(aggregate_by_guest(ranked), [((0x30, 0x100, 0x100), 3)])

    def test_filter_other_threads_and_timestamps(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'samples.bin'
            path.write_bytes(b''.join([
                SAMPLE.pack(100, 0x100, 222, 0),
                SAMPLE.pack(110, 0x110, 223, 0),
                SAMPLE.pack(120, 0x120, 222, 0),
                SAMPLE.pack(130, 0x130, 222, 0),
            ]))
            self.assertEqual(read_samples(path, tid=222, start_ns=110, end_ns=120), [(120, 0x120)])

    def test_sample_binary_layout_and_incomplete_record(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'samples.bin'
            path.write_bytes(SAMPLE.pack(12345, 0xABC, 222, 0))
            self.assertEqual(read_samples(path), [(12345, 0xABC)])
            path.write_bytes(b'abc')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                read_samples(path)


if __name__ == '__main__':
    unittest.main()
