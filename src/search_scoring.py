"""GUI-free candidate projection and bounded, spawn-based parallel scoring."""
from collections import deque
from concurrent.futures import ProcessPoolExecutor, wait
from contextlib import ExitStack
import multiprocessing
import os

import numpy as np
from astropy import units as u

from .geometry import gcs_mesh_sunpy
from .outline_uncertainty import PARAMETERS, surface_faces, silhouette_boundary
from .tiered_outlines import prepare_checks, check_errors, passes_checks

# Independent of the display mesh: scoring must not change with display mode.
STRAIGHT, FRONT, CIRCLE = 10, 24, 48
FACES = surface_faces((FRONT + STRAIGHT) * 2 - 3, CIRCLE)


def available_cpus():
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    # ProcessPoolExecutor has a 61-worker limit on Windows.
    return max(1, min(count, 61) if os.name == 'nt' else count)


def project_boundaries(parameters, date, images, resolution, density=1):
    return dict(iter_projected_boundaries(parameters, date, images, resolution, density))


def display_boundaries(parameters, date, images, resolution, density=1, existing=None, cancelled=None):
    """Complete a preview in other views without adding acceptance checks.

    Reuse silhouettes already calculated while scoring. A model completely outside
    an untraced image can have no visible silhouette there; it still remains an
    accepted fit when all of the actual outline checks pass.
    """
    boundaries = dict(existing or {})
    missing = {view: image for view, image in images.items() if view not in boundaries}
    if missing:
        try:
            boundaries.update(iter_projected_boundaries(parameters, date, missing, resolution, density,
                skip_invalid=True, cancelled=cancelled))
        except (ValueError, FloatingPointError):
            pass
    return boundaries


def iter_projected_boundaries(parameters, date, images, resolution, density=1, skip_invalid=False, cancelled=None):
    """Project one view at a time so a rejected fit can stop before later views."""
    straight, front, circle = STRAIGHT * density, FRONT * density, CIRCLE * density
    faces = FACES if density == 1 else surface_faces((front + straight) * 2 - 3, circle)
    mesh = gcs_mesh_sunpy(date, np.deg2rad(parameters['half_angle']),
                         parameters['height'], straight, front, circle,
                         parameters['kappa'], np.deg2rad(parameters['lat']),
                         np.deg2rad(parameters['lon']), np.deg2rad(parameters['tilt']))
    for index, image in images.items():
        if cancelled is not None and cancelled.is_set():
            return
        try:
            x, y = image.world_to_pixel(mesh.transform_to(image.coordinate_frame))
            boundary = silhouette_boundary(np.column_stack((x.to_value(u.pix), y.to_value(u.pix))),
                                           faces, image.data.shape, resolution)
        except (ValueError, FloatingPointError):
            if skip_invalid:
                continue
            raise
        yield index, boundary


def score_candidate(row, config, images, date, checks, baseline, project=None):
    """Return scores and accepted boundaries; skip later views after rejection.

    Rejected candidates may have NaN scores for views that were never evaluated.
    Accepted candidates always retain every view/tier score in the original order.
    """
    try:
        parameters = dict(zip(PARAMETERS, row))
        if project is not None and project is not project_boundaries:
            # Preserve custom projection hooks used by callers and diagnostics.
            boundaries = project(parameters, date, images, config['resolution'], config.get('mesh_density', 1))
            scores = check_errors(checks, boundaries)
            return scores, boundaries if passes_checks(scores, baseline, checks) else None
        scores = np.full(len(checks), np.nan)
        boundaries = {}
        for view, boundary in iter_projected_boundaries(parameters, date, images,
                config['resolution'], config.get('mesh_density', 1)):
            indices = [i for i, check in enumerate(checks) if check['view'] == view]
            view_checks = [checks[i] for i in indices]
            view_scores = check_errors(view_checks, {view: boundary})
            scores[indices] = view_scores
            if not passes_checks(view_scores, baseline, view_checks):
                return scores, None
            boundaries[view] = boundary
    except (ValueError, FloatingPointError):
        return None, None
    return scores, boundaries if passes_checks(scores, baseline, checks) else None


_worker_state = None
_worker_thread_limit = None


def _init_scorer(config, views, date, baseline):
    from sunpy.map import Map
    from threadpoolctl import threadpool_limits
    global _worker_thread_limit
    # Keep native numerical libraries from adding a second layer of CPU threads.
    _worker_thread_limit = threadpool_limits(limits=1)
    # Projection needs shape and WCS, not image intensities. Reconstruct maps
    # locally with a zero-stride array instead of copying full images per CPU.
    images = {i: Map(np.broadcast_to(np.zeros((1, 1)), shape), metadata)
              for i, (shape, metadata) in views.items()}
    checks = prepare_checks(config, images)
    global _worker_state
    _worker_state = config, images, date, checks, baseline


def _score_row(index, row):
    return (index, *score_candidate(row, *_worker_state))


def create_scoring_pool(config, images, date, baseline, cpus):
    views = {i: (image.data.shape, dict(image.meta)) for i, image in images.items()}
    return ProcessPoolExecutor(max_workers=cpus,
        mp_context=multiprocessing.get_context('spawn'),
        initializer=_init_scorer, initargs=(config, views, date, baseline))


def parallel_scores(values, config, images, date, baseline, cpus, cancelled, pool=None,
                    stop_submitting=None):
    """Yield fits in candidate order, keeping at most one pending fit per CPU.

    Cancellation or a reached target stops submissions and drains in-flight fits.
    The bounded ordered window prevents faster candidates from deciding which
    accepted fits are retained, or getting an entire batch ahead of a slow fit.
    Spawn avoids forking the GUI and its running threads on macOS and Linux.
    """
    pending = deque()
    rows = iter(enumerate(values))
    with ExitStack() as resources:
        if pool is None:
            pool = resources.enter_context(create_scoring_pool(config, images, date, baseline, cpus))
        try:
            while True:
                if cancelled.is_set() or (stop_submitting is not None and stop_submitting.is_set()):
                    for future in pending:
                        future.cancel()
                else:
                    while len(pending) < cpus:
                        item = next(rows, None)
                        if item is None:
                            break
                        pending.append(pool.submit(_score_row, *item))
                if not pending:
                    break
                future = pending[0]
                if future.cancelled():
                    pending.popleft()
                    continue
                if not future.done():
                    wait((future,), timeout=.1)
                if not future.done():
                    continue
                pending.popleft()
                yield future.result()
        finally:
            for future in pending:
                future.cancel()
