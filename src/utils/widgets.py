import math
from html import escape
import re

from PyQt5 import QtCore, QtGui, QtWidgets


class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """Hide trailing fractional zeros while retaining the full stored precision."""
    def textFromValue(self, value):
        text = super().textFromValue(value)
        if self.decimals():
            text = text.rstrip(self.locale().zeroDigit()).rstrip(self.locale().decimalPoint())
        return text


class SearchStatusLabel(QtWidgets.QLabel):
    """Render live counts in stable columns while keeping plain status text."""
    _counts = re.compile(r'^Fits tried: (\d+)(?: / (\d+))? \| Accepted: (\d+)(?: / (\d+))?(.*)$', re.S)

    def __init__(self, text):
        super().__init__()
        self.setText(text)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.FontChange and hasattr(self, '_plain_text'):
            self.setText(self._plain_text)

    def text(self):
        return self._plain_text

    def setText(self, text):
        self._plain_text = text
        counts = self._counts.match(text)
        if counts is None:
            self.setTextFormat(QtCore.Qt.PlainText)
            super().setText(text)
            return
        tried, total, accepted, target, hint = counts.groups()
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        # Keep the fixed-width number family, but inherit the surrounding UI's
        # size rather than the often smaller system monospace default.
        if self.font().pixelSize() > 0:
            font.setPixelSize(self.font().pixelSize())
            size = '{}px'.format(font.pixelSize())
        else:
            font.setPointSizeF(self.font().pointSizeF())
            size = '{}pt'.format(font.pointSizeF())
        number_style = 'font-family: {}; font-size: {}'.format(escape(font.family(), quote=True), size)
        number_width = QtGui.QFontMetrics(font).horizontalAdvance('9' * 7) + 8
        rows = []
        for label, count, limit in (('Fits tried:', tried, total), ('Accepted:', accepted, target)):
            rows.append('<tr><td>{}</td><td width="{}" align="right" style="{}">{}</td>'
                        '<td style="{}">{}</td></tr>'.format(
                            label, number_width, number_style, count,
                            number_style, '/ ' + limit if limit else ''))
        rendered = '<table cellspacing="0" cellpadding="2" style="font-size: {}">{}</table>'.format(size, ''.join(rows))
        hint = hint.strip(' \n—')
        if hint:
            rendered += '<div>{}</div>'.format(escape(hint).replace('\n', '<br>'))
        self.setTextFormat(QtCore.Qt.RichText)
        super().setText(rendered)


class SliderAndTextbox(QtWidgets.QWidget):
    """
    Widget representing a slider with label and textbox
    """

    def __init__(self, label, min, max, init, resolution=0.1):
        super(SliderAndTextbox, self).__init__()
        self.resolution = resolution
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(int(min / resolution))
        self.slider.setMaximum(int(max / resolution))
        self.slider.setValue(int(init / resolution))
        self.slider.valueChanged.connect(self.handleSliderValueChange)

        self.numbox = QtWidgets.QDoubleSpinBox()
        self.numbox.setMinimum(min)
        self.numbox.setMaximum(max)
        self.numbox.setValue(init)
        self.numbox.setDecimals(-int(math.log10(resolution)))
        self.numbox.setSingleStep(resolution)
        self.numbox.setKeyboardTracking(False)  # when editing text, only update after return key pressed
        self.numbox.valueChanged.connect(self.handleNumboxValueChange)

        self.label = QtWidgets.QLabel()
        self.label.setText(label)

        vlayout = QtWidgets.QVBoxLayout(self)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(2)
        layout = QtWidgets.QHBoxLayout()
        vlayout.addWidget(self.label)
        layout.addWidget(self.slider)
        layout.addWidget(self.numbox)
        vlayout.addLayout(layout)

        self.valueChanged = self.numbox.valueChanged

    @property
    def val(self):
        return self.numbox.value()

    @QtCore.pyqtSlot(int)
    def handleSliderValueChange(self, value):
        self.numbox.setValue(value * self.resolution)


    @QtCore.pyqtSlot(float)
    def handleNumboxValueChange(self, value):
        # Prevent values outside slider range
        if value < self.slider.minimum():
            self.numbox.setValue(self.slider.minimum())
        elif value > self.slider.maximum():
            self.numbox.setValue(self.slider.maximum())

        self.slider.setValue(int(round(self.numbox.value() / self.resolution)))
