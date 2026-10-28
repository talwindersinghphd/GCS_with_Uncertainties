"""Outline-compatible GCS searches. No GUI or solar dependencies in this module.

These are bounded search results, NOT calibrated posterior samples. Angles are
degrees; traced coordinates, distances, and tolerances are native image pixels.
"""
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion, binary_fill_holes
from scipy.spatial import cKDTree
from scipy.stats import qmc

PARAMETERS = ('lat', 'lon', 'tilt', 'height', 'half_angle', 'kappa')
# Angular numbers: Singh et al. (2022). The remaining scales are editable
# exploratory defaults, NOT measurements from that paper or Gaussian sigmas.
DEFAULT_SCALES = dict(lat=5.74, lon=11.23, tilt=24.71,
                      height=0.6, half_angle=10.0, kappa=0.1)
SCALE_SOURCES = dict(lat='Singh et al. 2022 average spread',
                     lon='Singh et al. 2022 average spread',
                     tilt='Singh et al. 2022 average spread',
                     height='exploratory default', half_angle='exploratory default',
                     kappa='exploratory default')


def resample_trace(points, count=128):
    """Uniform arclength sampling of an OPEN observed polyline."""
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
        raise ValueError('Trace at least two distinct points in each arc.')
    if not np.isfinite(points).all():
        raise ValueError('Trace contains non-finite coordinates.')
    lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    points = points[np.r_[True, lengths > 1e-8]]
    if len(points) < 2:
        raise ValueError('Trace needs two distinct points.')
    distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    locations = np.linspace(0, distance[-1], count)
    return np.column_stack([np.interp(locations, distance, points[:, j]) for j in (0, 1)])


def surface_faces(rows, columns):
    """Quads of the existing GCS ring mesh (last angular point repeats first)."""
    grid = np.arange(rows * columns).reshape(rows, columns)
    return np.stack((grid[:-1, :-1], grid[1:, :-1],
                     grid[1:, 1:], grid[:-1, 1:]), axis=-1).reshape(-1, 4)


def silhouette_boundary(projected, faces, image_shape, resolution=1024):
    """Rasterize the projected surface union, then return its exterior boundary.

    Rendering each quad as two triangles avoids nonconvex-quad fill artifacts.
    Padding and rejecting frame-edge pixels prevent clipping edges being scored
    as the CME outline. No convex hull is used: concave exterior is preserved.
    The numerical boundary accuracy is of order max(image_shape)/resolution px.
    """
    points = np.asarray(projected, dtype=float)
    if not np.isfinite(points).all():
        raise ValueError('Non-finite projected surface.')
    height, width = image_shape
    scale = max(height, width) / float(resolution)
    pad = 4
    raster_width = int(np.ceil(width / scale)) + 2 * pad
    raster_height = int(np.ceil(height / scale)) + 2 * pad
    raster = Image.new('1', (raster_width, raster_height), 0)
    draw = ImageDraw.Draw(raster)
    p = (points + 0.5) / scale + pad
    for face in faces:
        q = p[face]
        draw.polygon([tuple(v) for v in q[[0, 1, 2]]], fill=1)
        draw.polygon([tuple(v) for v in q[[0, 2, 3]]], fill=1)
    mask = binary_fill_holes(np.asarray(raster, dtype=bool))
    boundary = mask & ~binary_erosion(mask)
    yy, xx = np.nonzero(boundary)
    xy = (np.column_stack((xx, yy)) + 0.5 - pad) * scale - 0.5
    # Don't interpret an image crop as a physical model boundary.
    inside = ((xy[:, 0] > scale) & (xy[:, 0] < width - 1 - scale) &
              (xy[:, 1] > scale) & (xy[:, 1] < height - 1 - scale))
    xy = xy[inside]
    if len(xy) < 3:
        raise ValueError('No usable model silhouette within image.')
    return xy


def outline_error(trace, boundary):
    """Observed-to-model RMS distance. Partial traces incur no missing-arc penalty."""
    distances = cKDTree(boundary).query(trace)[0]
    return float(np.sqrt(np.mean(distances ** 2)))


def search_candidates(reference, scales, multiplier, exponent, seed):
    """Uniform Sobol search within +/- multiplier*scale, rejecting invalid geometry.

    The reference is evaluated separately and is not inserted as a fake draw.
    Returned offsets retain unwrapped angular differences for range diagnostics.
    """
    scales = np.array([scales[k] for k in PARAMETERS], dtype=float)
    if not np.isfinite(scales).all() or np.any(scales < 0) or multiplier <= 0:
        raise ValueError('Scales must be finite and nonnegative; multiplier must be positive.')
    if not np.any(scales > 0):
        raise ValueError('Vary at least one parameter; an all-fixed search would only duplicate the manual fit.')
    if not 1 <= exponent <= 20:
        raise ValueError('Search exponent must be between 1 and 20.')
    halfwidth = scales * multiplier
    if halfwidth[1] > 180 or halfwidth[2] > 90:
        raise ValueError('Longitude half-range must be <=180 degrees; tilt <=90 degrees. '
                         'Larger ranges repeat equivalent geometric orientations.')
    unit = qmc.Sobol(d=6, scramble=True, seed=seed).random_base2(exponent)
    offsets = (2 * unit - 1) * halfwidth
    values = np.array([reference[k] for k in PARAMETERS]) + offsets
    values[:, 1] %= 360
    values[:, 2] = (values[:, 2] + 90) % 180 - 90
    valid = (np.isfinite(values).all(axis=1) & (np.abs(values[:, 0]) <= 90) &
             (values[:, 3] > 0) & (values[:, 4] > 0) & (values[:, 4] < 90) &
             (values[:, 5] > 0) & (values[:, 5] < 1))
    return values[valid], offsets[valid], int((~valid).sum()), halfwidth


def summarize(values, offsets, reference, halfwidth):
    """Design-dependent quantiles with angles unwrapped around the manual fit."""
    if len(values) == 0:
        return {}, []
    summary, edges = {}, []
    for j, key in enumerate(PARAMETERS):
        samples = reference[key] + offsets[:, j] if key in ('lon', 'tilt') else values[:, j]
        q = np.percentile(samples, [2.5, 16, 50, 84, 97.5])
        summary[key] = dict(zip(('p2_5', 'p16', 'median', 'p84', 'p97_5'), map(float, q)))
        summary[key].update(min=float(samples.min()), max=float(samples.max()))
        if halfwidth[j] > 0 and np.any(np.abs(offsets[:, j]) >= 0.95 * halfwidth[j]):
            edges.append(key)
    return summary, edges
