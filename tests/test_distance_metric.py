"""Physical distances must not depend on plate scale, rotation or image size."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from astropy import units as u
from astropy.constants import R_sun
from astropy.coordinates import SkyCoord
from sunpy.coordinates import frames
from sunpy.map import Map, make_fitswcs_header
from PyQt5 import QtWidgets

from distance_metric import PHYSICAL_UNIT, ProjectedDistance
from tiered_outlines import prepare_checks, check_error
from uncertainty_gui import SearchWorker, export_result
from test_solar_integration import DATE, REFERENCE, config


def observation(scales=(40,40), distance=1., rotation=0., shape=(512,512), center_arcsec=(0,0)):
    observer=SkyCoord(0*u.deg,0*u.deg,distance*u.AU,
                      frame=frames.HeliographicStonyhurst,obstime=DATE)
    center=SkyCoord(center_arcsec[0]*u.arcsec,center_arcsec[1]*u.arcsec,frame=frames.Helioprojective(observer=observer,obstime=DATE))
    data=np.zeros(shape)
    return Map(data,make_fitswcs_header(data,center,scale=np.array(scales)*u.arcsec/u.pix,
                                      rotation_angle=rotation*u.deg))


def pixels(image, xy):
    # Inverse of ray-plane intersection, independent of the metric implementation.
    d=float(image.meta['dsun_obs'])/R_sun.to_value(u.m)
    x,y=np.asarray(xy).T
    tx=np.arctan2(x,d)
    ty=np.arctan2(y,np.sqrt(d*d+x*x))
    world=SkyCoord(tx*u.rad,ty*u.rad,frame=image.coordinate_frame)
    a,b=image.world_to_pixel(world)
    return np.column_stack((a.to_value(u.pix),b.to_value(u.pix)))


class PhysicalDistances(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_same_physical_mismatch_with_different_wcs_and_observer_distance(self):
        arc=np.column_stack((np.linspace(-1,1,10),np.full(10,.2)))
        edge=np.column_stack((np.linspace(-1,1,128),np.zeros(128)))
        for image in [observation(),observation((20,20),shape=(1024,1024)),
                      observation((20,60),rotation=37),observation((35,35),distance=.7),
                      observation(center_arcsec=(1200,800),rotation=19)]:
            c=dict(score_unit=PHYSICAL_UNIT,tier_outlines={'0':{'1':[pixels(image,arc).tolist()]}},
                   tier_tolerances={'0':{'1':.05}})
            check=prepare_checks(c,{0:image})[0]
            self.assertAlmostEqual(check_error(check,pixels(image,edge)),.2,places=9)
            # Arc weighting itself uses physical length, not anisotropic pixels.
            self.assertAlmostEqual(check['sample'][1].sum(),2.,places=9)

    def test_anisotropic_pixels_use_direction_dependent_physical_lengths(self):
        image=observation((20,60),rotation=0)
        metric=ProjectedDistance(image)
        xy=metric.transform(np.array([[255.,255.],[256.,255.],[255.,256.]]))
        dx=np.linalg.norm(xy[1]-xy[0]);dy=np.linalg.norm(xy[2]-xy[0])
        self.assertAlmostEqual(dy/dx,3,places=8)
        with self.assertRaisesRegex(ValueError,'cannot be converted exactly'):
            metric.legacy_pixel_scale()

    def test_missing_distance_and_unsupported_units_fail(self):
        image=observation();header=image.meta.copy();del header['dsun_obs']
        with self.assertRaisesRegex(ValueError,'DSUN_OBS'):
            ProjectedDistance(Map(image.data,header))
        with self.assertRaisesRegex(ValueError,'Unknown outline distance unit'):
            prepare_checks(dict(score_unit='km'))

    def test_physical_worker_export_labels_and_geometry(self):
        im={0:observation()}
        c=config(im);c['score_unit']=PHYSICAL_UNIT;c['tolerances']={'0':2.}
        worker=SearchWorker(c,im,DATE);results=[];failures=[]
        worker.completed.connect(results.append);worker.failed.connect(failures.append);worker.run()
        self.assertFalse(failures,failures);r=results[0]
        self.assertEqual(r['distance_unit'],PHYSICAL_UNIT)
        self.assertEqual(len(r['accepted']),64)
        with tempfile.TemporaryDirectory() as root:
            folder=export_result(r,root)
            meta=json.loads((folder/'search.json').read_text())
            self.assertEqual(meta['units']['errors'],PHYSICAL_UNIT)
            self.assertEqual(meta['distance_geometry']['0']['solar_radius_m'],695700000.)
            with (folder/'accepted_all.csv').open() as f:
                self.assertIn('rms_rsun_view_0',csv.DictReader(f).fieldnames)
            with np.load(folder/'accepted_arrays.npz',allow_pickle=False) as data:
                self.assertEqual(str(data['error_unit']),PHYSICAL_UNIT)

    def test_saved_pixel_allowances_migrate_once_then_round_trip(self):
        import gui
        image=observation()
        with patch.object(gui,'load_image',return_value=image),patch.object(gui,'load_params',return_value=REFERENCE):
            window=gui.GCSGui(DATE,['STA'])
        panel=window._uncertainty
        old=dict(format='gcs-outlines-v2',images=panel.image_identity(),reference=window.get_params_dict(),
                 tier_outlines={'0':{'1':[[[100.,100.],[200.,120.]]]}},
                 tier_tolerances={'0':{'1':3.,'2':12.}})
        panel.restore_annotations(old)
        scale=ProjectedDistance(image).legacy_pixel_scale()
        self.assertAlmostEqual(panel.tolerances[0].value(),3*scale,places=9)
        self.assertAlmostEqual(panel.tier2_tolerances[0].value(),12*scale,places=9)
        new=json.loads(json.dumps(panel.annotation_payload()))
        self.assertEqual(new['format'],'gcs-outlines-v3')
        self.assertEqual(new['migration']['original_tier_tolerances'],old['tier_tolerances'])
        panel.restore_annotations(new)
        self.assertEqual(panel.annotation_payload()['tier_tolerances'],new['tier_tolerances'])
        bad=deepcopy(new);bad['score_unit']='pixels'
        with self.assertRaises(ValueError):panel.restore_annotations(bad)
        self.assertEqual(panel.annotation_payload()['tier_tolerances'],new['tier_tolerances'])
        # Legacy metadata is not modified in place.
        self.assertEqual(old['tier_tolerances']['0']['1'],3.)
        window.close()
