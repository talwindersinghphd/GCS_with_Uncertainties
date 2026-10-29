"""The bundled, offline STEREO example and its original observation metadata."""
from datetime import datetime
import json

import numpy as np
from sunpy.map import Map

from .event_config import utc_datetime
from .paths import EXAMPLE_ROOT

DATE = datetime(2012, 7, 12, 17, 54)
BACKGROUND_DATE = datetime(2012, 7, 12, 16, 24)


def bundled_maps():
    result = {}
    with np.load(EXAMPLE_ROOT / 'data' / 'maps.npz', allow_pickle=False) as cache:
        if str(cache['format']) != 'gcs-fixed-normalization-v1':
            raise ValueError('Unknown bundled image format.')
        for spacecraft in ('STA', 'STB'):
            base = cache[spacecraft + '_base_data'].astype(float)
            target = cache[spacecraft + '_target_data'].astype(float)
            amean, astd, bmean, bstd = cache[spacecraft + '_coefficients']
            if astd <= 0 or bstd <= 0:
                raise ValueError('Invalid bundled image normalization.')
            result[spacecraft] = Map((target - bmean) / bstd - (base - amean) / astd,
                                     json.loads(str(cache[spacecraft + '_metadata'])))
    return result


def matches_bundled(frame):
    return (frame.time == DATE and frame.background.mode == 'fixed' and
            frame.background.time == BACKGROUND_DATE and
            all(view.spacecraft in ('STA', 'STB') and view.coronagraph == 'COR2' and
                not view.image and not view.background_image and not view.background_time and
                not view.image_folder and not view.background_folder
                for view in frame.views))


def bundled_annotations():
    return json.loads((EXAMPLE_ROOT / 'manual_traces.json').read_text())


def bundled_background_times():
    manifest = json.loads((EXAMPLE_ROOT / 'manifest.json').read_text())
    return {item['spacecraft']: utc_datetime(item['image_info']['date'])
            for item in manifest['images'] if item['role'] == 'base'}
