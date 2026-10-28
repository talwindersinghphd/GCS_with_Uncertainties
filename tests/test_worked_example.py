"""Portability check for the bundled real observations and saved annotations."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt5 import QtWidgets
import gui
from fit_july12_2012 import prepare_example_session,JulySession


class WorkedExample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_offline_example_restores_both_tiers_without_modifying_fixtures(self):
        fixture=Path(__file__).resolve().parent.parent/'examples/july12_2012/manual_traces.json'
        before=hashlib.sha256(fixture.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            prepare_example_session(folder)
            with patch.object(gui,'load_image'),patch.object(gui,'load_params'):
                window=JulySession(folder,readonly=True)
                panel=window._uncertainty
                self.assertEqual(window.get_params_dict()['lat'],-11.9)
                self.assertEqual(set(panel.tier_outlines),{0,1})
                self.assertEqual([len(a) for a in panel.tier_outlines[0]['2']],[4])
                self.assertEqual([len(a) for a in panel.tier_outlines[1]['2']],[3])
                panel.preview()
                self.assertIn('STA Tier 1',panel.report.toPlainText())
                self.assertIn('STB Tier 2',panel.report.toPlainText())
                window.close()
            saved=folder/'manual_fit.json'
            saved.write_text('user-edited session marker')
            prepare_example_session(folder)
            self.assertEqual(saved.read_text(),'user-edited session marker')
        self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),before)


if __name__=='__main__':
    unittest.main()
