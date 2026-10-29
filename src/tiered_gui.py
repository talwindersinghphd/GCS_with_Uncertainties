"""Two-tier annotation editor, using a separate manual baseline per view/tier."""
import json
from pathlib import Path
import numpy as np
from .distance_metric import PHYSICAL_UNIT, ProjectedDistance
from PyQt5 import QtCore, QtWidgets

from .uncertainty_gui import (UncertaintyPanel, SearchWorker, candidate_design,
    project_boundaries, display_boundaries, PARAMETERS, STRAIGHT, FRONT, CIRCLE, DEFAULT_HISTORY,
    DEFAULT_MAX_PROPOSALS, completion_status, MANUAL_OUTLINE_STYLE)
from .tiered_outlines import TIERS, COLORS, prepare_checks, check_error, move_segment
from .utils.widgets import CompactDoubleSpinBox


class TieredPanel(UncertaintyPanel):
    def __init__(self, gui, layout):
        self.annotation_migration = None
        self.tier_outlines = {}
        self.selected_arcs = {}
        super().__init__(gui, layout)
        self.tier = QtWidgets.QComboBox()
        self.tier.addItem('Tier 1 — clear boundary (cyan)', '1')
        self.tier.addItem('Tier 2 — less certain (yellow)', '2')
        self.arc = QtWidgets.QComboBox()
        position = self._form.indexOf(self.view) + 1
        self._form.insertWidget(position, self.tier)
        self._form.insertWidget(position + 1, self.arc)
        row = QtWidgets.QHBoxLayout()
        new = QtWidgets.QPushButton('New arc')
        delete = QtWidgets.QPushButton('Delete arc')
        new.clicked.connect(self.new_arc)
        delete.clicked.connect(self.delete_arc)
        self.undo_button.setToolTip('Remove the last point from the selected arc. Click again to undo more points. '
                                    'Tracing stays active so you can place a replacement point.')
        for button in (new, self.undo_button, delete):
            row.addWidget(button)
        self._form.insertLayout(position + 2, row)
        self.undo_button.show()
        self.assign = QtWidgets.QPushButton('Assign segments to selected tier')
        self.assign.setCheckable(True)
        self.assign.setToolTip('Choose the target tier, enable this mode, then click the line between two outline points '
                               'in the other tier. Right-click to finish. Pan/zoom switches off automatically.')
        self._form.insertWidget(position + 3, self.assign)
        self.assign.toggled.connect(self.toggle_assign)
        self.outline_help = QtWidgets.QLabel(
            '<b>Tiers:</b> Cyan (Tier 1) marks clear boundaries. Yellow (Tier 2) marks less certain sections '
            'and allows more mismatch in the search.<br><br>'
            '<b>Arcs:</b> Choose a view and tier before tracing. Use New arc across gaps. '
            'Select an arc to continue drawing. Undo point removes its last point; click repeatedly to step back. '
            'Delete arc removes the whole selected arc.<br><br>'
            '<b>Reassigning:</b> Choose the destination tier and enable assignment. Click the line between two '
            'neighboring points in the other tier. Only that segment changes tier; its position stays fixed. '
            'Example: choose Tier 2, then click a cyan segment to make it yellow. Right-click finishes.<br><br>'
            'Trace outline and assignment switch pan/zoom off automatically and keep your zoom level. '
            'The mode label shows whether editing is on. Right-click or the Stop button ends editing. '
            'Repeat for each view, then adjust Manual fit and open Uncertainty.')
        self.outline_help.setWordWrap(True)
        self.outline_help.setContentsMargins(0, 8, 0, 0)
        self._form.insertWidget(self._form.indexOf(self.status) + 1, self.outline_help)
        self.tier.currentIndexChanged.connect(self.refresh_arcs)
        self.view.currentIndexChanged.connect(self.refresh_arcs)
        self.arc.currentIndexChanged.connect(self.select_arc)
        self.tier2_tolerances = {}
        for i, sc in enumerate(gui._view_labels):
            spin = CompactDoubleSpinBox()
            spin.setRange(0, 100)
            spin.setDecimals(10)
            spin.setSingleStep(.01)
            spin.setValue(.20)
            spin.setToolTip('Additional Tier 2 RMS allowed ABOVE YOUR MANUAL TIER 2 RMS, in projected solar radii. '
                            '0.20 is an editable starting allowance, not a measured uncertainty.')
            self.tier2_tolerances[i] = spin
            spin.valueChanged.connect(self.invalidate)
        self.allowance_control = QtWidgets.QWidget()
        allowance_layout = QtWidgets.QVBoxLayout(self.allowance_control)
        allowance_layout.setContentsMargins(0, 0, 0, 0)
        allowance_layout.setSpacing(3)
        heading = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel('Extra RMS allowance [RSun]')
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        heading.addWidget(title)
        self.allowance_defaults = QtWidgets.QPushButton('Defaults')
        self.allowance_defaults.setToolTip('Set every view to +0.05 solar radii for Tier 1 and +0.20 for Tier 2.')
        self.allowance_defaults.clicked.connect(self.reset_allowances)
        heading.addWidget(self.allowance_defaults)
        allowance_layout.addLayout(heading)
        allowances = QtWidgets.QGridLayout()
        allowances.setContentsMargins(0, 0, 0, 0)
        allowances.setHorizontalSpacing(3)
        allowances.setVerticalSpacing(2)
        allowances.setAlignment(QtCore.Qt.AlignLeft)
        for column, text in enumerate(('', 'Tier 1', 'Tier 2')):
            allowances.addWidget(QtWidgets.QLabel(text), 0, column)
        for row, sc in enumerate(gui._view_labels, 1):
            allowances.addWidget(QtWidgets.QLabel(sc), row, 0)
            for column, spin in enumerate((self.tolerances[row - 1], self.tier2_tolerances[row - 1]), 1):
                spin.setFixedWidth(spin.fontMetrics().horizontalAdvance('0.0000') + 27)
                allowances.addWidget(spin, row, column)
                spin.show()
        allowance_layout.addLayout(allowances)
        self.allowance_help = QtWidgets.QLabel(
            '<b>Candidate RMS ≤ manual RMS + allowance.</b><br><br>'
            '<b>Extra RMS allowance:</b> How much more outline mismatch to allow above your manual fit, '
            'in projected solar radii (RSun). Larger values are more permissive; smaller values are '
            'stricter. Each traced view and tier must pass separately.<br><br>'
            'Defaults: Tier 1 = 0.05 RSun; Tier 2 = 0.20 RSun. These are adjustable search tolerances.')
        self.allowance_help.setWordWrap(True)
        policy = self.allowance_help.sizePolicy()
        policy.setHorizontalPolicy(QtWidgets.QSizePolicy.Ignored)
        self.allowance_help.setSizePolicy(policy)
        allowance_layout.addWidget(self.allowance_help)
        for button in self._form.parentWidget().findChildren(QtWidgets.QPushButton):
            if button.text() == 'Clear view':
                button.setText('Clear tier')
        self.edit_controls += [self.tier, self.arc, new, delete, self.assign, self.allowance_defaults,
                               *self.tier2_tolerances.values()]
        self.status.setText('Tier 1: cyan; Tier 2: yellow. Use separate arcs across gaps.')
        self.refresh_arcs()

    def reset_allowances(self):
        for spin in self.tolerances.values():
            spin.setValue(.05)
        for spin in self.tier2_tolerances.values():
            spin.setValue(.20)

    def invalidate(self, *args):
        super().invalidate(*args)
        self.status.setText('Fit or outlines updated. Choose a tier to continue tracing or assigning segments.')

    def import_legacy(self):
        # The original demos/tests can still supply one flat polyline per image.
        if self.outlines:
            for view, points in self.outlines.items():
                self.tier_outlines.setdefault(int(view), {})['1'] = [points]
            self.outlines = {}

    def selection(self):
        return int(self.view.currentData()), self.tier.currentData()

    def arcs_for(self, view, tier):
        return self.tier_outlines.setdefault(view, {}).setdefault(tier, [])

    def refresh_arcs(self, *args):
        self.import_legacy()
        view, tier = self.selection()
        arcs = self.arcs_for(view, tier)
        selected = min(self.selected_arcs.get((view, tier), 0), max(0, len(arcs)-1))
        blocker = QtCore.QSignalBlocker(self.arc)
        self.arc.clear()
        for i, points in enumerate(arcs):
            self.arc.addItem('Arc {} ({} points)'.format(i+1, len(points)), i)
        if arcs:
            self.arc.setCurrentIndex(selected)
        self.selected_arcs[(view, tier)] = selected
        del blocker

    def select_arc(self, *args):
        if self.arc.currentData() is not None:
            self.selected_arcs[self.selection()] = int(self.arc.currentData())

    def new_arc(self):
        self.import_legacy()
        view, tier = self.selection()
        arcs = self.arcs_for(view, tier)
        arcs.append([])
        self.selected_arcs[(view, tier)] = len(arcs)-1
        self.refresh_arcs()
        self.invalidate()
        self.trace_button.setChecked(True)

    def delete_arc(self):
        view, tier = self.selection()
        arcs = self.arcs_for(view, tier)
        if arcs:
            arcs.pop(self.selected_arcs.get((view, tier), 0))
        self.refresh_arcs()
        self.invalidate()
        self.draw_trace(view)

    def toggle_trace(self, active):
        super().toggle_trace(active)
        if active:
            self.status.setText('Draw in the selected tier. Left-click adds points; right-click ends tracing. '
                                'Use New arc across gaps. Your current zoom is kept.')

    def toggle_assign(self, active):
        if active:
            self.prepare_editing()
            self.trace_button.setChecked(False)
            self.status.setText('Select the target tier, then click the middle of a cyan/yellow segment. '
                                'The segment changes tier without changing its position. Right-click or Stop assigning segments finishes.')
        else:
            self.status.setText('Assignment stopped. Choose Trace outline to draw or enable assignment to change tiers.')
        self.update_edit_mode()

    def click(self, event):
        if self.is_running():
            return
        if event.button == 3:
            self.stop_editing()
            return
        if event.button != 1 or event.inaxes not in self.gui._axes:
            return
        if getattr(self.gui._figure.canvas.toolbar, 'mode', ''):
            return
        self.import_legacy()
        view = self.gui._axes.index(event.inaxes)
        self.view.setCurrentIndex(view)
        if self.assign.isChecked():
            self.assign_segment(view, event)
            return
        if not self.trace_button.isChecked():
            return
        x, y = event.xdata, event.ydata
        h, w = self.gui._images[view].data.shape
        margin = 3 * max(h, w) / self.resolution.currentData()
        if x is None or y is None or not (margin < x < w-1-margin and margin < y < h-1-margin):
            self.status.setText('Trace inside the image, away from its cropped edge.')
            return
        tier = self.tier.currentData()
        arcs = self.arcs_for(view, tier)
        if not arcs:
            arcs.append([])
        index = self.selected_arcs.get((view, tier), 0)
        arcs[index].append([float(x), float(y)])
        self.refresh_arcs()
        self.invalidate()
        self.draw_trace(view)

    def assign_segment(self, view, event):
        target = self.tier.currentData()
        closest = None
        for tier, arcs in self.tier_outlines.get(view, {}).items():
            if tier == target:
                continue
            for ai, arc in enumerate(arcs):
                if len(arc) < 2:
                    continue
                xy = self.gui._axes[view].transData.transform(np.asarray(arc))
                for si, (a, b) in enumerate(zip(xy[:-1], xy[1:])):
                    d = b-a
                    fraction = np.clip(np.dot(np.array([event.x,event.y])-a, d) / max(np.dot(d,d),1e-12), 0, 1)
                    distance = np.linalg.norm(np.array([event.x,event.y]) - (a+fraction*d))
                    if closest is None or distance < closest[0]:
                        closest = (distance, tier, ai, si)
        if closest is None or closest[0] > 12:
            self.status.setText('Click nearer the line between two points in the other tier.')
            return
        _, source, ai, si = closest
        move_segment(self.tier_outlines[view], source, ai, si, target)
        self.selected_arcs[(view,target)] = len(self.tier_outlines[view][target])-1
        self.refresh_arcs()
        self.invalidate()
        self.draw_trace(view)
        self.status.setText('Segment assigned to Tier {}. Continue clicking other segments or right-click to finish.'.format(target))

    def draw_trace(self, view):
        self.import_legacy()
        for key in list(self.lines):
            if (key[0] if isinstance(key, tuple) else key) == view:
                self.lines.pop(key).remove()
        for tier, arcs in self.tier_outlines.get(view, {}).items():
            for i, points in enumerate(arcs):
                if points:
                    xy = np.asarray(points)
                    self.lines[(view,tier,i)] = self.gui._axes[view].plot(xy[:,0],xy[:,1], 'o-',
                        color=COLORS[tier], lw=1.3, ms=3, scalex=False, scaley=False)[0]
        self.gui._figure.canvas.draw_idle()

    def edit_trace(self, clear):
        self.import_legacy()
        view, tier = self.selection()
        arcs = self.arcs_for(view, tier)
        if clear:
            arcs.clear()
        elif arcs:
            points = arcs[self.selected_arcs.get((view,tier),0)]
            if points:
                points.pop()
        self.refresh_arcs()
        self.invalidate()
        self.draw_trace(view)

    def annotation_payload(self):
        self.import_legacy()
        return dict(format='gcs-outlines-v3', score_unit=PHYSICAL_UNIT,
            outline_coordinate_unit='native_image_pixels',
            distance_geometry={str(i): ProjectedDistance(image).provenance() for i,image in enumerate(self.gui._images)},
            migration=self.annotation_migration, images=self.image_identity(),
            observations=self.gui._observations,
            views=[dict(spacecraft=sc, coronagraph=detector) for sc, detector in
                   zip(self.gui._spacecraft, self.gui._view_detectors)],
            reference=self.gui.get_params_dict(), tier_outlines=self.tier_outlines,
            image_display=self.gui.image_display_settings(),
            tier_tolerances={str(i): {'1': s.value(), '2': self.tier2_tolerances[i].value()}
                             for i, s in self.tolerances.items()},
            search_settings=dict(scales={k:s.value() for k,s in self.scales.items()},
                multiplier=self.multiplier.value(), exponent=self.exponent.value(), seed=self.seed.value(),
                cpus=self.cpus.value(),
                target_accepted=self.target_accepted.value(),
                resolution=self.resolution.currentData(), mesh_density=self.mesh_density.currentData(),
                sampling=self.sampling.currentData(), broad_fraction=self.broad_fraction.value()),
            rule='Each view/tier RMS <= the frozen manual view/tier RMS + its additional allowance. No absolute cap.')

    def save_traces(self):
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(self.gui, 'Save tiered traces and manual fit',
                                                         'gcs_tiered_traces.json', 'JSON (*.json)')
        if filename:
            try:
                Path(filename).write_text(json.dumps(self.annotation_payload(),indent=2,allow_nan=False))
                self.status.setText('Saved both tiers and the manual reference.')
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self.gui,'Cannot save traces',str(exc))

    def restore_annotations(self, payload):
        if payload.get('format') not in ('gcs-outlines-v1','gcs-outlines-v2','gcs-outlines-v3') or payload.get('images') != self.image_identity():
            raise ValueError('Traces do not match these exact images and metadata, or the format is unsupported.')
        reference = payload['reference']
        if set(reference) != set(PARAMETERS):
            raise ValueError('Invalid reference parameter names.')
        for key, value in reference.items():
            control = getattr(self.gui, '_s_'+key).numbox
            if not np.isfinite(value) or not control.minimum() <= value <= control.maximum():
                raise ValueError('Reference parameter is outside the manual slider limits.')
        legacy = payload['format'] == 'gcs-outlines-v1'
        tiers = {int(k): {'1':[v]} for k,v in payload['outlines'].items() if len(v)} if legacy else {
            int(k): {str(t): arcs for t,arcs in v.items()} for k,v in payload['tier_outlines'].items()}
        for view, groups in tiers.items():
            if not 0 <= view < len(self.gui._images) or any(t not in TIERS for t in groups):
                raise ValueError('Invalid view or tier in annotation file.')
            h,w = self.gui._images[view].data.shape
            for arcs in groups.values():
                for arc in arcs:
                    if not len(arc):
                        continue
                    xy = np.asarray(arc,dtype=float)
                    if xy.ndim != 2 or xy.shape[1] != 2 or not np.isfinite(xy).all() or (xy < 0).any() or (xy[:,0] >= w).any() or (xy[:,1] >= h).any():
                        raise ValueError('Invalid or out-of-image annotation coordinates.')
        physical = payload['format'] == 'gcs-outlines-v3'
        if physical and (payload.get('score_unit') != PHYSICAL_UNIT or
                         payload.get('outline_coordinate_unit') != 'native_image_pixels'):
            raise ValueError('Unsupported saved outline coordinate or distance units.')
        settings = {str(v):dict(a) for v,a in payload.get('tier_tolerances',{}).items()}
        # Validate legacy values before conversion and before mutating GUI state.
        for view, allowances in settings.items():
            if int(view) not in self.tolerances or any(t not in TIERS or not np.isfinite(v) or
                    not 0 <= v <= (100 if physical else 1000) for t,v in allowances.items()):
                raise ValueError('Invalid saved tier allowance.')
        migration = payload.get('migration') if physical else None
        if not physical and settings:
            factors = {view: ProjectedDistance(self.gui._images[int(view)]).legacy_pixel_scale()
                       for view in settings}
            settings = {view:{tier:value*factors[view] for tier,value in allowances.items()}
                        for view,allowances in settings.items()}
            migration = dict(source_format=payload['format'], source_unit='native_image_pixels',
                             rsun_per_pixel=factors, original_tier_tolerances=payload['tier_tolerances'],
                             note='Exact similarity WCS conversion; coordinates and original files retained.')

        for view, allowance in settings.items():
            if int(view) not in self.tolerances or any(t not in TIERS or not np.isfinite(v) or not 0 <= v <= 100 for t,v in allowance.items()):
                raise ValueError('Invalid tier allowance.')
        search = payload.get('search_settings',{})
        if 'cpus' in search and (not isinstance(search['cpus'], int) or search['cpus'] < 1):
            raise ValueError('Saved CPU count must be a positive integer.')
        numeric_controls = dict(multiplier=self.multiplier, exponent=self.exponent,
                                seed=self.seed, broad_fraction=self.broad_fraction,
                                target_accepted=self.target_accepted)
        if 'target_accepted' in search and type(search['target_accepted']) is not int:
            raise ValueError('Saved accepted-fit target must be a positive integer.')
        combo_controls = dict(resolution=self.resolution, mesh_density=self.mesh_density,sampling=self.sampling)
        for key,control in numeric_controls.items():
            if key in search and (not np.isfinite(search[key]) or not control.minimum() <= search[key] <= control.maximum()):
                raise ValueError('Saved search setting outside limits: '+key)
        for key,control in combo_controls.items():
            if key in search and control.findData(search[key]) < 0:
                raise ValueError('Unknown saved search setting: '+key)
        for key,value in search.get('scales',{}).items():
            if key not in self.scales or not np.isfinite(value) or not self.scales[key].minimum() <= value <= self.scales[key].maximum():
                raise ValueError('Invalid saved search scale.')
        self.gui.restore_image_display(payload.get('image_display'))
        self.outlines = {}
        for artist in self.lines.values():
            artist.remove()
        self.lines = {}
        self.annotation_migration = migration
        self.tier_outlines = tiers
        self.selected_arcs = {}
        for key,value in reference.items():
            getattr(self.gui,'_s_'+key).numbox.setValue(value)
        for view, allowance in settings.items():
            if '1' in allowance:
                self.tolerances[int(view)].setValue(allowance['1'])
            if '2' in allowance:
                self.tier2_tolerances[int(view)].setValue(allowance['2'])
        for key,control in numeric_controls.items():
            if key in search:
                control.setValue(search[key])
        for key,control in combo_controls.items():
            if key in search:
                control.setCurrentIndex(control.findData(search[key]))
        for key,value in search.get('scales',{}).items():
            self.scales[key].setValue(value)
        if 'cpus' in search:
            self.cpus.setValue(min(search['cpus'], self.cpus.maximum()))
        for view in tiers:
            self.draw_trace(view)
        self.refresh_arcs()
        self.invalidate()
        self.status.setText('Previous outlines loaded into Tier 1. Assign less-certain segments to Tier 2.'
                            if legacy else 'Restored the manual fit and both outline tiers.')
        return legacy

    def load_traces(self):
        filename,_ = QtWidgets.QFileDialog.getOpenFileName(self.gui,'Load traces and fit','','JSON (*.json)')
        if filename:
            try:
                self.restore_annotations(json.loads(Path(filename).read_text()))
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self.gui,'Cannot load traces',str(exc))

    def make_config(self):
        payload = self.annotation_payload()
        return dict(score_unit=PHYSICAL_UNIT, reference=payload['reference'], tier_outlines=payload['tier_outlines'],
            annotation_migration=payload['migration'],
            tier_tolerances=payload['tier_tolerances'],
            scales={k:s.value() for k,s in self.scales.items()}, scale_sources=self.scale_sources,
            multiplier=self.multiplier.value(), exponent=self.exponent.value(), seed=self.seed.value(),
            cpus=self.cpus.value(),
            target_accepted=self.target_accepted.value(),
            max_proposals=max(DEFAULT_MAX_PROPOSALS, 2 ** self.exponent.value()),
            resolution=self.resolution.currentData(), mesh_density=self.mesh_density.currentData(),
            date=self.gui._date.isoformat(), sampling=self.sampling.currentData(),
            broad_fraction=self.broad_fraction.value()/100., history_path=str(DEFAULT_HISTORY),
            image_identity=payload['images'], views=payload['views'],
            observations=payload['observations'],
            running_difference=bool(self.gui._runndiff),
            geometry_mesh=dict(straight=STRAIGHT*self.mesh_density.currentData(),
                               front=FRONT*self.mesh_density.currentData(), circle=CIRCLE*self.mesh_density.currentData()),
            acceptance_rule=payload['rule'],
            score='Arclength-weighted RMS observed-to-model distance in projected solar radii, pooled over separate arcs within each view/tier.',
            interpretation='Proposal-dependent compatibility ranges, not calibrated confidence intervals.')

    def describe_scores(self, baseline, checks):
        lines = ['Manual RMS / allowed RMS [projected RSun]:']
        for c in checks:
            error = baseline[c['key']]
            lines.append('{} Tier {}: {:.5f} / {:.5f} (+{:.5f})'.format(
                self.gui._view_labels[c['view']],c['tier'],error,error+c['allowance'],c['allowance']))
        return lines

    def preview(self):
        try:
            config = self.make_config()
            checks = prepare_checks(config, dict(enumerate(self.gui._images)))
            images = {c['view']:self.gui._images[c['view']] for c in checks}
            b = project_boundaries(config['reference'],self.gui._date,images,config['resolution'],config['mesh_density'])
            baseline = {c['key']:check_error(c,b[c['view']]) for c in checks}
            b = display_boundaries(config['reference'], self.gui._date, dict(enumerate(self.gui._images)),
                config['resolution'], config['mesh_density'], existing=b)
            self.invalidate()
            for view,xy in b.items():
                self.overlays.append(self.gui._axes[view].plot(xy[:,0],xy[:,1],'.',
                    scalex=False,scaley=False,**MANUAL_OUTLINE_STYLE)[0])
            self.report.setPlainText('\n'.join(self.describe_scores(baseline,checks)))
            self.status.setText('Each tier uses its own manual baseline plus its own extra allowance. Both must pass separately.')
            self.gui._figure.canvas.draw_idle()
        except (ValueError,FloatingPointError) as exc:
            QtWidgets.QMessageBox.warning(self.gui,'Cannot score tiers',str(exc))

    def start(self):
        if self.is_running():
            return
        try:
            config = self.make_config()
            checks = prepare_checks(config, dict(enumerate(self.gui._images)))
            p = config['reference']
            if not (abs(p['lat']) <= 90 and p['height'] > 0 and 0 < p['half_angle'] < 90 and 0 < p['kappa'] < 1):
                raise ValueError('Manual fit needs height >0, 0<half_angle<90, 0<kappa<1.')
            candidate_design(config)
        except (ValueError,OSError) as exc:
            QtWidgets.QMessageBox.warning(self.gui,'Cannot start tiered search',str(exc))
            return
        self.trace_button.setChecked(False)
        self.assign.setChecked(False)
        self.invalidate()
        self.set_running(True)
        images = {c['view']:self.gui._images[c['view']] for c in checks}
        self.worker = SearchWorker(config,images,self.gui._date,self.gui,
                                   display_images=dict(enumerate(self.gui._images)))
        self.begin_live_histograms(config)
        self.worker.progress.connect(self.set_search_status)
        self.worker.preview_ready.connect(self.queue_preview)
        self.worker.accepted_fit.connect(self.queue_histogram_sample)
        self.worker.completed.connect(self.finished)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(lambda:self.set_running(False))
        self.set_search_status('Fits tried: 0 | Accepted: 0 / {}\nPreparing the manual reference and search...'
                               .format(self.target_accepted.value()))
        self.worker.start()

    def finished(self,result):
        self.result = result
        self.stop_live_histograms()
        self.export_button.setEnabled(True)
        self.finish_overlay_preview(result)
        lines = self.describe_scores(result['reference_scores'],result['score_components'])
        lines.append('Each view/tier must pass; good Tier 2 agreement cannot offset a Tier 1 failure.')
        lines.append('Parameter: manual | median | accepted min..max')
        for key,stats in result['summary'].items():
            lines.append('{}: {:.3f} | {:.3f} | {:.3f}..{:.3f}'.format(key,result['config']['reference'][key],
                stats['median'],stats['min'],stats['max']))
        if result['search_edges']:
            lines.append('Accepted fits near search bounds: '+', '.join(result['search_edges']))
        lines.append('Compatibility summaries only; not calibrated confidence intervals.')
        self.report.setPlainText('\n'.join(lines))
        self.set_search_status(completion_status(result) + ' Rose pink: accepted fits; bright pink: manual fit.')
        self.gui.update_parameter_histograms(result)
        self.gui._figure.canvas.draw_idle()
