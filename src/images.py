"""Observation loading and per-frame caches for arbitrary view lists."""
import hashlib
import json
from pathlib import Path
import threading
from concurrent.futures import CancelledError

import numpy as np
from PyQt5 import QtCore
from sunpy.map import Map

from . import gui
from .distance_metric import ProjectedDistance
from .event_config import utc_string, utc_datetime
from .example import bundled_background_times, bundled_maps, matches_bundled
from .paths import PROJECT_ROOT
from .local_fits import closest_exposure, exposure_map


def json_scalar(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError('Unsupported image metadata type: ' + type(value).__name__)


def frame_fingerprint(frame):
    return hashlib.sha256(json.dumps(frame.manifest(), sort_keys=True).encode()).hexdigest()


def local_path(pattern, date):
    path = Path(pattern.format(time=date)).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def raw_image(view, date, background=False):
    return _raw_image_and_source(view, date, background=background)[0]


def _raw_image_and_source(view, date, background=False, before=None, progress=None, cancelled=None):
    if background and (view.background_image or view.background_folder):
        pattern, folder = view.background_image, view.background_folder
    else:
        pattern, folder = view.image, view.image_folder
    path = local_path(folder or pattern, date) if folder or pattern else None
    if folder or (path is not None and path.is_dir()):
        exposure = closest_exposure(path, view, date, before=before, progress=progress, cancelled=cancelled)
        if progress is not None:
            progress('Selected {}: {} at {} UTC'.format(view.label, Path(exposure.path).name, exposure.utc))
        source = exposure.path + (' [HDU {}]'.format(exposure.hdu) if exposure.hdu else '')
        return exposure_map(exposure), source
    if path is not None:
        if not path.is_file():
            raise ValueError('Image file does not exist: ' + str(path))
        return Map(str(path)), str(path)
    observatory = 'STEREO_A' if view.spacecraft in ('STA', 'HISTA') else 'STEREO_B'
    if view.coronagraph == 'HI':
        return Map(gui.download_HI(date, observatory, include_background=False)), 'downloaded observation'
    if view.coronagraph == 'COR2beacon':
        return Map(gui.download_Cor2_beacon(date, observatory, include_background=False)), 'downloaded observation'
    instrument = 'LASCO' if view.spacecraft == 'SOHO' else 'SECCHI'
    return (gui.download_helioviewer(date, 'SOHO' if view.spacecraft == 'SOHO' else observatory,
                                   instrument, view.coronagraph), 'downloaded observation')


def observation_timing(frame, view, image, background_time, maximum):
    """Record signed time offsets; the selected limit warns without rejecting images."""
    offset = (image.date.to_datetime() - frame.time).total_seconds() / 60.
    requested_background = frame.background.date_for(frame.time, view)
    background_offset = ((utc_datetime(background_time) - requested_background).total_seconds() / 60.
                         if background_time is not None and requested_background is not None else None)
    return dict(time_offset_minutes=offset, time_limit_minutes=maximum,
                outside_time_limit=maximum is not None and abs(offset) > maximum,
                requested_background_time=utc_string(requested_background) if requested_background is not None else None,
                background_time_offset_minutes=background_offset,
                background_outside_time_limit=background_offset is not None and maximum is not None and abs(background_offset) > maximum)


def load_frame(event, frame, progress=lambda message: None, cancelled=None):
    cancelled = cancelled or threading.Event()
    folder = event.output_directory / 'frames' / frame.key
    folder.mkdir(parents=True, exist_ok=True)
    cache_path = folder / 'images.npz'
    fingerprint = frame_fingerprint(frame)
    if cache_path.exists():
        with np.load(cache_path, allow_pickle=False) as cache:
            if str(cache['fingerprint']) != fingerprint:
                raise ValueError('This saved time uses different views or backgrounds. Choose a different event/output folder to keep both versions.')
            observations = json.loads(str(cache['observations']))
            images = [Map(cache['data_' + str(i)], json.loads(str(cache['metadata_' + str(i)])))
                      for i in range(len(frame.views))]
        for view, image, observation in zip(frame.views, images, observations):
            # Recompute warnings when reopening with a different selected limit,
            # including caches written before timing information was recorded.
            observation.update(observation_timing(frame, view, image, observation.get('background_time'),
                                                  event.max_time_offset_minutes))
        return images, observations
    images, observations = [], []
    bundled = bundled_maps() if matches_bundled(frame) else None
    bundled_backgrounds = bundled_background_times() if bundled is not None else None
    for index, view in enumerate(frame.views):
        if cancelled.is_set():
            return None
        progress('Loading view {}/{}: {}'.format(index + 1, len(frame.views), view.label))
        if bundled is not None:
            image = bundled[view.spacecraft]
            background_time = utc_string(bundled_backgrounds[view.spacecraft])
            source = 'bundled July 12 observations'
            background_source = source
        else:
            try:
                target, source = _raw_image_and_source(view, frame.time, progress=progress, cancelled=cancelled)
            except CancelledError:
                return None
            requested_background = frame.background.date_for(frame.time, view)
            if requested_background is None:
                image, background_time, background_source = target, None, None
            else:
                try:
                    base, background_source = _raw_image_and_source(
                        view, requested_background, background=True,
                        before=target.date, progress=progress, cancelled=cancelled)
                except CancelledError:
                    return None
                if base.date >= target.date:
                    raise ValueError('The background image must precede the target for ' + view.label)
                if base.data.shape != target.data.shape:
                    raise ValueError('Target and background must have matching, registered pixel grids for ' + view.label)
                a, b = base.data.astype(float), target.data.astype(float)
                if not np.isfinite(a).all() or not np.isfinite(b).all() or a.std() <= 0 or b.std() <= 0:
                    raise ValueError('Invalid image intensities for ' + view.label)
                image = Map((b - b.mean()) / b.std() - (a - a.mean()) / a.std(), target.meta)
                background_time = base.date.isot + 'Z'
        if image.data.ndim != 2 or not np.isfinite(image.data).all():
            raise ValueError('Expected a finite, two-dimensional coronagraph image for ' + view.label)
        ProjectedDistance(image)
        images.append(image)
        observations.append(dict(view=view.manifest(), requested_time=utc_string(frame.time),
                                 observation_time=image.date.isot + 'Z', background_time=background_time,
                                 source=source, background_source=background_source,
                                 **observation_timing(frame, view, image, background_time,
                                                      event.max_time_offset_minutes)))
    if cancelled.is_set():
        return None
    contents = dict(fingerprint=np.array(fingerprint), observations=np.array(json.dumps(observations)))
    for index, image in enumerate(images):
        contents['data_' + str(index)] = image.data
        contents['metadata_' + str(index)] = np.array(json.dumps(dict(image.meta), default=json_scalar))
    temporary = cache_path.with_name('images.pending.npz')
    np.savez_compressed(temporary, **contents)
    temporary.replace(cache_path)
    return images, observations


class ImageLoader(QtCore.QThread):
    loaded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    progress = QtCore.pyqtSignal(str)

    def __init__(self, event, frame, parent=None):
        super().__init__(parent)
        self.event, self.frame = event, frame
        self.cancelled = threading.Event()

    def run(self):
        try:
            result = load_frame(self.event, self.frame, self.progress.emit, self.cancelled)
            if result is not None and not self.cancelled.is_set():
                self.loaded.emit(result)
        except Exception as error:
            self.failed.emit(str(error))
