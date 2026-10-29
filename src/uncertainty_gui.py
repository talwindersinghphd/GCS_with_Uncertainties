"""Optional, nonblocking uncertainty panel for the existing manual GCS GUI."""
import csv
import hashlib
import json
from pathlib import Path
import threading
import time
import traceback

import numpy as np
from PyQt5 import QtCore, QtWidgets

from .distance_metric import PHYSICAL_UNIT, ProjectedDistance, score_unit
from .outline_uncertainty import (PARAMETERS, DEFAULT_SCALES, SCALE_SOURCES,
    resample_trace, outline_error,
    search_candidates, summarize)
from .historical_sampling import guided_candidates, DEFAULT_HISTORY, load_history, suggested_scales
from .tiered_outlines import prepare_checks, check_error
from .search_scoring import (STRAIGHT, FRONT, CIRCLE, project_boundaries, display_boundaries,
    available_cpus, score_candidate, parallel_scores, create_scoring_pool)
from .utils.widgets import SearchStatusLabel, CompactDoubleSpinBox
from .outline_preview import OutlinePreview

DEFAULT_MAX_PROPOSALS = 65536
MAX_OVERLAY_FITS = 20
MAX_LIVE_OVERLAY_FITS = 6
LIVE_PREVIEW_INTERVAL_MS = 1000
LIVE_HISTOGRAM_INTERVAL_MS = 1000
ACCEPTED_OUTLINE_STYLE = dict(color='#e779a5', alpha=.25, ms=1)
MANUAL_OUTLINE_STYLE = dict(color='magenta', alpha=.85, ms=1.5, zorder=3)


def ensemble_selection(result):
    """Choose the requested number reproducibly from all accepted rows."""
    count = len(result['accepted'])
    target = result['config'].get('target_accepted') or 512
    if result.get('stopping_rule') == 'first-accepted-in-candidate-order':
        return np.arange(min(target, count))
    return np.sort(np.random.default_rng(result['config']['seed']).choice(
        count, min(target, count), replace=False))


def completion_status(result):
    target = result['config'].get('target_accepted')
    count = len(result['accepted'])
    if target is None:
        return '{}Fits tried: {} | Accepted: {}.\nChoose Save to save the fit, outlines and search results.'.format(
            'Search cancelled. ' if result['cancelled'] else 'Search complete. ', result['tested'], count)
    reason = {'target_reached': 'Target reached.', 'proposal_limit': 'Search limit reached.',
              'cancelled': 'Search cancelled.'}[result['stop_reason']]
    return '{} Fits tried: {} | Accepted: {} / {}.\nEnsemble: {} fits. Choose Save to save all results.'.format(
        reason, result['tested'], count, target, min(target, count))


def candidate_design(config):
    args = (config['reference'], config['scales'], config['multiplier'],
            config['exponent'], config['seed'])
    if config.get('sampling') == 'historical-mixture':
        return guided_candidates(*args, history_path=config.get('history_path', DEFAULT_HISTORY),
            exclude_date=config['date'][:10], broad_fraction=config.get('broad_fraction', .30))
    values, offsets, invalid, halfwidth = search_candidates(*args)
    return (values, offsets, invalid, halfwidth, np.full(len(values), 'broad'),
            np.full(len(values), -1), dict(method='Uniform bounded Sobol search.'))


class SearchWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(str)
    preview_ready = QtCore.pyqtSignal(object)
    accepted_fit = QtCore.pyqtSignal(object, object)
    completed = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, config, images, date, parent=None, display_images=None):
        super().__init__(parent)
        self.config, self.images, self.date = config, images, date
        self.display_images = display_images if display_images is not None else images
        self._last_overlay_preview = None
        self.cancelled = threading.Event()
        self._target_reached = threading.Event()

    def report_progress(self, tried, total, accepted):
        target = self.config.get('target_accepted')
        if target is None:
            text = 'Fits tried: {} / {} | Accepted: {}'.format(tried, total, accepted)
        else:
            text = 'Fits tried: {} | Accepted: {} / {}'.format(tried, accepted, target)
            if accepted >= target:
                text += ' — target reached'
        self.progress.emit(text)

    def report_preview(self, reference, choices, force=False):
        if choices and not force and self._last_overlay_preview is not None and (
                time.monotonic() - self._last_overlay_preview < LIVE_PREVIEW_INTERVAL_MS / 1000.):
            return
        if choices:
            self._last_overlay_preview = time.monotonic()
        ordered = sorted(choices, key=lambda item: item[1])
        preview = dict(reference_boundaries=reference, config=self.config,
            overlays=[item[2] for item in ordered], overlay_keys=[item[1] for item in ordered],
            overlay_parameters=[dict(zip(PARAMETERS, self._preview_values[item[1]].tolist())) for item in ordered])
        self.preview_ready.emit(preview)
        return preview

    def serial_scores(self, values, checks, baseline):
        for index, row in enumerate(values):
            if self.cancelled.is_set() or self._target_reached.is_set():
                break
            yield (index, *score_candidate(row, self.config, self.images, self.date,
                                          checks, baseline, project=project_boundaries))

    def run(self):
        pool = None
        self._target_reached.clear()
        self._preview_values = {}
        self._last_overlay_preview = None
        try:
            c = self.config
            checks = prepare_checks(c, self.images)
            boundaries = project_boundaries(c['reference'], self.date, self.images,
                                            c['resolution'], c.get('mesh_density', 1))
            baseline = {check['key']: check_error(check, boundaries[check['view']]) for check in checks}
            if not all(np.isfinite(v) for v in baseline.values()):
                raise ValueError('Reference scores must be finite.')
            self.report_preview(boundaries, [])
            target = c.get('target_accepted')
            if target is not None and (type(target) is not int or target < 1):
                raise ValueError('Target accepted fits must be a positive integer.')
            batch_size = 2 ** c['exponent']
            budget = c.get('max_proposals', max(DEFAULT_MAX_PROPOSALS, batch_size)) if target is not None else batch_size
            if type(budget) is not int or not batch_size <= budget <= 2 ** 20:
                raise ValueError('Proposal limit must cover a full batch and be at most 1048576.')
            accepted_records, errors, overlay_choices = [], [], []
            projection_failures, tested, invalid, proposed, duplicates = 0, 0, 0, 0, 0
            inflight_accepted = 0
            seen, batches = set(), []
            sampling_info = None
            indices = sorted(set(check['view'] for check in checks))
            requested_cpus = c.get('cpus', 1)
            if not isinstance(requested_cpus, int) or requested_cpus < 1:
                raise ValueError('CPU count must be a positive integer.')
            cpus = min(requested_cpus, available_cpus())
            while proposed + batch_size <= budget:
                # Generate complete Sobol designs, but evaluate only the prefix
                # needed for the first N accepted fits in candidate order.
                batch_seed = (c['seed'] + len(batches)) % (2 ** 32)
                design = dict(c, seed=batch_seed)
                values, offsets, bad, halfwidth, labels, cases, info = candidate_design(design)
                if sampling_info is None:
                    sampling_info = dict(info)
                else:
                    for key in ('historical_rows_proposed', 'historical_rows_with_joint_tilt',
                                'outside_box_attempts', 'broad_fallback_rows'):
                        if key in info:
                            sampling_info[key] += info[key]
                keep = []
                for index, row in enumerate(values):
                    identity = tuple(row)
                    if identity not in seen:
                        seen.add(identity)
                        keep.append(index)
                batch_duplicates = len(values) - len(keep)
                duplicates += batch_duplicates
                values, offsets, labels, cases = (array[keep] for array in (values, offsets, labels, cases))
                start = proposed
                proposed += batch_size
                invalid += bad
                batch_tested, before_accepted = tested, len(accepted_records)
                before_inflight = inflight_accepted
                self.report_progress(tested, tested + len(values), len(accepted_records))
                if pool is None and cpus > 1 and len(values) and not self.cancelled.is_set():
                    pool = create_scoring_pool(c, self.images, self.date, baseline, cpus)
                scores = (parallel_scores(values, c, self.images, self.date, baseline,
                                         cpus, self.cancelled, pool=pool,
                                         stop_submitting=self._target_reached)
                          if cpus > 1 and len(values) and not self.cancelled.is_set()
                          else self.serial_scores(values, checks, baseline))
                for index, score, b in scores:
                    tested += 1
                    if score is None:
                        projection_failures += 1
                    elif b is not None:
                        if self._target_reached.is_set():
                            # These later candidates were already running when
                            # the target was established. Keep the exact first N.
                            inflight_accepted += 1
                            self.report_progress(tested, batch_tested + len(values), len(accepted_records))
                            continue
                        ordinal = start + index
                        record = (ordinal, values[index].copy(), offsets[index].copy(),
                                  str(labels[index]), int(cases[index]))
                        accepted_records.append(record)
                        self.accepted_fit.emit(record[1], record[2])
                        errors.append(score)
                        # Priorities and final ordering are independent of CPU completion order.
                        priority = np.random.default_rng(c['seed'] + ordinal).random()
                        overlay_choices.append((priority, ordinal, b))
                        overlay_choices.sort(key=lambda item: (item[0], item[1]))
                        del overlay_choices[MAX_OVERLAY_FITS:]
                        self._preview_values = {item[1]: self._preview_values[item[1]]
                            for item in overlay_choices if item[1] in self._preview_values}
                        if any(item[1] == ordinal for item in overlay_choices):
                            self._preview_values[ordinal] = record[1]
                            self.report_preview(boundaries, overlay_choices)
                        if target is not None and len(accepted_records) >= target:
                            self._target_reached.set()
                    self.report_progress(tested, batch_tested + len(values), len(accepted_records))
                complete = tested - batch_tested == len(values)
                batches.append(dict(seed=batch_seed, proposed=batch_size, invalid_geometry=bad,
                    duplicate_proposals=batch_duplicates,
                    tested=tested-batch_tested, accepted=len(accepted_records)-before_accepted,
                    inflight_accepted_not_retained=inflight_accepted-before_inflight,
                    complete=complete, sampling_info=info))
                if self.cancelled.is_set():
                    break
                if target is None or len(accepted_records) >= target:
                    break
            order = sorted(range(len(accepted_records)), key=lambda index: accepted_records[index][0])
            records = [accepted_records[index] for index in order]
            accepted = np.asarray([row[1] for row in records]).reshape(-1, 6)
            accepted_offsets = np.asarray([row[2] for row in records]).reshape(-1, 6)
            errors = np.asarray(errors).reshape(-1, len(checks))[order]
            preview = self.report_preview(boundaries, overlay_choices, force=True)
            summary, edges = summarize(accepted, accepted_offsets, c['reference'], halfwidth)
            result = dict(config=c, reference_scores=baseline,
                          accepted=accepted, offsets=accepted_offsets,
                          errors=errors,
                          view_indices=indices, display_view_indices=sorted(self.display_images),
                          summary=summary, search_edges=edges,
                          distance_unit=score_unit(c),
                          distance_geometry={str(i): ProjectedDistance(image).provenance()
                              for i, image in self.images.items()} if score_unit(c) == PHYSICAL_UNIT else {},
                          score_components=[{k: check[k] for k in ('key', 'view', 'tier', 'allowance')} for check in checks],
                          invalid_geometry=invalid, projection_failures=projection_failures,
                          tested=tested, proposed=proposed, proposal_limit=budget,
                          duplicate_proposals=duplicates,
                          inflight_accepted_not_retained=inflight_accepted,
                          stopping_rule=('first-accepted-in-candidate-order' if target is not None else 'fixed-budget'),
                          target_candidate_ordinal=(records[-1][0] if target is not None and len(records) >= target else None),
                          batches=batches,
                          batch_sampling='Independent scrambled Sobol batches are generated; seed = (seed + batch index) modulo 2^32. Evaluation can stop mid-batch. Filtering and early stopping do not preserve pure Sobol balance.',
                          stop_reason=('cancelled' if self.cancelled.is_set() else
                              'target_reached' if target is not None and len(records) >= target else
                              'proposal_limit' if target is not None else 'fixed_budget'),
                          cpu_workers=cpus,
                          cancelled=self.cancelled.is_set(), overlays=preview['overlays'],
                          overlay_keys=preview['overlay_keys'], overlay_parameters=preview['overlay_parameters'],
                          sampling_info=sampling_info,
                          accepted_sampling_labels=np.asarray([row[3] for row in records], dtype='U32'),
                          accepted_history_cases=np.asarray([row[4] for row in records], dtype=int),
                          reference_boundaries=boundaries)
            self.completed.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            if pool is not None:
                pool.shutdown(wait=True)


class UncertaintyPanel:
    def __init__(self, gui, layout):
        self.gui = gui
        self.worker = None
        self.result = None
        self.outlines, self.lines, self.overlays = {}, {}, []
        self._accepted_overlay_artists = {}
        self._manual_overlay_artists = {}
        self._pending_preview = None
        self._preview_projection = None
        self._preview_future = None
        self._preview_source = None
        self._final_preview_result = None
        self._preview_timer = QtCore.QTimer(gui)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(LIVE_PREVIEW_INTERVAL_MS)
        self._preview_timer.timeout.connect(self.flush_preview)
        gui.destroyed.connect(self._shutdown_preview_projection)
        self._live_histogram_result = None
        self._histogram_dirty = False
        self._histogram_timer = QtCore.QTimer(gui)
        self._histogram_timer.setSingleShot(True)
        self._histogram_timer.setInterval(LIVE_HISTOGRAM_INTERVAL_MS)
        self._histogram_timer.timeout.connect(self.flush_live_histograms)
        self.settings_revision = 0
        box = QtWidgets.QGroupBox('Draw Outline')
        form = QtWidgets.QVBoxLayout(box)
        self._form = form
        note = QtWidgets.QLabel('Trace visible leading edge / flanks.\nLeft-click adds points; right-click ends tracing.')
        note.setWordWrap(True)
        form.addWidget(note)
        # Keep search/persistence controls for scripts and saved sessions, but
        # expose only the annotation workflow in the GUI.
        self._internal_controls = QtWidgets.QWidget(box)
        internal_form = QtWidgets.QVBoxLayout(self._internal_controls)
        self._internal_controls.hide()
        self.sampling = QtWidgets.QComboBox()
        self.sampling.addItem('Historical differences + broad search', 'historical-mixture')
        self.sampling.addItem('Broad search only', 'uniform')
        self.sampling.setToolTip('History guides angular proposals only. Current event date and '
                                'three convention-flagged events are excluded. No speed or flux is inferred.')
        internal_form.addWidget(self.sampling)
        self.broad_fraction = QtWidgets.QSpinBox()
        self.broad_fraction.setRange(0, 100)
        self.broad_fraction.setSingleStep(5)
        self.broad_fraction.setValue(30)
        self.broad_fraction.setSuffix(' %')
        self.broad_fraction.setToolTip('Requested broad Sobol proposal share within the configured ranges around the manual fit. '
                                      'The history-guided percentage automatically makes up the remainder.')
        self.guided_fraction = QtWidgets.QSpinBox()
        self.guided_fraction.setRange(0, 100)
        self.guided_fraction.setSingleStep(5)
        self.guided_fraction.setValue(70)
        self.guided_fraction.setSuffix(' %')
        self.guided_fraction.setToolTip('Requested share guided by historical angular differences. Height, half-angle and aspect '
            'ratio stay broadly sampled. Historical proposals outside the bounds can fall back to broad search, '
            'so actual tried and accepted proportions can differ.')
        self.trace_button = QtWidgets.QPushButton('Trace outline')
        self.trace_button.setCheckable(True)
        self.trace_button.toggled.connect(self.toggle_trace)
        form.addWidget(self.trace_button)
        self.mode_label = QtWidgets.QLabel('Mode: VIEWING — editing off')
        self.mode_label.setWordWrap(True)
        font = self.mode_label.font()
        font.setBold(True)
        self.mode_label.setFont(font)
        form.addWidget(self.mode_label)
        self.view = QtWidgets.QComboBox()
        for i, sc in enumerate(gui._view_labels):
            self.view.addItem('{}: {}'.format(i + 1, sc), i)
        form.addWidget(self.view)
        row = QtWidgets.QHBoxLayout()
        undo = QtWidgets.QPushButton('Undo point')
        self.undo_button = undo
        clear = QtWidgets.QPushButton('Clear view')
        undo.clicked.connect(lambda: self.edit_trace(False))
        clear.clicked.connect(lambda: self.edit_trace(True))
        row.addWidget(undo)
        row.addWidget(clear)
        internal_form.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        save_trace = QtWidgets.QPushButton('Save traces + fit')
        load_trace = QtWidgets.QPushButton('Load traces + fit')
        save_trace.clicked.connect(self.save_traces)
        load_trace.clicked.connect(self.load_traces)
        row.addWidget(save_trace)
        row.addWidget(load_trace)
        internal_form.addLayout(row)
        self.scales = {}
        self.scale_sources = SCALE_SOURCES.copy()
        starting_scales = DEFAULT_SCALES.copy()
        self.historical_scales = {}
        self._history_available = True
        try:
            historical_events, _ = load_history(exclude_date=gui._date.date().isoformat())
            self.historical_scales = suggested_scales(historical_events)
            starting_scales.update(self.historical_scales)
            for key in PARAMETERS[:3]:
                self.scale_sources[key] = 'Mean eventwise pairwise angular range after review exclusions and target-date holdout; not a sigma'
        except (OSError, ValueError):
            self._history_available = False
            self.sampling.setCurrentIndex(1)
        grid = QtWidgets.QFormLayout()
        self._parameter_form = grid
        for key in PARAMETERS:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setDecimals(2 if key == 'kappa' else 1)
            spin.setSingleStep(.01 if key == 'kappa' else .1)
            spin.setRange(0, 180 if key != 'kappa' else 1)
            spin.setValue(starting_scales[key])
            spin.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            spin.setToolTip(self.scale_sources[key] + '; zero fixes this parameter')
            self.scales[key] = spin
        self.default_scales = {key: spin.value() for key, spin in self.scales.items()}
        self.default_scale_sources = self.scale_sources.copy()
        self.multiplier = QtWidgets.QDoubleSpinBox()
        self.multiplier.setRange(0.1, 10)
        self.multiplier.setSingleStep(0.5)
        self.multiplier.setValue(2)
        self.multiplier.setToolTip('Search half-width = scale × multiplier. The default multiplier of 2 is exploratory, not a confidence level.')
        self.tolerances = {}
        for i, sc in enumerate(gui._view_labels):
            spin = CompactDoubleSpinBox()
            spin.setRange(0, 100)
            spin.setDecimals(10)
            spin.setSingleStep(.01)
            spin.setValue(.05)
            spin.setToolTip('Additional projected RMS distance above the manual fit, in solar radii. '
                            '0.05 is an editable starting allowance, not a measured uncertainty.')
            grid.addRow('{} {} allowance [RSun]'.format(i + 1, sc), spin)
            self.tolerances[i] = spin
        self.exponent = QtWidgets.QSpinBox()
        self.exponent.setRange(6, 20)
        self.exponent.setValue(12)
        grid.addRow('Sobol batch size = 2^', self.exponent)
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
        internal_form.addLayout(grid)
        self.preview_button = QtWidgets.QPushButton('Score manual fit only')
        self.preview_button.clicked.connect(self.preview)
        internal_form.addWidget(self.preview_button)
        self.range_control = QtWidgets.QWidget()
        range_layout = QtWidgets.QVBoxLayout(self.range_control)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(3)
        ranges = QtWidgets.QGridLayout()
        ranges.setContentsMargins(0, 0, 0, 0)
        ranges.setHorizontalSpacing(6)
        ranges.setVerticalSpacing(2)
        ranges.setAlignment(QtCore.Qt.AlignLeft)
        for column, title in enumerate(('Parameter', 'Scale', 'Historical\ndifference')):
            label = QtWidgets.QLabel(title)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            ranges.addWidget(label, 0, column)
        self.historical_labels = {}
        labels = {'lat': 'Latitude [°]', 'lon': 'Longitude [°]', 'tilt': 'Tilt [°]',
                  'height': 'Height [RSun]', 'half_angle': 'Half-angle [°]', 'kappa': 'κ'}
        for row, key in enumerate(PARAMETERS, 1):
            ranges.addWidget(QtWidgets.QLabel(labels[key]), row, 0)
            ranges.addWidget(self.scales[key], row, 1)
            historical = self.historical_scales.get(key)
            self.historical_labels[key] = QtWidgets.QLabel('' if historical is None else '{:.1f}'.format(historical))
            self.historical_labels[key].setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.historical_labels[key].setToolTip(
                'Mean eventwise fit-to-fit angular difference after review exclusions and target-date holdout. '
                'Reference only; not multiplied by the range multiplier.'
                if historical is not None else 'No historical difference is available for this parameter.')
            ranges.addWidget(self.historical_labels[key], row, 2)
        range_layout.addLayout(ranges)
        range_note = QtWidgets.QLabel('Exploratory defaults: height, half-angle, κ.')
        range_note.setWordWrap(True)
        range_layout.addWidget(range_note)
        multiplier_form = QtWidgets.QFormLayout()
        multiplier_form.setContentsMargins(0, 0, 0, 0)
        multiplier_form.addRow('Range = ± scale ×', self.multiplier)
        range_layout.addLayout(multiplier_form)
        internal_form.addWidget(self.range_control)
        self.cpu_control = QtWidgets.QWidget()
        cpu_form = QtWidgets.QFormLayout(self.cpu_control)
        cpu_form.setContentsMargins(0, 0, 0, 0)
        cpu_form.setVerticalSpacing(3)
        self.cpus = QtWidgets.QSpinBox()
        self.cpus.setRange(1, available_cpus())
        self.cpus.setValue(min(4, self.cpus.maximum()))
        self.cpus.setToolTip('Number of CPU processes used to score fits. 1 runs sequentially.')
        cpu_form.addRow('CPUs to use', self.cpus)
        self.target_accepted = QtWidgets.QSpinBox()
        self.target_accepted.setRange(1, DEFAULT_MAX_PROPOSALS)
        self.target_accepted.setValue(512)
        self.target_accepted.setToolTip('Stop after this many distinct fits are accepted in candidate order. '
                                       'The default safety limit is 65,536 proposals; partial results can be saved.')
        cpu_form.addRow('Target accepted fits', self.target_accepted)
        cpu_form.addRow('History-guided', self.guided_fraction)
        cpu_form.addRow('Broad search', self.broad_fraction)
        self.mixture_note = QtWidgets.QLabel(
            'Proposal mix; accepted shares may differ.'
            if self._history_available else 'No eligible historical fits available. Using broad search only.')
        self.mixture_note.setWordWrap(True)
        cpu_form.addRow(self.mixture_note)
        self.history_source_note = QtWidgets.QLabel(
            'Historical differences and guided samples use GCS fits from the '
            '<a href="https://zenodo.org/records/5515483">Zenodo dataset</a>.')
        self.history_source_note.setWordWrap(True)
        self.history_source_note.setContentsMargins(0, 8, 0, 0)
        self.history_source_note.setOpenExternalLinks(True)
        cpu_form.addRow(self.history_source_note)
        self.search_help = QtWidgets.QLabel(
            '<b>Ranges:</b> Explore ± Scale × multiplier around the manual fit; 5° × 2 means ±10°. '
            'Zero fixes a parameter.<br><br>'
            '<b>Search mix:</b> History-guided uses previous angular differences; broad explores your full ranges. '
            'Height, half-angle and κ remain broad.<br><br>'
            '<b>Running:</b> CPUs score fits in parallel. Target accepted fits sets the desired match count; '
            'cancelling or reaching the search limit can leave fewer.<br><br>'
            '<b>Results:</b> Rose pink previews up to 6 accepted outlines while searching and up to 20 afterward; bright pink '
            'marks your manual fit. Switch to Accepted parameter histograms to watch fractions update. '
            'Save after the search to keep the fit, outlines and all accepted results.')
        self.search_help.setWordWrap(True)
        self.search_help.setContentsMargins(0, 12, 0, 0)
        cpu_form.addRow(self.search_help)
        internal_form.addWidget(self.cpu_control)
        self.run_button = QtWidgets.QPushButton('Run uncertainty search')
        self.run_button.clicked.connect(self.run_or_cancel)
        self.cancel_button = QtWidgets.QPushButton('Cancel search')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        self.export_button = QtWidgets.QPushButton('Export search and accepted fits')
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export)
        internal_form.addWidget(self.run_button)
        internal_form.addWidget(self.cancel_button)
        internal_form.addWidget(self.export_button)
        self.status = SearchStatusLabel('No outline search yet.')
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        self.search_status = SearchStatusLabel('Draw outlines and adjust the manual fit, then run the search.')
        self.search_status.setWordWrap(True)
        internal_form.addWidget(self.search_status)
        self.report = QtWidgets.QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(160)
        internal_form.addWidget(self.report)
        form.addStretch(1)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(box)
        scroll.setMinimumWidth(300)
        layout.addWidget(scroll, stretch=1)
        gui._figure.canvas.mpl_connect('button_press_event', self.click)
        self.edit_controls = [self.trace_button, self.view, undo, clear, save_trace, load_trace,
                              self.sampling, self.broad_fraction, self.guided_fraction,
                              self.multiplier, self.exponent, self.seed, self.resolution,
                              self.mesh_density, self.preview_button, self.cpus, self.target_accepted,
                              *self.scales.values(), *self.tolerances.values()]
        # Changes after a completed run invalidate its exports to prevent stale results.
        for control in self.scales.values():
            control.valueChanged.connect(self.update_scale_sources)
        self.update_scale_sources()
        for control in [*self.scales.values(), *self.tolerances.values(),
                        self.multiplier, self.exponent, self.seed]:
            control.valueChanged.connect(self.invalidate)
        self.broad_fraction.valueChanged.connect(self._set_mixture)
        self.guided_fraction.valueChanged.connect(lambda value: self._set_mixture(100-value))
        self.sampling.currentIndexChanged.connect(self._sync_sampling_mode)
        self._sync_sampling_mode()
        self.broad_fraction.valueChanged.connect(self.invalidate)
        self.guided_fraction.valueChanged.connect(self.invalidate)
        self.sampling.currentIndexChanged.connect(self.invalidate)
        self.resolution.currentIndexChanged.connect(self.invalidate)
        self.mesh_density.currentIndexChanged.connect(self.invalidate)
        self.target_accepted.valueChanged.connect(self.invalidate)
        for slider in self.sliders():
            slider.valueChanged.connect(self.invalidate)
        self.update_edit_mode()

    def sliders(self):
        return [getattr(self.gui, '_s_' + k) for k in PARAMETERS]

    def update_scale_sources(self, *args):
        for key, spin in self.scales.items():
            is_default = spin.value() == self.default_scales[key]
            self.scale_sources[key] = (self.default_scale_sources[key] if is_default else
                                      'Custom or saved search scale; not a measured uncertainty')
            spin.setToolTip(self.scale_sources[key] + '; zero fixes this parameter')

    def _set_mixture(self, broad_percent):
        broad_percent = int(broad_percent) if self._history_available else 100
        blockers = [QtCore.QSignalBlocker(widget) for widget in
                    (self.broad_fraction, self.guided_fraction, self.sampling)]
        self.broad_fraction.setValue(broad_percent)
        self.guided_fraction.setValue(100-broad_percent)
        self.sampling.setCurrentIndex(1 if broad_percent == 100 else 0)
        enabled = self._history_available and not self.is_running()
        self.broad_fraction.setEnabled(enabled)
        self.guided_fraction.setEnabled(enabled)
        del blockers

    def _sync_sampling_mode(self, *args):
        self._set_mixture(100 if self.sampling.currentData() == 'uniform' else self.broad_fraction.value())

    def invalidate(self, *args):
        self.settings_revision += 1
        self.result = None
        self.export_button.setEnabled(False)
        self.stop_overlay_preview()
        for artist in self.overlays:
            artist.remove()
        self.overlays = []
        self._accepted_overlay_artists.clear()
        self._manual_overlay_artists.clear()
        self.report.clear()
        self.stop_live_histograms()
        self.gui.update_parameter_histograms()
        self.search_status.setText('Fit or outlines changed. Run uncertainty search when ready.')
        self.status.setText('Inputs changed. Freeze current fit and sample again.')
        self.gui._figure.canvas.draw_idle()

    def queue_preview(self, preview):
        # Keep one newest snapshot; never queue a backlog of projection jobs.
        self._pending_preview = preview
        if self.gui._figure.canvas.isVisible() and not self._preview_timer.isActive():
            self._preview_timer.start(LIVE_PREVIEW_INTERVAL_MS)

    def _shutdown_preview_projection(self, *args):
        if self._preview_projection is not None:
            self._preview_projection.close()
        self._preview_projection = None
        self._preview_future = None
        self._preview_source = None

    def stop_overlay_preview(self):
        self._preview_timer.stop()
        self._pending_preview = None
        self._final_preview_result = None
        self._shutdown_preview_projection()

    def _prepare_preview(self, source):
        preview = source
        if source is not self._final_preview_result and len(source['overlays']) > MAX_LIVE_OVERLAY_FITS:
            selected = np.linspace(0, len(source['overlays']) - 1, MAX_LIVE_OVERLAY_FITS, dtype=int)
            preview = dict(source)
            for key in ('overlays', 'overlay_keys', 'overlay_parameters'):
                if key in source:
                    preview[key] = [source[key][i] for i in selected]
        views = set(range(len(self.gui._images)))
        missing = any(views.difference(boundaries) for boundaries in
                      [preview['reference_boundaries']] + preview['overlays'])
        if missing and 'overlay_parameters' in preview:
            if self._preview_projection is None:
                self._preview_projection = OutlinePreview(self.gui._images, self.gui._date)
            self._preview_source = source
            self._preview_future = self._preview_projection.submit(preview)
        else:
            self._paint_preview(preview, source)

    def _paint_preview(self, preview, source):
        if self._final_preview_result is not None and source is not self._final_preview_result:
            return
        if source is self._final_preview_result:
            source.update(reference_boundaries=preview['reference_boundaries'], overlays=preview['overlays'])
            self._final_preview_result = None
        self.update_search_overlays(preview)

    def _receive_projected_preview(self):
        future, source = self._preview_future, self._preview_source
        self._preview_future = None
        self._preview_source = None
        preview = future.result()
        if preview is not None:
            self._paint_preview(preview, source)

    def flush_preview(self):
        self._preview_timer.stop()
        if not self.gui._figure.canvas.isVisible():
            return
        try:
            if self._preview_future is not None:
                if not self._preview_future.done():
                    self._preview_timer.start(100)
                    return
                self._receive_projected_preview()
            if self._pending_preview is not None:
                preview, self._pending_preview = self._pending_preview, None
                self._prepare_preview(preview)
            if self._preview_future is not None:
                self._preview_timer.start(100)
        except Exception as error:
            self.stop_overlay_preview()
            self.status.setText('Outline preview unavailable: ' + str(error))

    def _update_overlay_group(self, artists, outlines, style):
        for key in list(artists):
            if key not in outlines:
                artist = artists.pop(key)
                artist.remove()
                self.overlays.remove(artist)
        for key, (view, xy) in outlines.items():
            if key in artists:
                artists[key].set_data(xy[:, 0], xy[:, 1])
            else:
                artist = self.gui._axes[view].plot(xy[:, 0], xy[:, 1], '.',
                    scalex=False, scaley=False, **style)[0]
                artists[key] = artist
                self.overlays.append(artist)

    def update_search_overlays(self, preview):
        accepted = {(slot, view): (view, xy)
                    for slot, boundaries in enumerate(preview['overlays'])
                    for view, xy in boundaries.items()}
        reference = {view: (view, xy) for view, xy in preview['reference_boundaries'].items()}
        self._update_overlay_group(self._accepted_overlay_artists, accepted, ACCEPTED_OUTLINE_STYLE)
        self._update_overlay_group(self._manual_overlay_artists, reference, MANUAL_OUTLINE_STYLE)
        if self.gui._figure.canvas.isVisible():
            self.gui._figure.canvas.draw_idle()

    def finish_overlay_preview(self, result):
        self._preview_timer.stop()
        self._final_preview_result = result
        self._pending_preview = result
        if self.gui._figure.canvas.isVisible():
            self.flush_preview()

    def complete_overlay_preview(self):
        """Finish the final display when saving; scoring never waits for it."""
        if self._final_preview_result is None:
            return
        self._preview_timer.stop()
        while self._preview_future is not None or self._pending_preview is not None:
            if self._preview_future is not None:
                self._receive_projected_preview()
            if self._pending_preview is not None:
                preview, self._pending_preview = self._pending_preview, None
                self._prepare_preview(preview)

    def begin_live_histograms(self, config):
        self._live_histogram_result = dict(config=dict(reference=config['reference']),
                                           accepted=[], offsets=[], running=True)
        self._histogram_dirty = True
        self.flush_live_histograms()

    def queue_histogram_sample(self, values, offsets):
        if self._live_histogram_result is None:
            return
        self._live_histogram_result['accepted'].append(values)
        self._live_histogram_result['offsets'].append(offsets)
        self._histogram_dirty = True
        if self.gui._histogram_figure.canvas.isVisible() and not self._histogram_timer.isActive():
            self._histogram_timer.start()

    def flush_live_histograms(self):
        self._histogram_timer.stop()
        if not self._histogram_dirty or self._live_histogram_result is None or not self.gui._histogram_figure.canvas.isVisible():
            return
        self._histogram_dirty = False
        self.gui.update_parameter_histograms(self._live_histogram_result)

    def stop_live_histograms(self):
        self._histogram_timer.stop()
        self._live_histogram_result = None
        self._histogram_dirty = False

    def stop_editing(self):
        self.trace_button.setChecked(False)
        if hasattr(self, 'assign'):
            self.assign.setChecked(False)

    def navigation_mode_changed(self):
        if self.gui._figure.canvas.toolbar.mode:
            self.stop_editing()
            self.status.setText('Navigation active. Choose Trace outline or segment assignment to resume editing.')
        elif not self.trace_button.isChecked() and not (hasattr(self, 'assign') and self.assign.isChecked()):
            self.status.setText('Viewing only. Choose Trace outline to draw or segment assignment to change tiers.')
        self.update_edit_mode()

    def prepare_editing(self):
        self.gui._figure_tabs.setCurrentIndex(0)
        toolbar = self.gui._figure.canvas.toolbar
        mode = str(toolbar.mode).lower()
        if 'pan' in mode:
            toolbar.pan()
        elif 'zoom' in mode:
            toolbar.zoom()

    def update_edit_mode(self):
        self.trace_button.setText('Stop tracing' if self.trace_button.isChecked() else 'Trace outline')
        assigning = hasattr(self, 'assign') and self.assign.isChecked()
        if hasattr(self, 'assign'):
            self.assign.setText('Stop assigning segments' if assigning else 'Assign segments to selected tier')
        mode = str(self.gui._figure.canvas.toolbar.mode).lower()
        if 'pan' in mode:
            text = 'Mode: PAN — editing off'
        elif 'zoom' in mode:
            text = 'Mode: ZOOM — editing off'
        elif self.trace_button.isChecked():
            text = 'Mode: TRACING — ON'
        elif assigning:
            text = 'Mode: ASSIGNING — ON'
        else:
            text = 'Mode: VIEWING — editing off'
        self.mode_label.setText(text)

    def toggle_trace(self, active):
        if active:
            self.prepare_editing()
            if hasattr(self, 'assign'):
                self.assign.setChecked(False)
            self.status.setText('Click along one visible outline per image. Do not bridge hidden arcs. '
                                'Right-click or Stop tracing ends tracing. Your current zoom is kept.')
        else:
            self.status.setText('Tracing stopped. Choose Trace outline to add more points.')
        self.update_edit_mode()

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

    def run_or_cancel(self):
        if self.is_running():
            self.cancel()
        else:
            self.start()

    def set_search_status(self, text):
        self.status.setText(text)
        self.search_status.setText(text)

    def cancel(self):
        if self.worker is not None:
            self.worker.cancelled.set()
            self.set_search_status(self.search_status.text() + '\nWaiting for fits already running to finish...')
            self.run_button.setText('Cancelling...')
            self.run_button.setEnabled(False)

    def preview(self):
        try:
            traces = {k: resample_trace(v) for k, v in self.outlines.items() if len(v)}
            if not traces:
                raise ValueError('Draw at least three points in one image first.')
            boundaries = project_boundaries(self.gui.get_params_dict(), self.gui._date,
                {k: self.gui._images[k] for k in traces}, self.resolution.currentData(),
                self.mesh_density.currentData())
            self.invalidate()
            lines = ['Reference RMS / allowed RMS [projected RSun]:']
            for k, xy in boundaries.items():
                metric = ProjectedDistance(self.gui._images[k])
                error = outline_error(resample_trace(metric.transform(self.outlines[k])), metric.transform(xy))
                lines.append('{}: {:.5f} / {:.5f}'.format(self.gui._view_labels[k], error,
                                                        error + self.tolerances[k].value()))
            boundaries = display_boundaries(self.gui.get_params_dict(), self.gui._date,
                dict(enumerate(self.gui._images)), self.resolution.currentData(),
                self.mesh_density.currentData(), existing=boundaries)
            for k, xy in boundaries.items():
                self.overlays.append(self.gui._axes[k].plot(xy[:, 0], xy[:, 1], '.',
                    scalex=False, scaley=False, **MANUAL_OUTLINE_STYLE)[0])
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
            config = dict(score_unit=PHYSICAL_UNIT, reference=self.gui.get_params_dict(), outlines=outlines,
                scales={k: v.value() for k, v in self.scales.items()},
                scale_sources=self.scale_sources, multiplier=self.multiplier.value(),
                exponent=self.exponent.value(), seed=self.seed.value(),
                cpus=self.cpus.value(),
                target_accepted=self.target_accepted.value(),
                max_proposals=max(DEFAULT_MAX_PROPOSALS, 2 ** self.exponent.value()),
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
                score='Per-view observed-to-model exterior-boundary RMS in projected solar radii; 128 physical arclength trace points.',
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
        self.worker = SearchWorker(config, images, self.gui._date, self.gui,
                                   display_images=dict(enumerate(self.gui._images)))
        self.begin_live_histograms(config)
        self.worker.progress.connect(self.set_search_status)
        self.worker.preview_ready.connect(self.queue_preview)
        self.worker.accepted_fit.connect(self.queue_histogram_sample)
        self.worker.completed.connect(self.finished)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(lambda: self.set_running(False))
        self.set_search_status('Fits tried: 0 | Accepted: 0 / {}\nPreparing the manual reference and search...'
                               .format(self.target_accepted.value()))
        self.worker.start()

    def set_running(self, active):
        for control in [*self.edit_controls, *self.sliders()]:
            control.setEnabled(not active)
        self.run_button.setText('Cancel search' if active else 'Run uncertainty search')
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(active)
        self.broad_fraction.setEnabled(not active and self._history_available)
        self.guided_fraction.setEnabled(not active and self._history_available)
        if hasattr(self.gui, 'update_navigation'):
            self.gui.update_navigation(active)

    def failed(self, message):
        self.stop_overlay_preview()
        self.stop_live_histograms()
        self.gui.update_parameter_histograms()
        self.set_search_status('Search failed: ' + message.strip().splitlines()[-1])
        self.report.setPlainText(message)

    def finished(self, result):
        self.result = result
        self.stop_live_histograms()
        self.export_button.setEnabled(True)
        self.finish_overlay_preview(result)
        count = len(result['accepted'])
        self.set_search_status('{}Accepted {} of {} scored; {} invalid geometries, {} unusable projections. '
            'Cyan: your trace. Bright pink: manual fit. Rose pink: accepted examples.'
            .format('CANCELLED — partial search. ' if result['cancelled'] else '', count,
                    result['tested'], result['invalid_geometry'], result['projection_failures']))
        lines = ['DESIGN-DEPENDENT RANGES; NOT CONFIDENCE INTERVALS.', 'Reference RMS / allowed RMS [RSun]:']
        sampling = result.get('sampling_info', {})
        if 'eligible_events' in sampling:
            lines.insert(1, 'History: {} eligible events; target date held out. {} guided proposals; '
                         '{} outside-box attempts; {} broad fallbacks.'.format(
                             sampling['eligible_events'], sampling['historical_rows_proposed'],
                             sampling['outside_box_attempts'], sampling['broad_fallback_rows']))
            lines.append('History guides exploration. Accepted frequencies depend on the proposal mixture.')
        for k, e in result['reference_scores'].items():
            lines.append('{}: {:.5f} / {:.5f}'.format(self.gui._view_labels[k], e,
                         e + result['config']['tolerances'][str(k)]))
        lines.append('Parameter: manual | median | accepted min..max')
        for k, s in result['summary'].items():
            lines.append('{}: {:.3f} | {:.3f} | {:.3f}..{:.3f}'.format(
                k, result['config']['reference'][k], s['median'], s['min'], s['max']))
        if result['search_edges']:
            lines.append('Accepted fits near search limits: ' + ', '.join(result['search_edges']) +
                         '. Widen these ranges and rerun; angular ranges cannot repeat orientations.')
        target = result['config'].get('target_accepted') or 512
        if count < target:
            lines.append('Fewer accepted fits than requested; the saved ensemble will be partial.')
        lines.append('Check stability at higher raster and mesh resolution before scientific use.')
        self.report.setPlainText('\n'.join(lines))
        self.set_search_status(completion_status(result))
        self.gui.update_parameter_histograms(result)
        self.gui._figure.canvas.draw_idle()

    def export(self):
        if self.result is None:
            return
        root = QtWidgets.QFileDialog.getExistingDirectory(self.gui, 'Choose parent folder for search export')
        if not root:
            return
        try:
            destination = export_result(self.result, root)
            self.gui.save_search_figures(destination)
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
    selected = ensemble_selection(result)
    target = result['config'].get('target_accepted')
    selection_file = 'selected_fits.csv' if target is not None else 'selected_up_to_512.csv'
    metadata['ensemble_requested'] = target if target is not None else 512
    metadata['ensemble_count'] = len(selected)
    metadata['ensemble_file'] = selection_file
    if result.get('stopping_rule') == 'first-accepted-in-candidate-order':
        metadata['summary_population'] = 'Retained accepted fits in candidate order; later in-flight matches are excluded.'
        metadata['selection_method'] = 'First requested number of accepted candidates in proposal order; NOT a balanced Sobol design or calibrated posterior.'
    else:
        metadata['summary_population'] = 'All distinct accepted fits, including any excess from the final batch.'
        metadata['selection_method'] = 'Seeded uniform subset of accepted rows; NOT a Sobol design or calibrated posterior.'
    metadata['software'] = dict(numpy=np.__version__)
    metadata['units'] = dict(lat='degrees', lon='degrees modulo 360',
                             tilt='geometric degrees modulo 180', height='solar radii',
                             half_angle='degrees', kappa='dimensionless', errors=result.get('distance_unit', score_unit(result['config'])))
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
    prefix = 'rms_rsun_' if metadata['units']['errors'] == PHYSICAL_UNIT else 'rms_px_'
    score_names = [prefix + c['key'] if c['tier'] is not None else prefix + 'view_' + str(c['view'])
                   for c in components] if components else [prefix + 'view_' + str(k) for k in result['view_indices']]
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
    write_rows(destination / selection_file, selected)
    np.savez_compressed(destination / 'accepted_arrays.npz', parameters=result['accepted'],
                        offsets=result['offsets'], errors=result['errors'], selected=selected,
                        error_unit=metadata['units']['errors'])
    return destination
