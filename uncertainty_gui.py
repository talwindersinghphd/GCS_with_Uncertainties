"""Optional, nonblocking uncertainty panel for the existing manual GCS GUI."""
import csv
import hashlib
import json
from pathlib import Path
import threading
import traceback

import numpy as np
from astropy import units as u
from PyQt5 import QtCore, QtWidgets

from geometry import gcs_mesh_sunpy
from outline_uncertainty import (PARAMETERS, DEFAULT_SCALES, SCALE_SOURCES,
    resample_trace, surface_faces, silhouette_boundary, outline_error,
    search_candidates, summarize)
from historical_sampling import guided_candidates, DEFAULT_HISTORY, load_history, suggested_scales
from tiered_outlines import prepare_checks, check_error, passes_checks

# Independent of the display mesh: scoring must not change with display mode.
STRAIGHT, FRONT, CIRCLE = 10, 24, 48
FACES = surface_faces((FRONT + STRAIGHT) * 2 - 3, CIRCLE)


def candidate_design(config):
    args = (config['reference'], config['scales'], config['multiplier'],
            config['exponent'], config['seed'])
    if config.get('sampling') == 'historical-mixture':
        return guided_candidates(*args, history_path=config.get('history_path', DEFAULT_HISTORY),
            exclude_date=config['date'][:10], broad_fraction=config.get('broad_fraction', .30))
    values, offsets, invalid, halfwidth = search_candidates(*args)
    return (values, offsets, invalid, halfwidth, np.full(len(values), 'broad'),
            np.full(len(values), -1), dict(method='Uniform bounded Sobol search.'))


def project_boundaries(parameters, date, images, resolution, density=1):
    straight, front, circle = STRAIGHT * density, FRONT * density, CIRCLE * density
    faces = FACES if density == 1 else surface_faces((front + straight) * 2 - 3, circle)
    mesh = gcs_mesh_sunpy(date, np.deg2rad(parameters['half_angle']),
                         parameters['height'], straight, front, circle,
                         parameters['kappa'], np.deg2rad(parameters['lat']),
                         np.deg2rad(parameters['lon']), np.deg2rad(parameters['tilt']))
    boundaries = {}
    for index, image in images.items():
        x, y = image.world_to_pixel(mesh.transform_to(image.coordinate_frame))
        boundaries[index] = silhouette_boundary(
            np.column_stack((x.to_value(u.pix), y.to_value(u.pix))),
            faces, image.data.shape, resolution)
    return boundaries


class SearchWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    completed = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, config, images, date, parent=None):
        super().__init__(parent)
        self.config, self.images, self.date = config, images, date
        self.cancelled = threading.Event()

    def run(self):
        try:
            c = self.config
            checks = prepare_checks(c)
            boundaries = project_boundaries(c['reference'], self.date, self.images,
                                            c['resolution'], c.get('mesh_density', 1))
            baseline = {check['key']: check_error(check, boundaries[check['view']]) for check in checks}
            if not all(np.isfinite(v) for v in baseline.values()):
                raise ValueError('Reference scores must be finite.')
            values, offsets, invalid, halfwidth, labels, cases, sampling_info = candidate_design(c)
            accepted, accepted_offsets, errors, overlays = [], [], [], []
            accepted_labels, accepted_cases = [], []
            projection_failures, tested = 0, 0
            indices = sorted(set(check['view'] for check in checks))
            for row, delta, label, source_case in zip(values, offsets, labels, cases):
                if self.cancelled.is_set():
                    break
                try:
                    b = project_boundaries(dict(zip(PARAMETERS, row)), self.date,
                                           self.images, c['resolution'], c.get('mesh_density', 1))
                    score = [check_error(check, b[check['view']]) for check in checks]
                except (ValueError, FloatingPointError):
                    projection_failures += 1
                    tested += 1
                    continue
                tested += 1
                if passes_checks(score, baseline, checks):
                    accepted.append(row.copy())
                    accepted_offsets.append(delta.copy())
                    errors.append(score)
                    accepted_labels.append(str(label))
                    accepted_cases.append(int(source_case))
                    # Reservoir selection avoids displaying only the first accepted fits.
                    if len(overlays) < 20:
                        overlays.append(b)
                    else:
                        slot = np.random.default_rng(c['seed'] + tested).integers(len(accepted))
                        if slot < 20:
                            overlays[slot] = b
                if tested % 32 == 0:
                    self.progress.emit('Scored {}/{} valid candidates; accepted {}.'
                                       .format(tested, len(values), len(accepted)))
            accepted = np.asarray(accepted).reshape(-1, 6)
            accepted_offsets = np.asarray(accepted_offsets).reshape(-1, 6)
            summary, edges = summarize(accepted, accepted_offsets, c['reference'], halfwidth)
            result = dict(config=c, reference_scores=baseline,
                          accepted=accepted, offsets=accepted_offsets,
                          errors=np.asarray(errors).reshape(-1, len(checks)),
                          view_indices=indices, summary=summary, search_edges=edges,
                          score_components=[{k: check[k] for k in ('key', 'view', 'tier', 'allowance')} for check in checks],
                          invalid_geometry=invalid, projection_failures=projection_failures,
                          tested=tested, proposed=2 ** c['exponent'],
                          cancelled=self.cancelled.is_set(), overlays=overlays,
                          sampling_info=sampling_info,
                          accepted_sampling_labels=np.asarray(accepted_labels, dtype='U32'),
                          accepted_history_cases=np.asarray(accepted_cases, dtype=int),
                          reference_boundaries=boundaries)
            self.completed.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class UncertaintyPanel:
    def __init__(self, gui, layout):
        self.gui = gui
        self.worker = None
        self.result = None
        self.outlines, self.lines, self.overlays = {}, {}, []
        self.settings_revision = 0
        box = QtWidgets.QGroupBox('Outline-compatible uncertainty')
        form = QtWidgets.QVBoxLayout(box)
        self._form = form
        note = QtWidgets.QLabel('Trace visible leading edge / flanks.\nLeft-click adds points; right-click ends tracing.\nRanges are search results, not confidence intervals.')
        note.setWordWrap(True)
        form.addWidget(note)
        self.sampling = QtWidgets.QComboBox()
        self.sampling.addItem('Historical differences + broad search', 'historical-mixture')
        self.sampling.addItem('Broad search only', 'uniform')
        self.sampling.setToolTip('History guides angular proposals only. Current event date and '
                                'three convention-flagged events are excluded. No speed or flux is inferred.')
        form.addWidget(self.sampling)
        self.broad_fraction = QtWidgets.QSpinBox()
        self.broad_fraction.setRange(25, 100)
        self.broad_fraction.setSingleStep(5)
        self.broad_fraction.setValue(30)
        mixture_form = QtWidgets.QFormLayout()
        mixture_form.addRow('Broad exploration [%]', self.broad_fraction)
        form.addLayout(mixture_form)
        self.trace_button = QtWidgets.QPushButton('Trace outline')
        self.trace_button.setCheckable(True)
        self.trace_button.toggled.connect(self.toggle_trace)
        form.addWidget(self.trace_button)
        self.view = QtWidgets.QComboBox()
        for i, sc in enumerate(gui._spacecraft):
            self.view.addItem('{}: {}'.format(i + 1, sc), i)
        form.addWidget(self.view)
        row = QtWidgets.QHBoxLayout()
        undo = QtWidgets.QPushButton('Undo point')
        clear = QtWidgets.QPushButton('Clear view')
        undo.clicked.connect(lambda: self.edit_trace(False))
        clear.clicked.connect(lambda: self.edit_trace(True))
        row.addWidget(undo)
        row.addWidget(clear)
        form.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        save_trace = QtWidgets.QPushButton('Save traces + fit')
        load_trace = QtWidgets.QPushButton('Load traces + fit')
        save_trace.clicked.connect(self.save_traces)
        load_trace.clicked.connect(self.load_traces)
        row.addWidget(save_trace)
        row.addWidget(load_trace)
        form.addLayout(row)
        self.scales = {}
        self.scale_sources = SCALE_SOURCES.copy()
        starting_scales = DEFAULT_SCALES.copy()
        try:
            historical_events, _ = load_history(exclude_date=gui._date.date().isoformat())
            starting_scales.update(suggested_scales(historical_events))
            for key in PARAMETERS[:3]:
                self.scale_sources[key] = 'Mean eventwise pairwise angular range after review exclusions and target-date holdout; not a sigma'
        except (OSError, ValueError):
            self.sampling.setCurrentIndex(1)
        grid = QtWidgets.QFormLayout()
        self._parameter_form = grid
        for key in PARAMETERS:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(0, 180 if key != 'kappa' else 1)
            spin.setValue(starting_scales[key])
            spin.setToolTip(self.scale_sources[key] + '; zero fixes this parameter')
            self.scales[key] = spin
            grid.addRow(key + ' search scale', spin)
        self.multiplier = QtWidgets.QDoubleSpinBox()
        self.multiplier.setRange(0.1, 10)
        self.multiplier.setSingleStep(0.5)
        self.multiplier.setValue(2)
        grid.addRow('Range = ±scale ×', self.multiplier)
        self.tolerances = {}
        for i, sc in enumerate(gui._spacecraft):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(0, 1000)
            spin.setDecimals(2)
            spin.setValue(3)
            spin.setToolTip('Allowed additional RMS error above the manual fit, in native image pixels. '
                            'Set using observed boundary ambiguity; 3 is only a starting value.')
            grid.addRow('{} {} allowance [px]'.format(i + 1, sc), spin)
            self.tolerances[i] = spin
        self.exponent = QtWidgets.QSpinBox()
        self.exponent.setRange(6, 20)
        self.exponent.setValue(12)
        grid.addRow('Candidates = 2^', self.exponent)
        self.seed = QtWidgets.QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.seed.setValue(2022)
        grid.addRow('Random seed', self.seed)
        self.resolution = QtWidgets.QComboBox()
        for n in (512, 1024, 2048):
            self.resolution.addItem(str(n), n)
        self.resolution.setCurrentIndex(1)
        grid.addRow('Silhouette raster size', self.resolution)
        self.mesh_density = QtWidgets.QComboBox()
        self.mesh_density.addItem('Standard', 1)
        self.mesh_density.addItem('Fine (slower)', 2)
        grid.addRow('Scoring surface mesh', self.mesh_density)
        form.addLayout(grid)
        self.preview_button = QtWidgets.QPushButton('Score manual fit only')
        self.preview_button.clicked.connect(self.preview)
        form.addWidget(self.preview_button)
        self.run_button = QtWidgets.QPushButton('Freeze current fit and sample')
        self.run_button.clicked.connect(self.start)
        self.cancel_button = QtWidgets.QPushButton('Cancel search')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.export_button = QtWidgets.QPushButton('Export search and accepted fits')
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        form.addWidget(self.run_button)
        form.addWidget(self.cancel_button)
        form.addWidget(self.export_button)
        self.status = QtWidgets.QLabel('No outline search yet.')
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(160)
        form.addWidget(self.report)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        scroll.setMinimumWidth(300)
        layout.addWidget(scroll, stretch=1)
        gui._figure.canvas.mpl_connect('button_press_event', self.click)
        self.edit_controls = [self.trace_button, self.view, undo, clear, save_trace, load_trace,
                              self.sampling, self.broad_fraction,
                              self.multiplier, self.exponent, self.seed, self.resolution,
                              self.mesh_density, self.preview_button,
                              *self.scales.values(), *self.tolerances.values()]
        # Changes after a completed run invalidate its exports to prevent stale results.
        for control in [*self.scales.values(), *self.tolerances.values(),
                        self.multiplier, self.exponent, self.seed]:
            control.valueChanged.connect(self.invalidate)
        self.broad_fraction.valueChanged.connect(self.invalidate)
        self.sampling.currentIndexChanged.connect(self.invalidate)
        self.resolution.currentIndexChanged.connect(self.invalidate)
        self.mesh_density.currentIndexChanged.connect(self.invalidate)
        for slider in self.sliders():
            slider.valueChanged.connect(self.invalidate)

    def sliders(self):
        return [getattr(self.gui, '_s_' + k) for k in PARAMETERS]

    def invalidate(self, *args):
        self.settings_revision += 1
        self.result = None
        self.export_button.setEnabled(False)
        for artist in self.overlays:
            artist.remove()
        self.overlays = []
        self.report.clear()
        self.status.setText('Inputs changed. Freeze current fit and sample again.')
        self.gui._figure.canvas.draw_idle()

    def toggle_trace(self, active):
        if active:
            self.status.setText('Click along one visible outline per image. Do not bridge hidden arcs. '
                                'Right-click ends tracing. Zoom/pan must be off.')

    def click(self, event):
        if not self.trace_button.isChecked() or self.is_running():
            return
        if event.button == 3:
            self.trace_button.setChecked(False)
            return
        if event.button != 1 or event.inaxes not in self.gui._axes:
            return
        if getattr(self.gui._figure.canvas.toolbar, 'mode', ''):
            return
        index = self.gui._axes.index(event.inaxes)
        self.view.setCurrentIndex(index)
        x, y = event.xdata, event.ydata
        h, w = self.gui._images[index].data.shape
        margin = 3 * max(h, w) / self.resolution.currentData()
        if x is None or y is None or not (margin < x < w - 1 - margin and margin < y < h - 1 - margin):
            self.status.setText('Trace inside the image, away from its cropped edge.')
            return
        self.outlines.setdefault(index, []).append([float(x), float(y)])
        self.invalidate()
        self.draw_trace(index)

    def draw_trace(self, index):
        if index in self.lines:
            self.lines[index].remove()
            del self.lines[index]
        points = self.outlines.get(index, [])
        if points:
            xy = np.asarray(points)
            self.lines[index] = self.gui._axes[index].plot(xy[:, 0], xy[:, 1],
                'o-', color='cyan', lw=1, ms=3, scalex=False, scaley=False)[0]
        self.gui._figure.canvas.draw_idle()

    def edit_trace(self, clear):
        index = self.view.currentData()
        if clear:
            self.outlines.pop(index, None)
        elif self.outlines.get(index):
            self.outlines[index].pop()
        self.invalidate()
        self.draw_trace(index)

    def is_running(self):
        return self.worker is not None and self.worker.isRunning()

    def image_identity(self):
        return [dict(spacecraft=sc, date=str(image.date), shape=list(image.data.shape),
                     data_sha256=hashlib.sha256(np.ascontiguousarray(image.data).tobytes()).hexdigest(),
                     metadata={str(k): str(v) for k, v in image.meta.items()})
                for sc, image in zip(self.gui._spacecraft, self.gui._images)]

    def save_traces(self):
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.gui, 'Save traces and current manual fit', 'gcs_traces.json', 'JSON (*.json)')
        if not filename:
            return
        try:
            payload = dict(format='gcs-outlines-v1', images=self.image_identity(),
                           reference=self.gui.get_params_dict(), outlines=self.outlines)
            Path(filename).write_text(json.dumps(payload, indent=2, allow_nan=False))
            self.status.setText('Saved traces and manual fit.')
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.gui, 'Cannot save traces', str(exc))

    def load_traces(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self.gui, 'Load traces and manual fit for these exact images', '', 'JSON (*.json)')
        if not filename:
            return
        try:
            payload = json.loads(Path(filename).read_text())
            if payload.get('format') != 'gcs-outlines-v1' or payload.get('images') != self.image_identity():
                raise ValueError('These traces belong to different images, viewpoints, or image metadata.')
            reference = payload['reference']
            outlines = {int(k): v for k, v in payload['outlines'].items()}
            if set(reference) != set(PARAMETERS):
                raise ValueError('Invalid saved manual-fit parameter names.')
            for key in PARAMETERS:
                number = reference[key]
                control = getattr(self.gui, '_s_' + key).numbox
                if not np.isfinite(number) or not control.minimum() <= number <= control.maximum():
                    raise ValueError('Saved fit is outside the manual slider limits.')
            for index, points in outlines.items():
                if index < 0 or index >= len(self.gui._images):
                    raise ValueError('Invalid image index in trace file.')
                if points:
                    xy = np.asarray(points, dtype=float)
                    h, w = self.gui._images[index].data.shape
                    if xy.ndim != 2 or xy.shape[1] != 2 or not np.isfinite(xy).all():
                        raise ValueError('Invalid outline coordinates.')
                    if (xy < 0).any() or (xy[:, 0] >= w).any() or (xy[:, 1] >= h).any():
                        raise ValueError('Saved outline lies outside its image.')
            for index in list(self.lines):
                self.outlines.pop(index, None)
                self.draw_trace(index)
            self.outlines = outlines
            for key, number in reference.items():
                getattr(self.gui, '_s_' + key).numbox.setValue(number)
            for index in outlines:
                self.draw_trace(index)
            self.invalidate()
            self.status.setText('Restored traces and manual fit. Search settings remain as displayed.')
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.gui, 'Cannot load traces', str(exc))

    def cancel(self):
        if self.worker is not None:
            self.worker.cancelled.set()
            self.status.setText('Cancelling after the current projection...')

    def preview(self):
        try:
            traces = {k: resample_trace(v) for k, v in self.outlines.items() if len(v)}
            if not traces:
                raise ValueError('Draw at least three points in one image first.')
            boundaries = project_boundaries(self.gui.get_params_dict(), self.gui._date,
                {k: self.gui._images[k] for k in traces}, self.resolution.currentData(),
                self.mesh_density.currentData())
            self.invalidate()
            lines = ['Reference RMS / allowed RMS [native image pixels]:']
            for k, xy in boundaries.items():
                error = outline_error(traces[k], xy)
                lines.append('{}: {:.2f} / {:.2f}'.format(self.gui._spacecraft[k], error,
                                                        error + self.tolerances[k].value()))
                self.overlays.append(self.gui._axes[k].plot(xy[:, 0], xy[:, 1], '.',
                    color='magenta', alpha=.65, ms=1, scalex=False, scaley=False)[0])
            self.report.setPlainText('\n'.join(lines))
            self.status.setText('Magenta: reference exterior. Check it follows the same feature you traced. '
                                'Use allowance to represent boundary ambiguity, then sample.')
            self.gui._figure.canvas.draw_idle()
        except (ValueError, FloatingPointError) as exc:
            QtWidgets.QMessageBox.warning(self.gui, 'Cannot score reference', str(exc))

    def start(self):
        if self.is_running():
            return
        try:
            outlines = {str(k): v for k, v in self.outlines.items() if len(v)}
            if not outlines:
                raise ValueError('Draw an outline with at least three points in one or more images.')
            for points in outlines.values():
                resample_trace(points)
            config = dict(reference=self.gui.get_params_dict(), outlines=outlines,
                scales={k: v.value() for k, v in self.scales.items()},
                scale_sources=self.scale_sources, multiplier=self.multiplier.value(),
                exponent=self.exponent.value(), seed=self.seed.value(),
                resolution=self.resolution.currentData(),
                mesh_density=self.mesh_density.currentData(),
                tolerances={k: self.tolerances[int(k)].value() for k in outlines},
                date=self.gui._date.isoformat(),
                sampling=self.sampling.currentData(),
                broad_fraction=self.broad_fraction.value() / 100.,
                history_path=str(DEFAULT_HISTORY),
                running_difference=bool(self.gui._runndiff),
                image_identity=self.image_identity(),
                views={str(i): dict(spacecraft=self.gui._spacecraft[i],
                    image_date=str(image.date), shape=list(image.data.shape),
                    metadata={str(k): str(v) for k, v in image.meta.items()})
                    for i, image in enumerate(self.gui._images)},
                geometry_mesh=dict(straight=STRAIGHT * self.mesh_density.currentData(),
                    front=FRONT * self.mesh_density.currentData(),
                    circle=CIRCLE * self.mesh_density.currentData()),
                interpretation='Proposal-dependent compatibility search; accepted quantiles are NOT calibrated credible/confidence intervals.',
                score='Per-view observed-to-model exterior-boundary RMS in native image pixels; 128 arclength trace points.',
                angular_convention='Longitude modulo 360; geometric tilt modulo 180. No magnetic polarity is inferred.')
            candidate_design(config)
            p = config['reference']
            if not (abs(p['lat']) <= 90 and p['height'] > 0 and
                    0 < p['half_angle'] < 90 and 0 < p['kappa'] < 1):
                raise ValueError('Manual fit needs height >0, 0<half_angle<90, 0<kappa<1.')
        except (ValueError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self.gui, 'Cannot start search', str(exc))
            return
        self.trace_button.setChecked(False)
        self.invalidate()
        self.set_running(True)
        images = {int(k): self.gui._images[int(k)] for k in outlines}
        self.worker = SearchWorker(config, images, self.gui._date, self.gui)
        self.worker.progress.connect(self.status.setText)
        self.worker.completed.connect(self.finished)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(lambda: self.set_running(False))
        self.worker.start()
        self.status.setText('Computing reference silhouette and sampling...')

    def set_running(self, active):
        for control in [*self.edit_controls, *self.sliders()]:
            control.setEnabled(not active)
        self.run_button.setEnabled(not active)
        self.cancel_button.setEnabled(active)

    def failed(self, message):
        self.status.setText('Search failed; see error details.')
        self.report.setPlainText(message)

    def finished(self, result):
        self.result = result
        self.export_button.setEnabled(True)
        for b in result['overlays']:
            for index, xy in b.items():
                artist = self.gui._axes[index].plot(xy[:, 0], xy[:, 1], '.',
                    color='orange', alpha=0.12, ms=1, scalex=False, scaley=False)[0]
                self.overlays.append(artist)
        for index, xy in result['reference_boundaries'].items():
            artist = self.gui._axes[index].plot(xy[:, 0], xy[:, 1], '.',
                color='magenta', alpha=0.65, ms=1, scalex=False, scaley=False)[0]
            self.overlays.append(artist)
        count = len(result['accepted'])
        self.status.setText('{}Accepted {} of {} scored; {} invalid geometries, {} unusable projections. '
            'Cyan: your trace. Magenta: reference boundary. Orange: accepted examples.'
            .format('CANCELLED — partial search. ' if result['cancelled'] else '', count,
                    result['tested'], result['invalid_geometry'], result['projection_failures']))
        lines = ['DESIGN-DEPENDENT RANGES; NOT CONFIDENCE INTERVALS.', 'Reference RMS / allowed RMS [px]:']
        sampling = result.get('sampling_info', {})
        if 'eligible_events' in sampling:
            lines.insert(1, 'History: {} eligible events; target date held out. {} guided proposals; '
                         '{} outside-box attempts; {} broad fallbacks.'.format(
                             sampling['eligible_events'], sampling['historical_rows_proposed'],
                             sampling['outside_box_attempts'], sampling['broad_fallback_rows']))
            lines.append('History guides exploration. Accepted frequencies depend on the proposal mixture.')
        for k, e in result['reference_scores'].items():
            lines.append('{}: {:.2f} / {:.2f}'.format(self.gui._spacecraft[k], e,
                         e + result['config']['tolerances'][str(k)]))
        lines.append('Parameter: manual | median | accepted min..max')
        for k, s in result['summary'].items():
            lines.append('{}: {:.3f} | {:.3f} | {:.3f}..{:.3f}'.format(
                k, result['config']['reference'][k], s['median'], s['min'], s['max']))
        if result['search_edges']:
            lines.append('Accepted fits near search limits: ' + ', '.join(result['search_edges']) +
                         '. Widen these ranges and rerun; angular ranges cannot repeat orientations.')
        if count < 512:
            lines.append('Fewer than 512 accepted fits: increase candidate count or reassess search/tolerance. No duplicates will be generated.')
        lines.append('Check stability at higher raster and mesh resolution before scientific use.')
        self.report.setPlainText('\n'.join(lines))
        self.gui._figure.canvas.draw_idle()

    def export(self):
        if self.result is None:
            return
        root = QtWidgets.QFileDialog.getExistingDirectory(self.gui, 'Choose parent folder for search export')
        if not root:
            return
        try:
            destination = export_result(self.result, root)
            self.gui._figure.savefig(str(destination / 'outline_overlays.png'), dpi=150)
            self.status.setText('Saved search to ' + str(destination))
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.gui, 'Export failed', str(exc))


def export_result(result, root):
    """Export every accepted row plus a reproducible subset, without duplicates."""
    from datetime import datetime
    import uuid
    destination = Path(root) / ('gcs_uncertainty_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    destination.mkdir(parents=True, exist_ok=False)
    metadata = {k: v for k, v in result.items()
                if k not in ('accepted', 'offsets', 'errors', 'overlays', 'reference_boundaries',
                             'accepted_sampling_labels', 'accepted_history_cases')}
    metadata['accepted_count'] = len(result['accepted'])
    metadata['selection_method'] = 'Seeded uniform subset of accepted rows; NOT a Sobol design or calibrated posterior.'
    metadata['software'] = dict(numpy=np.__version__)
    metadata['units'] = dict(lat='degrees', lon='degrees modulo 360',
                             tilt='geometric degrees modulo 180', height='solar radii',
                             half_angle='degrees', kappa='dimensionless', errors='native image pixels')
    if len(result['offsets']) > 1:
        metadata['parameter_order'] = list(PARAMETERS)
        metadata['accepted_offset_covariance'] = np.cov(result['offsets'], rowvar=False).tolist()
    with (destination / 'search.json').open('w') as file:
        json.dump(metadata, file, indent=2, allow_nan=False)
    with (destination / 'parameter_summary.csv').open('w', newline='') as file:
        fields = ['parameter', 'manual', 'median', 'min', 'max', 'p2_5', 'p16', 'p84', 'p97_5']
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for key, stats in result['summary'].items():
            writer.writerow(dict(parameter=key, manual=result['config']['reference'][key], **stats))
    components = result.get('score_components', [])
    score_names = ['rms_px_' + c['key'] if c['tier'] is not None else 'rms_px_view_' + str(c['view'])
                   for c in components] if components else ['rms_px_view_' + str(k) for k in result['view_indices']]
    fieldnames = list(PARAMETERS) + score_names
    has_sources = 'accepted_sampling_labels' in result
    if has_sources:
        fieldnames += ['proposal_source', 'historical_case']
    def write_rows(path, selection):
        with path.open('w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['candidate_row'] + fieldnames)
            for i in selection:
                row = [int(i)] + result['accepted'][i].tolist() + result['errors'][i].tolist()
                if has_sources:
                    row += [str(result['accepted_sampling_labels'][i]), int(result['accepted_history_cases'][i])]
                writer.writerow(row)
    count = len(result['accepted'])
    write_rows(destination / 'accepted_all.csv', np.arange(count))
    selected = np.random.default_rng(result['config']['seed']).choice(count, min(512, count), replace=False)
    write_rows(destination / 'selected_up_to_512.csv', selected)
    np.savez_compressed(destination / 'accepted_arrays.npz', parameters=result['accepted'],
                        offsets=result['offsets'], errors=result['errors'], selected=selected)
    return destination
