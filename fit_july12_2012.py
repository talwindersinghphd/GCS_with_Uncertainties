"""Open the real, cached STEREO-A/B July 12, 2012 manual fitting session.

No network or historical target-event fit is used. Images are at 17:54 UTC.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import shutil

import numpy as np
from PyQt5 import QtWidgets
from sunpy.map import Map

import gui
from uncertainty_gui import export_result

DATE = datetime(2012, 7, 12, 17, 54)
# Neutral starting geometry, not a fit to this event.
INITIAL = dict(lat=0., lon=0., tilt=0., height=10., half_angle=25., kappa=.25)


def load_session_images(folder):
    canonical = folder / 'maps.npz'
    if canonical.exists():
        with np.load(canonical, allow_pickle=False) as cache:
            result = {}
            for sc in ('STA','STB'):
                if str(cache['format']) != 'gcs-fixed-normalization-v1':
                    raise ValueError('Unknown canonical example format.')
                a,b=cache[sc+'_base_data'].astype(float),cache[sc+'_target_data'].astype(float)
                amean,astd,bmean,bstd=cache[sc+'_coefficients']
                if astd <= 0 or bstd <= 0:
                    raise ValueError('Invalid example normalization coefficients.')
                result[sc] = Map((b-bmean)/bstd-(a-amean)/astd,json.loads(str(cache[sc+'_metadata'])))
        for image in result.values():
            if image.data.shape != (2048,2048) or not np.isfinite(image.data).all():
                raise ValueError('Invalid canonical example image.')
            if abs((image.date.to_datetime()-DATE).total_seconds()) > 60:
                raise ValueError('Canonical image has the wrong observation epoch.')
        return result
    result = {}
    for spacecraft in ('STA', 'STB'):
        target = Map(str(folder / (spacecraft + '_target_1754.jp2')))
        base = Map(str(folder / (spacecraft + '_base.jp2')))
        if abs((target.date.to_datetime() - DATE).total_seconds()) > 60:
            raise ValueError('Target image is not within one minute of 17:54 UTC.')
        if target.data.shape != base.data.shape:
            raise ValueError('Target and background image dimensions differ.')
        for key in ('hgln_obs', 'hglt_obs', 'dsun_obs'):
            if target.meta.get(key) is None:
                raise ValueError('Missing spacecraft geometry: ' + key)
        a, b = base.data.astype(float), target.data.astype(float)
        if not np.isfinite(a).all() or not np.isfinite(b).all() or a.std() == 0 or b.std() == 0:
            raise ValueError('Invalid background or target image data.')
        # Same standardized background subtraction as the original GUI. This is
        # a pre-event base difference, not an inferred outline or a fitted model.
        result[spacecraft] = Map((b - b.mean()) / b.std() - (a - a.mean()) / a.std(), target.meta)
    return result


class JulySession(gui.GCSGui):
    def __init__(self, folder, readonly=False):
        self.session_folder = folder
        self.readonly = readonly
        self.initial_params = INITIAL.copy()
        saved = folder / 'manual_fit.json'
        if saved.exists():
            self.initial_params = json.loads(saved.read_text())['parameters']
        images = load_session_images(folder)
        gui.load_image = lambda spacecraft, *args: images[spacecraft]
        gui.load_params = lambda date: self.initial_params.copy()
        super().__init__(DATE, ['STA', 'STB'], runndiff=True)
        self.setWindowTitle('12 July 2012 CME — TWO-TIER OUTLINES — STEREO A/B 17:54 UTC')
        for ax, spacecraft in zip(self._axes, self._spacecraft):
            ax.set_title(spacecraft + ' | ' + str(images[spacecraft].date) + ' UTC', fontsize=9)
        self.statusBar().showMessage('Make your manual fit, then open Outline uncertainty and trace the observed boundary. Save keeps this window open.')
        traces = folder / 'manual_traces.json'
        if traces.exists():
            legacy = self._uncertainty.restore_annotations(json.loads(traces.read_text()))
            self._control_tabs.setCurrentIndex(1)
            self.statusBar().showMessage('Saved fit restored. Old outlines are in Tier 1: reassign uncertain segments to Tier 2, then Save.'
                                        if legacy else 'Saved fit and both outline tiers restored. Review and Save before rerunning.')
        self._figure.canvas.draw_idle()

    def persist(self, include_search=False):
        self.session_folder.mkdir(parents=True, exist_ok=True)
        parameters = self.get_params_dict()
        payload = dict(date=DATE.isoformat() + 'Z', parameters=parameters,
                       note='User manual fit; not an automated best-fit estimate.')
        (self.session_folder / 'manual_fit.json').write_text(json.dumps(payload, indent=2))
        panel = self._uncertainty
        annotations = panel.annotation_payload()
        (self.session_folder / 'manual_traces.json').write_text(json.dumps(annotations, indent=2))
        self._figure.savefig(str(self.session_folder / 'manual_fit.png'), dpi=150)
        if include_search and panel.result is not None:
            destination = export_result(panel.result, self.session_folder)
            self._figure.savefig(str(destination / 'outline_overlays.png'), dpi=150)
        self.statusBar().showMessage('Saved fit, traces and figure to ' + str(self.session_folder))

    def save(self):
        if self._uncertainty.is_running():
            self.statusBar().showMessage('Wait for the search to finish or cancel it before saving the complete session.')
            return
        self.persist(include_search=True)

    def closeEvent(self, event):
        if self.readonly:
            event.accept()
            return
        if self._uncertainty.is_running():
            super().closeEvent(event)
            return
        if self.get_params_dict() != self.initial_params or any(self._uncertainty.tier_outlines.values()):
            self.persist(include_search=True)
        event.accept()


def prepare_example_session(folder):
    """Copy bundled inputs into a writable session without overwriting saved work."""
    example = Path(__file__).resolve().parent / 'examples' / 'july12_2012'
    folder.mkdir(parents=True, exist_ok=True)
    for path in (example / 'data').iterdir():
        if path.is_file() and not (folder / path.name).exists():
            shutil.copy2(path, folder / path.name)
    for name in ('manual_fit.json', 'manual_traces.json'):
        if not (folder / name).exists():
            shutil.copy2(example / name, folder / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path,
                        default=Path(__file__).resolve().parent / 'sessions' / 'july12_2012')
    parser.add_argument('--check-only', action='store_true', help='Render an initial preview and exit without saving a fit.')
    args = parser.parse_args()
    prepare_example_session(args.data_dir)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = JulySession(args.data_dir, readonly=args.check_only)
    if args.check_only:
        window.showNormal()
        window.resize(1500, 950)
        app.processEvents()
        window.grab().save(str(args.data_dir / 'ready_to_fit.png'))
        print('Real session verified; no fitted parameters or outlines generated.')
        return
    window.show()
    app.processEvents()
    print('Ready: July 12 TWO-TIER OUTLINES window. Saved fit and annotations are restored.',flush=True)
    app.exec_()


if __name__ == '__main__':
    main()
