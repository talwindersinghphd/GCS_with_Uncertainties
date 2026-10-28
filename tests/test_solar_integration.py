"""Solar-coordinate and GUI integration tests; requires the full GCS environment.

Run with QT_QPA_PLATFORM=offscreen for a headless check.
"""
import csv
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from sunpy.coordinates import frames
from sunpy.map import Map, make_fitswcs_header
from PyQt5 import QtWidgets

from outline_uncertainty import PARAMETERS
from uncertainty_gui import project_boundaries, SearchWorker, export_result

DATE = datetime(2012, 7, 12, 17, 24)
REFERENCE = dict(lat=0, lon=0, tilt=20, height=8, half_angle=30, kappa=.3)


def images():
    result = {}
    for i, longitude in enumerate((0, 60)):
        observer = SkyCoord(longitude * u.deg, 0 * u.deg, 1 * u.AU,
                            frame=frames.HeliographicStonyhurst, obstime=DATE)
        center = SkyCoord(0 * u.arcsec, 0 * u.arcsec,
                          frame=frames.Helioprojective(observer=observer, obstime=DATE))
        data = np.zeros((512, 512))
        header = make_fitswcs_header(data, center, scale=[40, 40] * u.arcsec / u.pix)
        result[i] = Map(data, header)
    return result


def config(im):
    b = project_boundaries(REFERENCE, DATE, im, 512)
    outlines = {}
    for i, xy in b.items():
        # One smooth arc of the synthesized exterior, sorted along image x.
        top = xy[xy[:, 1] > np.percentile(xy[:, 1], 90)]
        top = top[np.argsort(top[:, 0])]
        top = top[np.linspace(0, len(top) - 1, 10).astype(int)]
        outlines[str(i)] = top.tolist()
    return dict(reference=REFERENCE, outlines=outlines, scales=dict.fromkeys(PARAMETERS, .1),
                multiplier=1, exponent=6, seed=2022, resolution=512,
                tolerances={'0': 20., '1': 20.})


class SolarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.im = images()

    def test_real_coordinate_projection_search_and_export(self):
        worker = SearchWorker(config(self.im), self.im, DATE)
        results, failures = [], []
        worker.completed.connect(results.append)
        worker.failed.connect(failures.append)
        worker.run()
        self.assertFalse(failures, failures)
        result = results[0]
        self.assertEqual(result['tested'], 64)
        self.assertEqual(len(result['accepted']), 64)
        self.assertEqual(result['errors'].shape, (64, 2))
        self.assertTrue(np.isfinite(result['errors']).all())
        with tempfile.TemporaryDirectory() as root:
            destination = export_result(result, root)
            metadata = json.loads((destination / 'search.json').read_text())
            self.assertEqual(metadata['accepted_count'], 64)
            with (destination / 'selected_up_to_512.csv').open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 64)
            self.assertEqual(len({r['candidate_row'] for r in rows}), 64)

    def test_cancelled_search_with_no_results_exports_cleanly(self):
        worker = SearchWorker(config(self.im), self.im, DATE)
        worker.cancelled.set()
        results, failures = [], []
        worker.completed.connect(results.append)
        worker.failed.connect(failures.append)
        worker.run()
        self.assertFalse(failures, failures)
        self.assertTrue(results[0]['cancelled'])
        self.assertEqual(len(results[0]['accepted']), 0)
        with tempfile.TemporaryDirectory() as root:
            destination = export_result(results[0], root)
            self.assertEqual(len((destination / 'selected_up_to_512.csv').read_text().splitlines()), 1)

    def test_selection_of_512_preserves_joint_rows_without_replacement(self):
        parameter = np.linspace(0, 1, 600)
        values = np.column_stack((parameter, 2 * parameter, 3 * parameter,
                                 8 + parameter, 30 + parameter, .3 + parameter / 10))
        result = dict(accepted=values, offsets=values - values.mean(axis=0),
                      errors=np.zeros((600, 2)), view_indices=[0, 1],
                      summary={}, config=dict(seed=2022, reference=REFERENCE))
        with tempfile.TemporaryDirectory() as root:
            destination = export_result(result, root)
            with (destination / 'selected_up_to_512.csv').open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 512)
            self.assertEqual(len({r['candidate_row'] for r in rows}), 512)
            for row in rows:
                index = int(row['candidate_row'])
                np.testing.assert_allclose([float(row[k]) for k in PARAMETERS], values[index])

    def test_bad_view_cannot_be_hidden_by_good_view(self):
        c = config(self.im)
        c['outlines'] = {'0': [[0, 0], [1, 0], [2, 0]],
                         '1': [[0, 0], [1, 0], [2, 0]]}
        c['tolerances'] = {'0': 5, '1': 5}
        line = np.array([[0, 0], [1, 0], [2, 0]])
        def fake_projection(parameters, *args):
            if parameters is c['reference']:
                return {0: line, 1: line}
            return {0: line, 1: line + [0, 10]}
        worker = SearchWorker(c, self.im, DATE)
        results, failures = [], []
        worker.completed.connect(results.append)
        worker.failed.connect(failures.append)
        with patch('uncertainty_gui.project_boundaries', side_effect=fake_projection):
            worker.run()
        self.assertFalse(failures, failures)
        self.assertEqual(results[0]['tested'], 64)
        self.assertEqual(len(results[0]['accepted']), 0)

    def test_gui_can_trace_run_overlay_and_invalidate(self):
        # Avoid all network and file I/O in the original manual GUI startup.
        with patch('utils.helioviewer.get_helioviewer_client', return_value=None):
            import gui
        with patch.object(gui, 'load_image', side_effect=[self.im[0], self.im[1]]), \
             patch.object(gui, 'load_params', return_value=REFERENCE):
            window = gui.GCSGui(DATE, ['STA', 'STB'])
        panel = window._uncertainty
        c = config(self.im)
        panel.outlines = {int(k): v for k, v in c['outlines'].items()}
        for k in panel.outlines:
            panel.draw_trace(k)
        for spin in panel.scales.values():
            spin.setValue(.1)
        panel.exponent.setValue(6)
        for spin in panel.tolerances.values():
            spin.setValue(20)
        panel.resolution.setCurrentIndex(0)
        panel.start()
        from time import monotonic
        deadline = monotonic() + 120
        while panel.is_running() and monotonic() < deadline:
            self.app.processEvents()
            panel.worker.wait(20)
        self.app.processEvents()
        self.assertFalse(panel.is_running(), 'Background search did not finish')
        self.assertIsNotNone(panel.result, panel.report.toPlainText())
        self.assertTrue(panel.export_button.isEnabled())
        self.assertGreater(len(panel.overlays), 0)
        panel.scales['lat'].setValue(1)
        self.assertIsNone(panel.result)
        self.assertFalse(panel.export_button.isEnabled())
        window.close()


if __name__ == '__main__':
    unittest.main()
