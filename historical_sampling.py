"""Empirical, event-weighted proposals for alternative GCS reconstructions.

Historical differences guide exploration, not probabilities of true parameters.
Every search excludes its own calendar event date and all review-flagged events.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

from outline_uncertainty import PARAMETERS, search_candidates

DEFAULT_HISTORY = Path(__file__).with_name('historical_gcs_proposals.json')


def suggested_scales(events):
    """Mean eventwise pairwise angular range, for search sizing, not sigmas."""
    result = {}
    for j, key in enumerate(PARAMETERS[:3]):
        ranges = [max(abs(p['delta'][j]) for p in event['pairs'] if p['delta'][j] is not None)
                  for event in events if any(p['delta'][j] is not None for p in event['pairs'])]
        if ranges:
            result[key] = float(np.mean(ranges))
    return result


def load_history(path=DEFAULT_HISTORY, exclude_date=None):
    path = Path(path)
    content = path.read_bytes()
    history = json.loads(content)
    if history.get('format') != 'gcs-fit-differences-v1':
        raise ValueError('Unsupported historical proposal file.')
    events, excluded = [], []
    for event in history['events']:
        if event.get('review_required') or event['date'] == exclude_date:
            excluded.append(dict(case=event['case'], date=event['date'],
                                 reason='target event' if event['date'] == exclude_date else 'convention review'))
            continue
        pairs = event['pairs']
        if not pairs:
            continue
        for pair in pairs:
            values = pair['delta']
            if len(values) != 3 or not all(v is None or np.isfinite(v) for v in values):
                raise ValueError('Invalid historical difference vector.')
            if values[0] is None or values[1] is None:
                raise ValueError('Historical proposal requires latitude and longitude.')
        events.append(event)
    if not events:
        raise ValueError('No eligible historical events remain after exclusions.')
    return events, dict(file=path.name, sha256=hashlib.sha256(content).hexdigest(),
                       eligible_events=len(events), excluded=excluded,
                       source=history['source'], method=history['method'])


def guided_candidates(reference, scales, multiplier, exponent, seed,
                      history_path=DEFAULT_HISTORY, exclude_date=None,
                      broad_fraction=0.30, smoothing=0.10):
    """Bounded mixture: broad Sobol proposals plus smoothed empirical differences.

    Select event uniformly, then a fit pair uniformly within that event. Flip the
    whole angular vector together with probability 1/2. Retain full fit-to-fit
    differences (not divided by sqrt(2)). Missing tilt stays broadly sampled.
    Non-angular parameters keep their broad proposals. Search scales define both
    the bounds and the independent kernel smoothing widths. Reject outside-box
    proposals instead of clipping; bounded retries fall back to broad sampling.
    """
    if not 0 <= broad_fraction <= 1 or not 0 <= smoothing <= 1:
        raise ValueError('Broad fraction must be 0..1; smoothing must be 0..1.')
    # Generate unfiltered proposals first so geometry rejection is applied once
    # after mixing. This also performs the standard range validation.
    _, _, _, halfwidth = search_candidates(reference, scales, multiplier, exponent, seed)
    from scipy.stats import qmc
    unit = qmc.Sobol(d=6, scramble=True, seed=seed).random_base2(exponent)
    offsets = (2 * unit - 1) * halfwidth
    events, metadata = load_history(history_path, exclude_date)
    rng = np.random.default_rng(seed)
    total = len(offsets)
    count = int(round(total * (1 - broad_fraction))) if np.any(halfwidth[:3] > 0) else 0
    selected = rng.permutation(total)[:count]
    labels = np.full(total, 'broad', dtype='U32')
    source_cases = np.full(total, -1, dtype=int)
    outside, fallbacks, with_tilt = 0, 0, 0
    jitter = smoothing * np.array([scales[k] for k in PARAMETERS[:3]])
    for index in selected:
        for attempt in range(64):
            event = events[int(rng.integers(len(events)))]
            pair = event['pairs'][int(rng.integers(len(event['pairs'])))]
            delta = offsets[index, :3].copy()
            sign = rng.choice([-1, 1])
            for j, value in enumerate(pair['delta']):
                if halfwidth[j] == 0:
                    delta[j] = 0
                elif value is not None:
                    delta[j] = sign * value + rng.normal(0, jitter[j])
            if np.any(np.abs(delta) > halfwidth[:3] + 1e-12):
                outside += 1
                continue
            offsets[index, :3] = delta
            labels[index] = 'history-angular' if pair['delta'][2] is not None else 'history-direction'
            source_cases[index] = event['case']
            with_tilt += int(pair['delta'][2] is not None)
            break
        else:
            fallbacks += 1
    values = np.array([reference[k] for k in PARAMETERS]) + offsets
    values[:, 1] %= 360
    values[:, 2] = (values[:, 2] + 90) % 180 - 90
    valid = (np.isfinite(values).all(axis=1) & (np.abs(values[:, 0]) <= 90) &
             (values[:, 3] > 0) & (values[:, 4] > 0) & (values[:, 4] < 90) &
             (values[:, 5] > 0) & (values[:, 5] < 1))
    metadata.update(broad_fraction_requested=broad_fraction, smoothing_fraction=smoothing,
                    historical_rows_proposed=count - fallbacks,
                    historical_rows_with_joint_tilt=with_tilt,
                    outside_box_attempts=outside, broad_fallback_rows=fallbacks,
                    target_excluded_date=exclude_date,
                    interpretation='Symmetric fit-to-fit proposal mixture, not a calibrated posterior. '
                                   'Filtering and truncation change frequencies; no importance correction is applied.')
    return (values[valid], offsets[valid], int((~valid).sum()), halfwidth,
            labels[valid], source_cases[valid], metadata)
