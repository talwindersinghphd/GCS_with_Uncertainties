"""Edit EVENT below, then run: python launch.py"""
import argparse
import json
from pathlib import Path


# All times are UTC. Add as many times and observation views as you need.
# A view is a spacecraft/coronagraph combination; one spacecraft can appear repeatedly.
# In 'times', enter quoted dates in chronological order: 'YYYY-MM-DD HH:MM'.
# In 'views', add one dictionary per instrument, using these exact download codes:
#   Spacecraft code   Spacecraft                  Allowed 'coronagraph' codes
#   'STA'             STEREO-A (Ahead)            'COR1', 'COR2', 'COR2beacon'
#   'STB'             STEREO-B (Behind)           'COR1', 'COR2', 'COR2beacon'
#   'SOHO'            SOHO / LASCO                'C2', 'C3'
#   'HISTA'           STEREO-A / HI-1             'HI'
#   'HISTB'           STEREO-B / HI-1             'HI'
# Example: {'spacecraft': 'SOHO', 'coronagraph': 'C3'},
# To use C2 and C3 together, add separate 'SOHO' entries, one for each coronagraph.

EVENT = {
    'name': 'july12_2012',
    'times': [
        '2012-07-12 17:54',
        # '2012-07-12 18:24',
        # '2012-07-12 18:54',
    ],
    'views': [
        {'spacecraft': 'STA', 'coronagraph': 'COR2'},
        {'spacecraft': 'STB', 'coronagraph': 'COR2'},
        {'spacecraft': 'SOHO', 'coronagraph': 'C2'},
        # {'spacecraft': 'SOHO', 'coronagraph': 'C2'},
        # {'spacecraft': 'SOHO', 'coronagraph': 'C3'},
        # Images download automatically. To select the nearest local FITS image:
        # {'spacecraft': 'SOHO', 'coronagraph': 'C3',
        #  'image_folder': '/path/to/C3_fits/'},
        # Subfolders are searched using FITS observation times, before AND after
        # each requested time. Use one instrument's folder per view; available
        # spacecraft/detector headers are checked to avoid mixing instruments.
        # The same folder supplies the image nearest the requested background time,
        # restricted to exposures before the selected target image.
        # Optional: 'background_folder': '/path/to/background_fits/' for another folder.
        # Explicit files still work: 'image': '/path/to/image.fits' and
        # 'background_image': '/path/to/background.fits'. Paths may also use
        # '/path/to/C3_{time:%Y%m%d_%H%M%S}.fits' for an exact filename template.
        # Background mode 'raw' below skips subtraction.
        # Local FITS files need helioprojective WCS and observer metadata (see README).
    ],
    'background': {'mode': 'fixed', 'time': '2012-07-12 16:24'},
    # Alternatives: {'mode': 'running', 'minutes': 60} or {'mode': 'raw'}.
    'max_time_offset_minutes': 20,  # Show farther images with red time warnings; None disables warnings.
    'output_directory': None,  # None means sessions/<event name>/.
    'initial_parameters': None,  # None uses the example, saved fit, or neutral defaults.
}


def main():
    parser = argparse.ArgumentParser(description='Launch a GCS_with_Uncertainties time series.')
    parser.add_argument('--config', type=Path, help='Read event settings from a JSON file instead of EVENT above.')
    parser.add_argument('--check-config', action='store_true', help='Validate and print the event schedule without opening the GUI or downloading images.')
    args = parser.parse_args()
    from src.event_config import parse_event
    try:
        settings = json.loads(args.config.read_text()) if args.config else EVENT
        event = parse_event(settings)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    if args.check_config:
        print(json.dumps(event.manifest(), indent=2))
        return
    from src.timeseries import run_event
    run_event(event)


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
