"""Per-time persistence and restoration of fits, annotations and accepted searches."""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from .event_config import utc_string
from .outline_uncertainty import PARAMETERS
from .search_scoring import display_boundaries
from .uncertainty_gui import export_result


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False))
    temporary.replace(path)


def annotation_signature(payload):
    # Worker count changes execution, not the saved scientific inputs.
    inputs = {key: payload[key] for key in ('images', 'reference', 'tier_outlines',
                                          'tier_tolerances', 'search_settings')}
    inputs['search_settings'] = dict(inputs['search_settings'])
    inputs['search_settings'].pop('cpus', None)
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def load_result(folder, images, date):
    """Restore accepted rows exactly; regenerate only the display silhouettes."""
    result = json.loads((folder / 'search.json').read_text())
    with np.load(folder / 'accepted_arrays.npz', allow_pickle=False) as data:
        result.update(accepted=data['parameters'], offsets=data['offsets'], errors=data['errors'])
    with (folder / 'accepted_all.csv').open(newline='') as file:
        rows = list(csv.DictReader(file))
    if rows and 'proposal_source' in rows[0]:
        result['accepted_sampling_labels'] = np.array([row['proposal_source'] for row in rows])
        result['accepted_history_cases'] = np.array([int(row['historical_case']) for row in rows])
    config = result['config']
    views = dict(enumerate(images))
    result['display_view_indices'] = list(views)
    def boundaries(parameters):
        return display_boundaries(parameters, date, views, config['resolution'], config.get('mesh_density', 1))
    result['reference_boundaries'] = boundaries(config['reference'])
    # Older exports saved only a small display sample. Rebuild from every
    # accepted row so those sessions also show the complete set of outlines.
    parameters = [dict(zip(PARAMETERS, row)) for row in result['accepted']]
    result['overlay_parameters'] = parameters
    result['overlay_keys'] = list(range(len(parameters)))
    result['overlays'] = [boundaries(values) for values in parameters]
    return result


def restore_search(window):
    state_file = window.session_folder / 'state.json'
    if not state_file.exists():
        return
    state = json.loads(state_file.read_text())
    export = state.get('search_export')
    panel = window._uncertainty
    if export and state.get('annotation_signature') == annotation_signature(panel.annotation_payload()):
        folder = (window.session_folder / export).resolve()
        if folder.parent != window.session_folder.resolve():
            raise ValueError('Saved search path must stay within its time-step folder.')
        result = load_result(folder, window._images, window._date)
        panel.finished(result)
        window.saved_search = (result, folder)


def persist(window):
    panel = window._uncertainty
    if panel.is_running():
        raise ValueError('Finish or cancel the search before saving or changing times.')
    panel.complete_overlay_preview()
    folder = window.session_folder
    folder.mkdir(parents=True, exist_ok=True)
    annotations = panel.annotation_payload()
    export = None
    if panel.result is not None:
        if window.saved_search is not None and window.saved_search[0] is panel.result:
            export = window.saved_search[1]
        else:
            export = export_result(panel.result, folder)
            window.saved_search = panel.result, export
        window.save_search_figures(export)
    write_json(folder / 'manual_fit.json', dict(date=utc_string(window._date),
        parameters=window.get_params_dict(), note='User manual fit; not an automated best-fit estimate.'))
    write_json(folder / 'manual_traces.json', annotations)
    write_json(folder / 'observations.json', window.observations)
    write_json(folder / 'state.json', dict(annotation_signature=annotation_signature(annotations),
        search_export=export.name if export else None,
        accepted_count=len(panel.result['accepted']) if panel.result is not None else 0,
        search_status=panel.result.get('stop_reason', 'complete') if panel.result is not None else 'not_run'))
    window._figure.savefig(str(folder / 'manual_fit.png'), dpi=150)
    write_summary(window.controller.event)


def write_summary(event):
    """One row per requested time, including unfinished time steps."""
    event.output_directory.mkdir(parents=True, exist_ok=True)
    write_json(event.output_directory / 'event.json', event.manifest())
    path = event.output_directory / 'time_series.csv'
    temporary = path.with_name(path.name + '.pending')
    with temporary.open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['time_utc'] + list(PARAMETERS) +
            ['accepted_count', 'search_status', 'search_export', 'observations'])
        writer.writeheader()
        for frame in event.frames:
            folder = event.output_directory / 'frames' / frame.key
            row = dict(time_utc=utc_string(frame.time), accepted_count=0, search_status='not_fitted')
            if (folder / 'manual_fit.json').exists():
                row.update(json.loads((folder / 'manual_fit.json').read_text())['parameters'])
                if (folder / 'state.json').exists():
                    state = json.loads((folder / 'state.json').read_text())
                    row.update({key: state[key] for key in ('accepted_count', 'search_status')})
                    if state.get('search_export'):
                        row['search_export'] = str(Path('frames') / frame.key / state['search_export'])
                if (folder / 'observations.json').exists():
                    row['observations'] = json.dumps(json.loads((folder / 'observations.json').read_text()))
            writer.writerow(row)
    temporary.replace(path)
