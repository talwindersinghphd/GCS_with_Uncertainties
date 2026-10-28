"""Offline synthetic demonstration. These images are NOT real CME observations.

Run: python demo_uncertainty.py
"""
from datetime import datetime
import sys

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from scipy.spatial import cKDTree
from sunpy.coordinates import frames
from sunpy.map import Map, make_fitswcs_header
from PyQt5 import QtWidgets

import gui
from uncertainty_gui import project_boundaries

DATE = datetime(2012, 7, 12, 17, 24)
REFERENCE = dict(lat=0, lon=0, tilt=20, height=8, half_angle=30, kappa=.3)


def synthetic_images():
    maps = {}
    for i, longitude in enumerate((0, 60)):
        observer = SkyCoord(longitude * u.deg, 0 * u.deg, 1 * u.AU,
                            frame=frames.HeliographicStonyhurst, obstime=DATE)
        center = SkyCoord(0 * u.arcsec, 0 * u.arcsec,
                          frame=frames.Helioprojective(observer=observer, obstime=DATE))
        data = np.zeros((512, 512))
        meta = make_fitswcs_header(data, center, scale=[40, 40] * u.arcsec / u.pix)
        maps[i] = Map(data, meta)
    boundaries = project_boundaries(REFERENCE, DATE, maps, 1024)
    yy, xx = np.indices((512, 512))
    xy = np.column_stack((xx.ravel(), yy.ravel()))
    rng = np.random.default_rng(12)
    for i, image in list(maps.items()):
        distance = cKDTree(boundaries[i]).query(xy)[0].reshape(512, 512)
        data = np.exp(-distance ** 2 / 8) + rng.normal(0, .03, (512, 512))
        data[np.hypot(xx - 255.5, yy - 255.5) < 40] = -.1
        maps[i] = Map(data, image.meta)
    return maps, boundaries


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    maps, boundaries = synthetic_images()
    # Override only for this separate demonstration process.
    gui.load_image = lambda sc, *args: maps[{'STA': 0, 'STB': 1}[sc]]
    gui.load_params = lambda date: REFERENCE.copy()
    window = gui.GCSGui(DATE, ['STA', 'STB'])
    window.setWindowTitle('SYNTHETIC DEMO — GCS outline-compatible uncertainty')
    panel = window._uncertainty
    for k, xy in boundaries.items():
        arc = xy[xy[:, 1] > np.percentile(xy[:, 1], 90)]
        arc = arc[np.argsort(arc[:, 0])]
        arc = arc[np.linspace(0, len(arc) - 1, 12).astype(int)]
        panel.outlines[k] = arc.tolist()
        panel.draw_trace(k)
        window._axes[k].set_title(('STA' if k == 0 else 'STB') + ' — SYNTHETIC')
    panel.preview()
    window._control_tabs.setCurrentIndex(1)
    window.show()
    app.exec_()


if __name__ == '__main__':
    main()
