"""Time navigation around the full GCS outline and uncertainty workflow."""
import json
import sys

from PyQt5 import QtCore, QtWidgets

from .event_config import DEFAULT_PARAMETERS, utc_string
from .example import bundled_annotations, matches_bundled
from .gui import APP_NAME, GCSGui
from .images import ImageLoader
from .session import persist, restore_search


class FrameWindow(GCSGui):
    def __init__(self, controller, index, images, observations, warm=None):
        self.controller, self.index = controller, index
        event = controller.event
        self.frame = event.frames[index]
        self.session_folder = event.output_directory / 'frames' / self.frame.key
        self.observations = observations
        self.saved_search = None
        self.switching = False
        self.loading = False
        saved = self.session_folder / 'manual_traces.json'
        payload = json.loads(saved.read_text()) if saved.exists() else None
        parameters = dict(event.initial_parameters or DEFAULT_PARAMETERS)
        if matches_bundled(self.frame) and event.initial_parameters is None:
            parameters = bundled_annotations()['reference']
        if warm:
            parameters = warm['reference']
        manual = self.session_folder / 'manual_fit.json'
        if manual.exists():
            parameters = json.loads(manual.read_text())['parameters']
        if payload:
            parameters = payload['reference']
        super().__init__(self.frame.time, [view.spacecraft for view in self.frame.views],
            runndiff=self.frame.background.mode != 'raw',
            view_detectors=[view.coronagraph for view in self.frame.views],
            images=images, initial_parameters=parameters, observations=observations)
        self.setWindowTitle('{} — {} — {} UTC'.format(APP_NAME, event.name, self.frame.time.isoformat(' ')))
        self.add_time_controls()
        panel = self._uncertainty
        # Import the original flat July session once, without overwriting it.
        if payload is None and matches_bundled(self.frame) and event.initial_parameters is None:
            legacy = event.output_directory / 'manual_traces.json'
            candidates = ([json.loads(legacy.read_text())] if legacy.exists() else []) + [bundled_annotations()]
            payload = next((item for item in candidates if item.get('images') == panel.image_identity()), None)
        if payload is not None:
            panel.restore_annotations(payload)
        elif warm:
            # Carry the fit and controls forward, never native-pixel trace coordinates.
            seed = panel.annotation_payload()
            seed['search_settings'] = warm['search_settings']
            old_allowances = {(view['spacecraft'], view['coronagraph']): warm['tier_tolerances'][str(i)]
                              for i, view in enumerate(warm['views'])}
            for i, view in enumerate(seed['views']):
                key = view['spacecraft'], view['coronagraph']
                if key in old_allowances:
                    seed['tier_tolerances'][str(i)] = old_allowances[key]
            panel.restore_annotations(seed)
        try:
            restore_search(self)
        except (OSError, ValueError, KeyError) as error:
            self.statusBar().showMessage('Fit and outlines restored; saved search could not be displayed: ' + str(error))
        else:
            self.statusBar().showMessage('Save keeps this time open. Previous/Next saves automatically. Draw fresh outlines at each new time.')

    def add_time_controls(self):
        toolbar = QtWidgets.QToolBar('Time series', self)
        toolbar.setObjectName('Time series')
        toolbar.setMovable(False)
        self.addToolBarBreak()
        self.addToolBar(toolbar)
        self.previous = QtWidgets.QPushButton('← Previous')
        self.next = QtWidgets.QPushButton('Next →')
        self.time_selector = QtWidgets.QComboBox()
        self.time_selector.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        for i, frame in enumerate(self.controller.event.frames):
            self.time_selector.addItem('{}/{} · {} UTC · {} views'.format(
                i + 1, len(self.controller.event.frames), frame.time.isoformat(' '), len(frame.views)), i)
        self.time_selector.setCurrentIndex(self.index)
        self.previous.clicked.connect(lambda: self.controller.open_frame(self.index - 1))
        self.next.clicked.connect(lambda: self.controller.open_frame(self.index + 1))
        self.time_selector.activated[int].connect(self.controller.open_frame)
        for widget in (self.previous, self.time_selector, self.next):
            toolbar.addWidget(widget)
            self._uncertainty.edit_controls.append(widget)
        toolbar.addWidget(QtWidgets.QLabel('  Saved on time change and close'))
        self.update_navigation()

    def update_navigation(self, active=None):
        active = self._uncertainty.is_running() if active is None else active
        enabled = not self.loading and not active
        self.previous.setEnabled(enabled and self.index > 0)
        self.next.setEnabled(enabled and self.index + 1 < len(self.controller.event.frames))
        self.time_selector.setEnabled(enabled)

    def set_loading(self, enabled):
        self.loading = enabled
        self.centralWidget().setEnabled(not enabled)
        for toolbar in self._figure_toolbars:
            toolbar.setEnabled(not enabled)
        self.update_navigation()

    def save(self):
        if self.loading or self._uncertainty.is_running():
            self.statusBar().showMessage('Finish loading or finish/cancel the search before saving.')
            return False
        try:
            persist(self)
        except Exception as error:
            QtWidgets.QMessageBox.warning(self, 'Cannot save this time', str(error))
            return False
        self.statusBar().showMessage('Saved fit, outlines and available search results to ' + str(self.session_folder))
        return True

    def closeEvent(self, event):
        if self.switching:
            self._uncertainty.stop_overlay_preview()
            event.accept()
        elif self.loading:
            self.controller.cancel_loading()
            event.ignore()
        elif self._uncertainty.is_running():
            super().closeEvent(event)
        elif self.save():
            self._uncertainty.stop_overlay_preview()
            event.accept()
            self.controller.app.quit()
        else:
            event.ignore()


class TimeSeriesController(QtCore.QObject):
    def __init__(self, event, app):
        super().__init__(app)
        self.event, self.app = event, app
        self.window = None
        self.loader = None
        self.dialog = None
        self.pending_index = None
        self.warm = None
        self.loading_error = None

    def open_frame(self, index):
        if self.loader is not None or not 0 <= index < len(self.event.frames):
            return
        if self.window is not None:
            if index == self.window.index or self.window._uncertainty.is_running():
                self.window.update_navigation()
                return
            if not self.window.save():
                self.reset_selector()
                return
            self.warm = self.window._uncertainty.annotation_payload()
            self.window._uncertainty.stop_editing()
            self.window.set_loading(True)
        else:
            self.warm = self.previous_saved(index)
        self.pending_index = index
        self.loading_error = None
        self.dialog = QtWidgets.QProgressDialog('Preparing observations…', 'Cancel', 0, 0)
        self.dialog.setWindowTitle('Loading ' + utc_string(self.event.frames[index].time))
        self.dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.canceled.connect(self.cancel_loading)
        self.dialog.show()
        self.loader = ImageLoader(self.event, self.event.frames[index], self)
        self.loader.progress.connect(self.dialog.setLabelText)
        self.loader.loaded.connect(self.frame_loaded)
        self.loader.failed.connect(self.load_failed)
        self.loader.finished.connect(self.loading_finished)
        self.loader.start()

    def previous_saved(self, index):
        for frame in reversed(self.event.frames[:index]):
            path = self.event.output_directory / 'frames' / frame.key / 'manual_traces.json'
            if path.exists():
                return json.loads(path.read_text())
        return None

    def frame_loaded(self, result):
        old = self.window
        try:
            window = FrameWindow(self, self.pending_index, *result, warm=self.warm)
        except Exception as error:
            # Dispose any partially constructed top-level window after a bad session.
            for widget in self.app.topLevelWidgets():
                if isinstance(widget, FrameWindow) and widget is not old:
                    widget.switching = True
                    widget.close()
                    widget.deleteLater()
            self.load_failed(str(error))
            return
        self.window = window
        if old is not None:
            window.setGeometry(old.geometry())
            window.setWindowState(old.windowState())
            for page, previous in zip(window._figure_pages, old._figure_pages):
                page.set_zoom(previous.zoom)
            old.switching = True
            old.close()
            old.deleteLater()
        window.show()

    def load_failed(self, message):
        self.loading_error = message

    def reset_selector(self):
        if self.window:
            self.window.time_selector.setCurrentIndex(self.window.index)

    def cancel_loading(self):
        if self.loader:
            self.loader.cancelled.set()
            if self.dialog:
                self.dialog.setLabelText('Cancelling after the current image operation…')
                self.dialog.setCancelButton(None)

    def loading_finished(self):
        self.loader.deleteLater()
        self.loader = None
        if self.dialog:
            self.dialog.reset()
            self.dialog.deleteLater()
            self.dialog = None
        if self.window:
            self.window.set_loading(False)
            self.reset_selector()
        if self.loading_error:
            QtWidgets.QMessageBox.warning(self.window, 'Cannot open this observation time', self.loading_error)
        if self.window is None:
            self.app.quit()


def run_event(event):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    controller = TimeSeriesController(event, app)
    QtCore.QTimer.singleShot(0, lambda: controller.open_frame(0))
    return app.exec_()
