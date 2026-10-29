"""Independent image display adjustments without changing observation data."""
import numpy as np
from matplotlib import colors
from PyQt5 import QtWidgets

from .widgets import SliderAndTextbox


class ImageDisplayControl(QtWidgets.QGroupBox):
    def __init__(self, title, artist, canvas):
        super().__init__(title)
        self.artist = artist
        self.canvas = canvas
        self.original_limits = artist.get_clim()
        self.brightness = SliderAndTextbox('Brightness [%]', -100, 100, 0, resolution=1)
        self.contrast = SliderAndTextbox('Contrast [%]', 10, 500, 100, resolution=1)
        self.brightness.setToolTip('Positive values brighten this image; zero keeps its original brightness.')
        self.contrast.setToolTip('100% keeps the original contrast; larger values emphasize intensity differences.')
        self.reset_button = QtWidgets.QPushButton('Reset this image')
        self.reset_button.clicked.connect(self.reset)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.brightness)
        layout.addWidget(self.contrast)
        layout.addWidget(self.reset_button)
        self.brightness.valueChanged.connect(self.update_display)
        self.contrast.valueChanged.connect(self.update_display)

    def settings(self):
        return dict(brightness=int(self.brightness.val), contrast=int(self.contrast.val))

    @staticmethod
    def validate_settings(settings):
        if not isinstance(settings, dict):
            raise ValueError('Image display settings must contain brightness and contrast.')
        for key, minimum, maximum in (('brightness', -100, 100), ('contrast', 10, 500)):
            value = settings.get(key)
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not np.isfinite(value) or not minimum <= value <= maximum):
                raise ValueError('Image {} must be between {} and {} percent.'.format(key, minimum, maximum))

    def set_settings(self, settings):
        self.validate_settings(settings)
        self.brightness.numbox.setValue(settings['brightness'])
        self.contrast.numbox.setValue(settings['contrast'])

    def reset(self):
        self.set_settings(dict(brightness=0, contrast=100))

    def update_display(self, *args):
        brightness, contrast = self.brightness.val / 100., self.contrast.val / 100.
        if brightness == 0 and contrast == 1:
            self.artist.set_clim(*self.original_limits)
        else:
            # Preserve each image's original stretch. LogNorm needs positive
            # limits, so shift and scale its range in logarithmic coordinates.
            logarithmic = isinstance(self.artist.norm, colors.LogNorm)
            low, high = np.log(self.original_limits) if logarithmic else self.original_limits
            width = high - low
            if width == 0:
                width = max(abs(low) * .01, 1.)
            half_width = width / (2 * contrast)
            centre = low + width / 2 - brightness * width / contrast
            limits = centre - half_width, centre + half_width
            if logarithmic:
                limits = np.exp(limits)
            self.artist.set_clim(*limits)
        self.canvas.draw_idle()
