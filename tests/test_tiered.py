import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PyQt5 import QtWidgets
from tiered_outlines import sample_arcs, weighted_error, prepare_checks, passes_checks, move_segment
from uncertainty_gui import SearchWorker, export_result
from test_solar_integration import images, REFERENCE, DATE


class TierRules(unittest.TestCase):
    def test_manual_baseline_is_retained_even_when_clear_section_mismatch_is_large(self):
        config = dict(tier_outlines={'0':{'1':[[[0,0],[1,0]]], '2':[[[5,0],[6,0]]]}},
                      tier_tolerances={'0':{'1':3.,'2':12.}})
        checks = prepare_checks(config)
        baseline = {checks[0]['key']:100., checks[1]['key']:2.}
        self.assertTrue(passes_checks([102.,13.],baseline,checks))
        self.assertFalse(passes_checks([104.,0.],baseline,checks))
        self.assertFalse(passes_checks([90.,15.],baseline,checks))
        self.assertTrue(passes_checks([80.,1.],baseline,checks))
        with self.assertRaises(ValueError):
            passes_checks([90.],baseline,checks)

    def test_disconnected_arcs_do_not_create_a_bridge(self):
        sample = sample_arcs([[[0,0],[10,0]], [[100,0],[110,0]]])
        self.assertFalse(((sample[0][:,0] > 10) & (sample[0][:,0] < 100)).any())
        boundary = np.column_stack((np.r_[np.linspace(0,10,128), np.linspace(100,110,128)],np.zeros(256)))
        self.assertAlmostEqual(weighted_error(sample,boundary),0)
        self.assertAlmostEqual(sample[1].sum(),20)

    def test_arclength_weighting_not_point_density_or_arc_count(self):
        a = sample_arcs([[[0,0],[10,0]], [[100,4],[130,4]]])
        b = sample_arcs([[[0,0],[1,0],[2,0],[10,0]], [[100,4],[115,4],[130,4]]])
        np.testing.assert_allclose(a[0],b[0])
        np.testing.assert_allclose(a[1],b[1])
        boundary = np.column_stack((np.linspace(0,130,26001),np.zeros(26001)))
        self.assertAlmostEqual(weighted_error(a,boundary),np.sqrt(12),places=4)
        split = sample_arcs([[[0,0],[10,0]], [[100,4],[115,4]], [[115,4],[130,4]]])
        self.assertAlmostEqual(weighted_error(a,boundary),weighted_error(split,boundary),places=5)

    def test_segment_reassignment_preserves_every_original_edge(self):
        tiers = {'1':[[[0,0],[1,1],[2,1],[3,0]]], '2':[]}
        move_segment(tiers,'1',0,1,'2')
        self.assertEqual(tiers['1'],[[[0,0],[1,1]],[[2,1],[3,0]]])
        self.assertEqual(tiers['2'],[[[1,1],[2,1]]])
        self.assertEqual(sum(len(a)-1 for arcs in tiers.values() for a in arcs),3)

    def test_missing_tier_is_omitted_and_less_certain_tier_has_more_flexibility(self):
        c = dict(tier_outlines={'0':{'2':[[[0,0],[1,0]]]}},tier_tolerances={'0':{'1':3.,'2':12.}})
        self.assertEqual(len(prepare_checks(c)),1)
        c['tier_outlines']['0']['1'] = [[[5,0],[6,0]]]
        c['tier_tolerances']['0']['2'] = 1
        with self.assertRaises(ValueError):
            prepare_checks(c)


class TierIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.images = images()

    def test_worker_uses_independent_baselines_and_exports_each_tier(self):
        config = dict(reference=REFERENCE, scales=dict(lat=.1,lon=.1,tilt=.1,height=.1,half_angle=.1,kappa=.01),
            multiplier=1,exponent=6,seed=2022,resolution=512,
            tier_outlines={'0':{'1':[[[0,0],[1,0],[2,0]]], '2':[[[100,0],[101,0],[102,0]]]}},
            tier_tolerances={'0':{'1':3.,'2':12.}})
        def run(clear_y,unclear_y):
            def project(parameters,*args):
                a,b=(20,0) if parameters is config['reference'] else (clear_y,unclear_y)
                return {0:np.vstack((np.column_stack((np.linspace(0,2,128),np.full(128,a))),
                                     np.column_stack((np.linspace(100,102,128),np.full(128,b)))))}
            worker = SearchWorker(config,{0:self.images[0]},DATE)
            results, failures = [],[]
            worker.completed.connect(results.append)
            worker.failed.connect(failures.append)
            with patch('uncertainty_gui.project_boundaries',side_effect=project):
                worker.run()
            self.assertFalse(failures,failures)
            return results[0]
        result = run(22,11)
        self.assertEqual(len(result['accepted']),64)
        self.assertAlmostEqual(result['reference_scores']['view_0_tier_1'],20,places=3)
        self.assertEqual(result['errors'].shape,(64,2))
        self.assertEqual(len(run(24,0)['accepted']),0)
        self.assertEqual(len(run(0,13)['accepted']),0)
        with tempfile.TemporaryDirectory() as root:
            destination=export_result(result,root)
            headers=(destination/'accepted_all.csv').read_text().splitlines()[0]
            self.assertIn('rms_px_view_0_tier_1',headers)
            self.assertIn('rms_px_view_0_tier_2',headers)
            metadata=json.loads((destination/'search.json').read_text())
            self.assertEqual(len(metadata['score_components']),2)

    def test_legacy_annotations_can_be_reclassified_and_round_trip_as_two_tiers(self):
        import gui
        with patch.object(gui,'load_image',side_effect=[self.images[0],self.images[1]]), \
             patch.object(gui,'load_params',return_value=REFERENCE):
            window=gui.GCSGui(DATE,['STA','STB'])
        panel=window._uncertainty
        original=[[100.,100.],[200.,120.],[300.,100.]]
        payload=dict(format='gcs-outlines-v1',images=panel.image_identity(),reference=window.get_params_dict(),outlines={'0':original})
        self.assertTrue(panel.restore_annotations(payload))
        panel.tier.setCurrentIndex(1)
        panel.assign.setChecked(True)
        x,y=window._axes[0].transData.transform([150,110])
        panel.click(SimpleNamespace(button=1,inaxes=window._axes[0],x=x,y=y,xdata=150.,ydata=110.))
        self.assertEqual(panel.tier_outlines[0]['2'],[original[:2]])
        self.assertEqual(panel.tier_outlines[0]['1'],[original[1:]])
        panel.tolerances[0].setValue(4)
        panel.tier2_tolerances[0].setValue(16)
        panel.seed.setValue(17)
        saved=json.loads(json.dumps(panel.annotation_payload()))
        panel.tier_outlines={}
        panel.seed.setValue(2022)
        self.assertFalse(panel.restore_annotations(saved))
        self.assertEqual(panel.tier_outlines[0]['2'],[original[:2]])
        self.assertEqual(panel.tier2_tolerances[0].value(),16)
        self.assertEqual(panel.seed.value(),17)
        self.assertEqual(window.get_params_dict(),payload['reference'])
        self.assertEqual(saved['format'],'gcs-outlines-v3')
        panel.preview()
        self.assertIn('Tier 1',panel.report.toPlainText())
        self.assertIn('Tier 2',panel.report.toPlainText())
        panel.assign.setChecked(False)
        window.close()


if __name__=='__main__':
    unittest.main()
