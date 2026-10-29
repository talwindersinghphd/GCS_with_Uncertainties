"""Scrollable, uniformly zoomable Matplotlib pages with native pinch support."""
import math

from PyQt5 import QtCore, QtGui, QtWidgets


class FigurePage(QtWidgets.QScrollArea):
    zoom_changed = QtCore.pyqtSignal(float)
    MIN_ZOOM, MAX_ZOOM = .5, 3.

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.zoom = 1.
        self._base_dpi = canvas.figure.dpi / canvas.device_pixel_ratio
        self._anchor = None
        self._pinch_start_zoom = None
        self._layout_timer = QtCore.QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._layout_page)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setWidgetResizable(False)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setWidget(canvas)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        for widget in (self, self.viewport(), canvas):
            widget.installEventFilter(self)
            widget.grabGesture(QtCore.Qt.PinchGesture)
        self.setToolTip('Pinch to zoom the whole page. Scroll with two fingers or the mouse wheel. '
                        'Ctrl/Command + wheel also zooms. Fit page restores the full page.')

    def sizeHint(self):
        return QtCore.QSize(850, 650)

    def minimumSizeHint(self):
        return QtCore.QSize(120, 120)

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_timer.start(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_layout_timer'):
            self._layout_timer.start(0)

    def set_zoom(self, zoom, anchor=None):
        zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom))
        if math.isclose(zoom, self.zoom):
            return
        anchor = QtCore.QPointF(anchor if anchor is not None else self.viewport().rect().center())
        origin = self.canvas.mapTo(self.viewport(), QtCore.QPoint(0, 0))
        # Store a page fraction so the point under the fingers stays in place.
        point = QtCore.QPointF((anchor.x() - origin.x()) / max(1, self.canvas.width()),
                              (anchor.y() - origin.y()) / max(1, self.canvas.height()))
        self._anchor = point, anchor
        self.zoom = zoom
        self._layout_timer.start(16)
        self.zoom_changed.emit(zoom)

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.2)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.2)

    def fit_page(self):
        self.set_zoom(1.)
        self._anchor = None
        self._layout_timer.start(0)

    def _layout_page(self):
        if not self.isVisible():
            return
        # contentsRect excludes the frame, but not scrollbars: showing bars must
        # not change the page's base dimensions and trigger repeated shrinking.
        base = self.contentsRect().size()
        if base.width() < 1 or base.height() < 1:
            return
        anchor = self._anchor
        self._anchor = None
        if anchor is None:
            centre = self.viewport().rect().center()
            origin = self.canvas.mapTo(self.viewport(), QtCore.QPoint(0, 0))
            anchor = (QtCore.QPointF((centre.x() - origin.x()) / max(1, self.canvas.width()),
                                    (centre.y() - origin.y()) / max(1, self.canvas.height())), centre)
        size = QtCore.QSize(round(base.width() * self.zoom), round(base.height() * self.zoom))
        # Scale fonts, markers and lines with the images. Keeping the figure's
        # physical size stable also keeps saved figures independent of page zoom.
        figure = self.canvas.figure
        dpi = self._base_dpi * self.zoom
        figure._original_dpi = dpi  # Qt uses this when a window changes Retina screens.
        figure.set_dpi(dpi * self.canvas.device_pixel_ratio)
        self.canvas.resize(size)
        figure.set_size_inches(size.width() / dpi, size.height() / dpi, forward=False)
        point, position = anchor
        self.horizontalScrollBar().setValue(round(point.x() * size.width() - position.x()))
        self.verticalScrollBar().setValue(round(point.y() * size.height() - position.y()))
        self.canvas.draw_idle()

    def eventFilter(self, widget, event):
        if isinstance(event, QtGui.QNativeGestureEvent):
            kind = event.gestureType()
            if kind == QtCore.Qt.ZoomNativeGesture:
                anchor = self.viewport().mapFromGlobal(event.globalPos())
                self.set_zoom(self.zoom * max(.1, 1. + event.value()), anchor)
            elif kind not in (QtCore.Qt.BeginNativeGesture, QtCore.Qt.EndNativeGesture):
                return super().eventFilter(widget, event)
            event.accept()
            return True
        if event.type() == QtCore.QEvent.Gesture:
            pinch = event.gesture(QtCore.Qt.PinchGesture)
            if pinch is not None:
                if self._pinch_start_zoom is None or pinch.state() == QtCore.Qt.GestureStarted:
                    self._pinch_start_zoom = self.zoom
                # Qt's macOS recognizer reports a widget-local centre, while
                # its hot spot is always in screen coordinates.
                position = pinch.hotSpot() if pinch.hasHotSpot() else pinch.centerPoint()
                anchor = self.viewport().mapFromGlobal(position.toPoint())
                self.set_zoom(self._pinch_start_zoom * pinch.totalScaleFactor(), anchor)
                if pinch.state() in (QtCore.Qt.GestureFinished, QtCore.Qt.GestureCanceled):
                    self._pinch_start_zoom = None
                event.accept(pinch)
                return True
        if event.type() == QtCore.QEvent.Wheel:
            if event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.MetaModifier):
                delta = event.angleDelta().y() or event.pixelDelta().y()
                factor = math.exp(max(-1., min(1., delta * math.log(1.2) / 120.)))
                anchor = self.viewport().mapFromGlobal(event.globalPos())
                self.set_zoom(self.zoom * factor, anchor)
            else:
                delta = event.pixelDelta()
                if delta.isNull():
                    delta = event.angleDelta() / 2
                x, y = delta.x(), delta.y()
                if event.modifiers() & QtCore.Qt.ShiftModifier and not x:
                    x, y = y, 0
                self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - x)
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - y)
            event.accept()
            return True
        return super().eventFilter(widget, event)

    def add_zoom_controls(self, toolbar):
        toolbar.addSeparator()
        toolbar.addWidget(QtWidgets.QLabel('Page zoom: '))
        decrease = toolbar.addAction('−', self.zoom_out)
        decrease.setToolTip('Zoom out the whole figure page')
        value = QtWidgets.QLabel('100%')
        value.setAlignment(QtCore.Qt.AlignCenter)
        value.setFixedWidth(value.fontMetrics().horizontalAdvance('300%') + 10)
        self.zoom_changed.connect(lambda zoom: value.setText('{:.0f}%'.format(zoom * 100)))
        toolbar.addWidget(value)
        increase = toolbar.addAction('+', self.zoom_in)
        increase.setToolTip('Zoom in the whole figure page')
        reset = toolbar.addAction('Fit page', self.fit_page)
        reset.setToolTip('Fit the whole figure page in the available space')
        # Native toolbar buttons can default to a smaller font than nearby labels.
        for action in (decrease, increase, reset):
            toolbar.widgetForAction(action).setFont(value.font())
