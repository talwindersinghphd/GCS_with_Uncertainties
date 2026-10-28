import unittest
import numpy as np
from outline_uncertainty import (resample_trace, silhouette_boundary, outline_error,
    search_candidates, DEFAULT_SCALES, PARAMETERS, summarize)


class OutlineTests(unittest.TestCase):
    def test_trace_click_density_does_not_weight_score(self):
        a = resample_trace([[10, 10], [30, 10], [50, 10]])
        b = resample_trace([[10, 10], [11, 10], [12, 10], [30, 10], [50, 10]])
        np.testing.assert_allclose(a, b)
        with self.assertRaises(ValueError):
            resample_trace([[1, 1], [1, 1], [1, 1]])

    def test_known_boundary_and_offset(self):
        square = np.array([[20, 20], [80, 20], [80, 80], [20, 80]])
        boundary = silhouette_boundary(square, [[0, 1, 2, 3]], (100, 100), 1000)
        trace = resample_trace([[30, 80], [50, 80], [70, 80]])
        self.assertLess(outline_error(trace, boundary), 0.3)
        self.assertAlmostEqual(outline_error(trace + [0, 5], boundary), 5, delta=0.3)

    def test_exterior_preserves_concavity_not_convex_hull(self):
        # L-shaped union. A convex hull would replace the inner corner with a diagonal.
        points = np.array([[20, 20], [80, 20], [80, 40], [20, 40],
                           [20, 40], [40, 40], [40, 80], [20, 80]])
        boundary = silhouette_boundary(points, [[0, 1, 2, 3], [4, 5, 6, 7]], (100, 100), 1000)
        trace = resample_trace([[40, 45], [40, 55], [40, 70]])
        self.assertLess(outline_error(trace, boundary), 0.3)

    def test_crop_edge_is_not_a_model_boundary(self):
        points = np.array([[-200, -200], [200, -200], [200, 200], [-200, 200]])
        with self.assertRaises(ValueError):
            silhouette_boundary(points, [[0, 1, 2, 3]], (100, 100), 100)

    def test_angular_wrapping_and_reproducible_candidates(self):
        ref = dict(lat=0, lon=359, tilt=89, height=10, half_angle=30, kappa=.3)
        first = search_candidates(ref, DEFAULT_SCALES, 2, 9, 7)
        second = search_candidates(ref, DEFAULT_SCALES, 2, 9, 7)
        np.testing.assert_array_equal(first[0], second[0])
        self.assertTrue(((first[0][:, 1] >= 0) & (first[0][:, 1] < 360)).all())
        self.assertTrue(((first[0][:, 2] >= -90) & (first[0][:, 2] < 90)).all())
        summary, _ = summarize(first[0], first[1], ref, first[3])
        self.assertGreater(summary['lon']['median'], 350)
        self.assertGreater(summary['tilt']['median'], 80)

    def test_invalid_geometry_rejected_not_clipped(self):
        ref = dict(lat=89, lon=0, tilt=0, height=.1, half_angle=1, kappa=.01)
        values, offsets, invalid, _ = search_candidates(ref, DEFAULT_SCALES, 2, 9, 7)
        self.assertGreater(invalid, 0)
        self.assertEqual(len(values) + invalid, 512)
        self.assertTrue((values[:, 3:] > 0).all())

    def test_fixed_dimensions_and_search_edge_warning(self):
        ref = dict(lat=0, lon=0, tilt=0, height=10, half_angle=30, kappa=.3)
        scales = dict.fromkeys(PARAMETERS, 0.)
        scales['lon'] = 10
        values, offsets, _, halfwidth = search_candidates(ref, scales, 1, 9, 7)
        np.testing.assert_array_equal(values[:, 0], np.zeros(512))
        _, edges = summarize(values, offsets, ref, halfwidth)
        self.assertEqual(edges, ['lon'])
        scales['tilt'] = 100
        with self.assertRaises(ValueError):
            search_candidates(ref, scales, 1, 9, 7)


if __name__ == '__main__':
    unittest.main()
