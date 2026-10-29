"""Observer-specific ray intersections with the plane through the Sun.

Distances use one fixed solar radius (IAU nominal R_sun = 695700 km), rather
than instrument pixels or a deprojected three-dimensional CME distance.
"""
import numpy as np
from astropy import units as u
from astropy.constants import R_sun

PHYSICAL_UNIT = 'projected_solar_radii'
PIXEL_UNIT = 'native_image_pixels'


def score_unit(config):
    # Unversioned old search files were defined in native pixels.
    unit = config.get('score_unit', PIXEL_UNIT)
    if unit not in (PHYSICAL_UNIT, PIXEL_UNIT):
        raise ValueError('Unknown outline distance unit: ' + str(unit))
    return unit


class ProjectedDistance:
    """Map image pixels to transverse coordinates in the observer's sky plane."""
    def __init__(self, image):
        self.image = image
        try:
            self.distance_m = float(image.meta['dsun_obs'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('Physical outline distances require DSUN_OBS in metres.')
        if not np.isfinite(self.distance_m) or self.distance_m <= R_sun.to_value(u.m):
            raise ValueError('DSUN_OBS must be finite and larger than one solar radius.')
        self.factor = self.distance_m / R_sun.to_value(u.m)
        # Fail early for unsupported coordinate frames; never guess a plate scale.
        self.transform(np.array([[0., 0.]]))

    def transform(self, pixels):
        xy = np.asarray(pixels, dtype=float)
        if xy.ndim != 2 or xy.shape[1] != 2 or not np.isfinite(xy).all():
            raise ValueError('Expected finite image coordinates with shape (N, 2).')
        world = self.image.pixel_to_world(xy[:, 0] * u.pix, xy[:, 1] * u.pix)
        if world.frame.name != 'helioprojective':
            raise ValueError('Physical outline distances require helioprojective image WCS.')
        tx, ty = world.Tx.to_value(u.rad), world.Ty.to_value(u.rad)
        if np.any(np.cos(tx) * np.cos(ty) <= 0):
            raise ValueError('Image rays must face the Sun plane.')
        # HPC ray direction is (cos(Ty)cos(Tx), cos(Ty)sin(Tx), sin(Ty)).
        # Intersect that ray with the plane normal to Sun-observer at distance D.
        result = self.factor * np.column_stack((np.tan(tx), np.tan(ty) / np.cos(tx)))
        if not np.isfinite(result).all():
            raise ValueError('Image WCS produced nonfinite physical coordinates.')
        return result

    def provenance(self):
        return dict(unit=PHYSICAL_UNIT, dsun_obs_m=self.distance_m,
                    solar_radius_m=float(R_sun.to_value(u.m)),
                    projection='Ray intersection with plane through Sun centre normal to observer Sun line',
                    coordinates='D/R_sun * (tan(Tx), tan(Ty)/cos(Tx)); image helioprojective WCS')

    def legacy_pixel_scale(self):
        """Exact scalar migration only for a similarity map, never average axes."""
        h, w = self.image.data.shape
        points = np.array([[0., 0.], [w-1., 0.], [0., h-1.], [w-1., h-1.],
                           [(w-1)/2, (h-1)/2], [(w-1)/4, (h-1)/3]])
        physical = self.transform(points)
        matrix = np.column_stack(((physical[1]-physical[0])/(w-1),
                                  (physical[2]-physical[0])/(h-1)))
        scale = np.linalg.svd(matrix, compute_uv=False)
        predicted = points @ matrix.T + physical[0]
        if (not np.allclose(scale[0], scale[1], rtol=1e-6, atol=0) or
                not np.allclose(physical, predicted, rtol=0, atol=max(scale)*1e-6)):
            raise ValueError('Saved pixel allowances cannot be converted exactly for this WCS. '
                             'Choose allowances in solar radii for a new physical-distance session.')
        return float(scale.mean())
