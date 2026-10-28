import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from historical_sampling import load_history, guided_candidates
from outline_uncertainty import PARAMETERS

REFERENCE = dict(lat=0, lon=359, tilt=89, height=10, half_angle=30, kappa=.3)
SCALES = dict(lat=10, lon=10, tilt=10, height=1, half_angle=5, kappa=.05)


class HistoryTests(unittest.TestCase):
    def fixture(self, root):
        path = Path(root) / 'history.json'
        events = [dict(case=i, date='2010-01-0' + str(i), review_required=i == 3,
                       pairs=[dict(delta=[2., 4., 6.])] * (1 if i != 2 else 10))
                  for i in (1, 2, 3)]
        path.write_text(json.dumps(dict(format='gcs-fit-differences-v1',
                                       source={}, method='test', events=events)))
        return path

    def test_target_date_and_review_flags_are_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            events, metadata = load_history(path, '2010-01-01')
            self.assertEqual([e['case'] for e in events], [2])
            self.assertEqual(len(metadata['excluded']), 2)

    def test_joint_differences_and_event_weighting(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            values, delta, invalid, halfwidth, labels, cases, info = guided_candidates(
                REFERENCE, SCALES, 2, 12, 2022, path, smoothing=0, broad_fraction=.25)
            guided = labels == 'history-angular'
            self.assertEqual(guided.sum(), 3072)
            self.assertEqual(invalid, 0)
            np.testing.assert_allclose(delta[guided, 1], 2 * delta[guided, 0])
            np.testing.assert_allclose(delta[guided, 2], 3 * delta[guided, 0])
            fraction_case_one = (cases[guided] == 1).mean()
            self.assertGreater(fraction_case_one, .45)
            self.assertLess(fraction_case_one, .55)  # Not 1/11 despite unequal pair counts.
            self.assertTrue((delta[guided, 0] > 0).any())
            self.assertTrue((delta[guided, 0] < 0).any())
            self.assertEqual((labels == 'broad').sum(), 1024)

    def test_missing_tilt_is_broadly_sampled_not_zero_filled(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            model = json.loads(path.read_text())
            for event in model['events']:
                for pair in event['pairs']:
                    pair['delta'][2] = None
            path.write_text(json.dumps(model))
            result = guided_candidates(REFERENCE, SCALES, 2, 9, 7, path, smoothing=0)
            guided = result[4] == 'history-direction'
            self.assertGreater(np.std(result[1][guided, 2]), 5)
            self.assertEqual(result[6]['historical_rows_with_joint_tilt'], 0)

    def test_fixed_parameters_bounds_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            scales = SCALES.copy()
            scales['lat'] = 0
            a = guided_candidates(REFERENCE, scales, 2, 9, 8, path)
            b = guided_candidates(REFERENCE, scales, 2, 9, 8, path)
            np.testing.assert_array_equal(a[0], b[0])
            np.testing.assert_array_equal(a[1][:, 0], 0)
            self.assertTrue((np.abs(a[1]) <= a[3] + 1e-12).all())

    def test_outside_box_attempts_fall_back_without_clipping(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.fixture(root)
            scales = dict.fromkeys(PARAMETERS, .001)
            result = guided_candidates(REFERENCE, scales, 1, 6, 7, path, smoothing=0)
            self.assertGreater(result[6]['broad_fallback_rows'], 0)
            self.assertTrue((result[4] == 'broad').all())
            self.assertTrue((np.abs(result[1]) < result[3]).all())

    def test_real_calibration_holds_out_july2012(self):
        events, metadata = load_history(exclude_date='2012-07-12')
        self.assertEqual(len(events), 53)
        self.assertNotIn(52, [e['case'] for e in events])
        self.assertNotIn(29, [e['case'] for e in events])
        self.assertTrue(any(e['reason'] == 'target event' for e in metadata['excluded']))


if __name__ == '__main__':
    unittest.main()
