"""Validated event schedules with an arbitrary number of observation views."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re

from .paths import PROJECT_ROOT, SESSION_ROOT

DEFAULT_PARAMETERS = dict(lat=0., lon=0., tilt=0., height=10., half_angle=25., kappa=.25)
DETECTORS = {'STA': ('COR1', 'COR2', 'COR2beacon'), 'STB': ('COR1', 'COR2', 'COR2beacon'),
             'SOHO': ('C2', 'C3'), 'HISTA': ('HI',), 'HISTB': ('HI',)}


def utc_datetime(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('Use UTC dates such as "2012-07-12 17:54" or "2012-07-12T17:54:00Z".')
    if not isinstance(value, datetime):
        raise ValueError('An observation time must be a UTC date/time string.')
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def utc_string(value):
    return value.isoformat() + 'Z'


@dataclass(frozen=True)
class View:
    spacecraft: str
    coronagraph: str
    image: str = None
    background_image: str = None
    background_time: datetime = None
    image_folder: str = None
    background_folder: str = None

    @property
    def label(self):
        return '{} / {}'.format(self.spacecraft, self.coronagraph)

    def manifest(self):
        result = dict(spacecraft=self.spacecraft, coronagraph=self.coronagraph)
        for key in ('image', 'background_image', 'image_folder', 'background_folder'):
            if getattr(self, key) is not None:
                result[key] = getattr(self, key)
        if self.background_time is not None:
            result['background_time'] = utc_string(self.background_time)
        return result


@dataclass(frozen=True)
class Background:
    mode: str
    time: datetime = None
    minutes: float = 60

    def date_for(self, date, view):
        if self.mode == 'raw':
            return None
        return view.background_time or (self.time if self.mode == 'fixed' else date - timedelta(minutes=self.minutes))

    def manifest(self):
        return dict(mode=self.mode, **({'time': utc_string(self.time)} if self.mode == 'fixed' else
                                     {'minutes': self.minutes} if self.mode == 'running' else {}))


@dataclass(frozen=True)
class Frame:
    time: datetime
    views: tuple
    background: Background

    @property
    def key(self):
        return self.time.strftime('%Y%m%dT%H%M%S') + ('.{:06d}'.format(self.time.microsecond) if self.time.microsecond else '') + 'Z'

    def manifest(self):
        return dict(time=utc_string(self.time), views=[view.manifest() for view in self.views],
                    background=self.background.manifest())


@dataclass(frozen=True)
class Event:
    name: str
    frames: tuple
    output_directory: Path
    max_time_offset_minutes: float
    initial_parameters: dict = None

    def manifest(self):
        return dict(name=self.name, times=[frame.manifest() for frame in self.frames],
                    output_directory=str(self.output_directory),
                    max_time_offset_minutes=self.max_time_offset_minutes,
                    initial_parameters=self.initial_parameters)


def parse_views(items):
    if not isinstance(items, (list, tuple)) or not items:
        raise ValueError('views must contain at least one spacecraft/coronagraph entry.')
    views = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Each view must be a dictionary with spacecraft and coronagraph.')
        spacecraft = str(item.get('spacecraft', '')).strip().upper()
        detector = str(item.get('coronagraph', '')).strip()
        detector = 'COR2beacon' if detector.upper() == 'COR2BEACON' else detector.upper()
        if not spacecraft or not detector:
            raise ValueError('Each view needs a spacecraft name and coronagraph.')
        for key in ('image', 'background_image', 'image_folder', 'background_folder'):
            if key in item and item[key] is not None and not str(item[key]).strip():
                raise ValueError(key + ' must be a nonempty path.')
        if item.get('image') and item.get('image_folder'):
            raise ValueError('Choose image or image_folder for a view, rather than both.')
        if item.get('background_image') and item.get('background_folder'):
            raise ValueError('Choose background_image or background_folder for a view, rather than both.')
        if not (item.get('image') or item.get('image_folder')) and detector not in DETECTORS.get(spacecraft, ()):
            raise ValueError('Unsupported download view {} / {}. Supply an image file or folder for another instrument.'.format(spacecraft, detector))
        view = View(spacecraft, detector,
                    image=str(item['image']) if item.get('image') else None,
                    background_image=str(item['background_image']) if item.get('background_image') else None,
                    background_time=utc_datetime(item['background_time']) if item.get('background_time') else None,
                    image_folder=str(item['image_folder']) if item.get('image_folder') else None,
                    background_folder=str(item['background_folder']) if item.get('background_folder') else None)
        if view in views:
            raise ValueError('Duplicate observation view: ' + view.label)
        views.append(view)
    return tuple(views)


def parse_background(value):
    if value is None:
        value = {'mode': 'raw'}
    if not isinstance(value, dict):
        raise ValueError('background must be a dictionary with mode fixed, running, or raw.')
    mode = value.get('mode', 'raw')
    if mode not in ('raw', 'fixed', 'running'):
        raise ValueError('background.mode must be fixed, running, or raw.')
    if mode == 'fixed':
        if not value.get('time'):
            raise ValueError('A fixed background needs its UTC time.')
        return Background(mode, utc_datetime(value['time']))
    minutes = float(value.get('minutes', 60))
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError('The running background interval must be positive.')
    return Background(mode, minutes=minutes)


def parse_event(settings):
    if not isinstance(settings, dict):
        raise ValueError('Event settings must be a dictionary.')
    name = str(settings.get('name', '')).strip()
    if not name:
        raise ValueError('Give the CME event a name.')
    entries = settings.get('times')
    if not isinstance(entries, (list, tuple)) or not entries:
        raise ValueError('times must contain at least one UTC observation time.')
    frames = []
    for entry in entries:
        spec = entry if isinstance(entry, dict) else {'time': entry}
        date = utc_datetime(spec.get('time'))
        views = parse_views(spec.get('views', settings.get('views')))
        background = parse_background(spec.get('background', settings.get('background')))
        if any(background.date_for(date, view) is not None and background.date_for(date, view) >= date for view in views):
            raise ValueError('The background must be earlier than its target observation.')
        frames.append(Frame(date, views, background))
    if any(a.time >= b.time for a, b in zip(frames, frames[1:])):
        raise ValueError('List the observation times in chronological order without duplicates.')
    maximum = settings.get('max_time_offset_minutes', 10)
    if maximum is not None:
        maximum = float(maximum)
        if not math.isfinite(maximum) or maximum < 0:
            raise ValueError('max_time_offset_minutes must be nonnegative, or None to disable time warnings.')
    output = settings.get('output_directory')
    if output is None:
        slug = re.sub(r'[^A-Za-z0-9_.-]+', '_', name).strip('._')
        if not slug:
            raise ValueError('Choose an event name containing letters or numbers.')
        output = SESSION_ROOT / slug
    else:
        output = Path(output).expanduser()
        if not output.is_absolute():
            output = PROJECT_ROOT / output
    parameters = settings.get('initial_parameters')
    if parameters is not None:
        if set(parameters) != set(DEFAULT_PARAMETERS):
            raise ValueError('initial_parameters must specify lat, lon, tilt, height, half_angle and kappa.')
        parameters = {key: float(value) for key, value in parameters.items()}
        if not all(math.isfinite(value) for value in parameters.values()):
            raise ValueError('Initial parameters must be finite.')
        if not (-90 <= parameters['lat'] <= 90 and 0 <= parameters['lon'] <= 360 and
                -90 <= parameters['tilt'] <= 90 and 0 < parameters['height'] <= 70 and
                0 < parameters['half_angle'] < 90 and 0 < parameters['kappa'] < 1):
            raise ValueError('Initial parameters are outside the GUI parameter ranges.')
    return Event(name, tuple(frames), output.resolve(), maximum, parameters)
