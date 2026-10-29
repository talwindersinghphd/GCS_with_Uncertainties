"""Find local coronagraph exposures by FITS observation time, reading headers only."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re
from concurrent.futures import CancelledError

import numpy as np
from astropy.io import fits
from astropy.time import Time
from sunpy.map import Map
from sunpy.time import parse_time


FITS_SUFFIXES = ('.fits', '.fit', '.fts', '.fits.gz', '.fit.gz', '.fts.gz',
                 '.fits.fz', '.fit.fz', '.fts.fz', '.fz')
DATE_KEYS = ('DATE-OBS', 'DATE_OBS', 'T_OBS', 'DATE-AVG', 'DATE_AVG',
             'DATE-BEG', 'DATE_BEG', 'DATE-END', 'DATE_END')


def normalized(value):
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def spacecraft_code(header):
    values = [normalized(header.get(key, '')) for key in
              ('OBSRVTRY', 'OBSERVAT', 'OBSERVATORY', 'SPACECRAFT', 'TELESCOP')]
    for value in values:
        if value in ('STA', 'AHEAD', 'HISTA') or value.startswith(('STEREOA', 'STEREOAHEAD')):
            return 'STA'
        if value in ('STB', 'BEHIND', 'HISTB') or value.startswith(('STEREOB', 'STEREOBEHIND')):
            return 'STB'
        if value.startswith('SOHO'):
            return 'SOHO'
    return next((value for value in values if value and value not in ('STEREO', 'SECCHI', 'LASCO')), None)


def detector_code(value):
    value = normalized(value)
    match = re.search(r'COR[12]|HI[12]|C[23]', value)
    return match.group() if match else value


def header_detector(header):
    for key in ('DETECTOR', 'CAMERA', 'INSTRUME', 'INSTRUMENT'):
        value = detector_code(header.get(key, ''))
        if value in ('COR1', 'COR2', 'HI1', 'HI2', 'C2', 'C3'):
            return value
    return None


def header_time(header, key=None):
    """Read full dates, split DATE/TIME headers, and numerical FITS dates in UTC."""
    scale = str(header.get('TIMESYS', 'UTC')).strip().lower()
    scale = {'ut': 'utc', 'gmt': 'utc', 'tdt': 'tt'}.get(scale, scale)
    for name in (key,) if key else DATE_KEYS:
        value = header.get(name)
        if value is None or not str(value).strip():
            continue
        value = str(value).strip()
        if name in ('DATE-OBS', 'DATE_OBS') and re.fullmatch(r'\d{4}[-/]\d{2}[-/]\d{2}', value):
            clock = header.get('TIME-OBS', header.get('TIME_OBS'))
            if clock:
                value += 'T' + str(clock).strip()
        time = parse_time(value) if 'TAI' in value.upper() else parse_time(value, scale=scale)
        time = time.utc
        time.precision = 9
        return time
    if key is None:
        for name, fmt in (('MJD-OBS', 'mjd'), ('MJD_OBS', 'mjd'), ('JD-OBS', 'jd')):
            if header.get(name) is not None:
                time = Time(float(header[name]), format=fmt, scale=scale).utc
                time.precision = 9
                return time
    raise ValueError('Missing FITS observation date/time.')


@dataclass(frozen=True)
class Exposure:
    path: str
    hdu: int
    timestamp: float
    utc: str
    spacecraft: str
    detector: str


@lru_cache(maxsize=8192)
def header_exposures(path, modified_ns, size):
    """Cache header records by file version; never cache full image arrays."""
    exposures = []
    try:
        with fits.open(path, memmap=False, lazy_load_hdus=True) as hdus:
            primary = hdus[0].header
            for index, hdu in enumerate(hdus):
                if hdu.header.get('NAXIS') != 2 or min(hdu.header.get('NAXIS1', 0), hdu.header.get('NAXIS2', 0)) < 2:
                    continue
                header = primary.copy()
                header.update(hdu.header)
                try:
                    time = header_time(header)
                except (ValueError, TypeError):
                    continue
                exposures.append(Exposure(path, index, float(time.unix), time.isot,
                                          spacecraft_code(header), header_detector(header)))
    except (OSError, ValueError, TypeError):
        pass
    return tuple(exposures)


def closest_exposure(folder, view, requested, before=None, progress=None, cancelled=None):
    if cancelled is not None and cancelled.is_set():
        raise CancelledError()
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError('FITS folder does not exist: ' + str(folder))
    target = float(Time(requested, scale='utc').unix)
    latest = float(Time(before).utc.unix) if before is not None else None
    spacecraft = {'HISTA': 'STA', 'HISTB': 'STB'}.get(view.spacecraft, normalized(view.spacecraft))
    detector = 'HI1' if view.coronagraph == 'HI' else detector_code(view.coronagraph)
    best, best_key = None, None
    scanned = 0
    for path in folder.rglob('*'):
        if cancelled is not None and cancelled.is_set():
            raise CancelledError()
        if not path.is_file() or not path.name.lower().endswith(FITS_SUFFIXES):
            continue
        scanned += 1
        if progress is not None and (scanned == 1 or scanned % 100 == 0):
            progress('Scanning local FITS for {}: {} files'.format(view.label, scanned))
        try:
            stat = path.stat()
        except OSError:
            continue
        for exposure in header_exposures(str(path), stat.st_mtime_ns, stat.st_size):
            if exposure.spacecraft is not None and exposure.spacecraft != spacecraft:
                continue
            if exposure.detector is not None and exposure.detector != detector:
                continue
            if latest is not None and exposure.timestamp >= latest:
                continue
            # An exact tie prefers the earlier exposure, then a stable filename/HDU.
            key = abs(exposure.timestamp - target), exposure.timestamp, exposure.path, exposure.hdu
            if best_key is None or key < best_key:
                best, best_key = exposure, key
    if best is None:
        suffix = ' before the selected target image' if before is not None else ''
        raise ValueError('No usable FITS images for {}{} in {}. Check observation dates and instrument headers.'.format(
            view.label, suffix, folder))
    return best


def exposure_map(exposure):
    with fits.open(exposure.path, memmap=False) as hdus:
        header = hdus[0].header.copy()
        header.update(hdus[exposure.hdu].header)
        data = np.array(hdus[exposure.hdu].data, copy=True)
    # Canonical UTC dates also support files with separate DATE-OBS/TIME-OBS
    # or numerical dates without relying on an instrument-specific Map subclass.
    metadata = dict(header)
    for key in DATE_KEYS:
        if key in header:
            try:
                metadata[key] = header_time(header, key).isot
            except (ValueError, TypeError):
                pass
    metadata['DATE-OBS'] = exposure.utc
    metadata['TIMESYS'] = 'UTC'
    for key, value in metadata.items():
        if not isinstance(value, (str, int, float, bool, type(None), np.generic)):
            metadata[key] = str(value)
    return Map(data, metadata)
