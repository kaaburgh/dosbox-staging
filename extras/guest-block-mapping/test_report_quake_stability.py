"""Synthetic A/A fixture tests; do not require DOSBox or original Quake."""
import copy
import unittest

from report_quake_stability import analyze, pct


def fixture(fps):
    assert len(fps) % 4 == 0
    warmups = 4
    manifest = {'warmups': warmups, 'measured_repetitions': len(fps),
                'A_equals_B': True, 'profiled': False,
                'trace_compiled_out': True, 'dosbox_sha256': 'example'}
    rows = []
    for i, rate in enumerate([100]*warmups + fps):
        rows.append({
            'number': str(i+1),
            'warmup': str(int(i < warmups)),
            'label': 'W' if i < warmups else ['A','B','B','A'][(i-warmups)%4],
            'frames': '969', 'status': 'ok', 'fps': str(rate),
            'cgroup_throttled_events': '0', 'cgroup_throttled_ms': '0',
        })
    return rows, manifest


class StabilityTest(unittest.TestCase):
    def test_identical_AA_constant(self):
        rows, meta = fixture([100.0] * 12)
        result = analyze(rows, meta, bootstrap_reps=200)
        self.assertEqual(result['fps']['median'], 100)
        self.assertEqual(result['fps']['p10_p90_spread_pct_of_median'], 0)
        self.assertEqual(result['placebo_ABBA']['geometric_quartet_A_over_B_pct'], 0)
        self.assertEqual(result['placebo_ABBA']['quartet_bootstrap_95pct_interval_pct'], [0, 0])
        self.assertTrue(result['exploratory_precision_gates']['all_three'])

    def test_symmetric_order_cancels_linear_drift(self):
        # Every quartet has A,B,B,A: a locally linear log-FPS drift
        # produces zero placebo effect even if absolute FPS is moving.
        data = [80*(1.001**i) for i in range(16)]
        rows, meta = fixture(data)
        result = analyze(rows, meta, bootstrap_reps=250)
        self.assertAlmostEqual(result['placebo_ABBA']['geometric_quartet_A_over_B_pct'], 0.0, places=3)

    def test_toy_false_effect_detected(self):
        rows, meta = fixture([110, 100, 100, 110] * 4)
        result = analyze(rows, meta, bootstrap_reps=250)
        self.assertGreater(result['placebo_ABBA']['geometric_quartet_A_over_B_pct'], 9.9)
        self.assertFalse(result['exploratory_precision_gates']['all_three'])

    def test_corrupted_frames_and_order_fail_closed(self):
        rows, meta = fixture([100] * 8)
        rows[5]['frames'] = '968'
        with self.assertRaisesRegex(ValueError, 'invalid or missing'):
            analyze(rows, meta)
        rows, meta = fixture([100] * 8)
        rows[5]['label'] = 'A'
        with self.assertRaisesRegex(ValueError, 'broken'):
            analyze(rows, meta)

    def test_instrumented_and_unbalanced_rejected(self):
        rows, meta = fixture([100]*8)
        meta['profiled'] = True
        with self.assertRaisesRegex(ValueError, 'uninstrumented'):
            analyze(rows, meta)
        rows, meta = fixture([100]*8)
        rows.pop()
        with self.assertRaisesRegex(ValueError, 'run count'):
            analyze(rows, meta)

    def test_percentiles(self):
        self.assertEqual(pct([10, 20, 30, 40], .5), 25)
        self.assertEqual(pct([10, 20, 30, 40], .1), 13)


if __name__ == '__main__':
    unittest.main()
