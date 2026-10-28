"""Separate, baseline-relative outline checks for each view and confidence tier."""
import numpy as np
from scipy.spatial import cKDTree
from distance_metric import PHYSICAL_UNIT, ProjectedDistance, score_unit
from outline_uncertainty import resample_trace, outline_error

TIERS = ('1', '2')
COLORS = {'1': 'cyan', '2': '#ffcc33'}


def sample_arcs(arcs):
    """Arclength quadrature over disconnected arcs, never joining their gaps."""
    points, weights = [], []
    for arc in arcs:
        if not len(arc):
            continue
        raw = np.asarray(arc, dtype=float)
        sampled = resample_trace(raw)
        length = np.linalg.norm(np.diff(raw, axis=0), axis=1).sum()
        w = np.full(len(sampled), length / (len(sampled) - 1))
        w[[0, -1]] *= .5
        points.append(sampled)
        weights.append(w)
    if not points:
        raise ValueError('Draw at least one arc with two distinct points.')
    return np.concatenate(points), np.concatenate(weights)


def weighted_error(sample, boundary):
    points, weights = sample
    distances = cKDTree(boundary).query(points)[0]
    return float(np.sqrt(np.average(distances ** 2, weights=weights)))


def prepare_checks(config, images=None):
    """Each check is independent; no tier can compensate for another tier."""
    checks = []
    physical = score_unit(config) == PHYSICAL_UNIT
    metrics = {}
    def convert(view, points):
        if not physical:
            return points
        view = int(view)
        if images is None or view not in images:
            raise ValueError('Physical scoring needs the observation map for every traced view.')
        if view not in metrics:
            metrics[view] = ProjectedDistance(images[view])
        return metrics[view].transform(points)
    if 'tier_outlines' not in config:
        for view, points in sorted(config['outlines'].items(), key=lambda p: int(p[0])):
            checks.append(dict(key=int(view), view=int(view), tier=None,
                sample=resample_trace(convert(view, points)), metric=metrics.get(int(view)), allowance=float(config['tolerances'][str(view)])))
        return checks
    for view, tiers in sorted(config['tier_outlines'].items(), key=lambda p: int(p[0])):
        nonempty = {tier: [a for a in arcs if len(a)] for tier, arcs in tiers.items() if any(len(a) for a in arcs)}
        if any(t not in TIERS for t in nonempty):
            raise ValueError('Tier must be 1 or 2.')
        allowances = config['tier_tolerances'][str(view)]
        if '1' in nonempty and '2' in nonempty and allowances['2'] < allowances['1']:
            raise ValueError('Tier 2 must have at least as much additional flexibility as Tier 1.')
        for tier in TIERS:
            if tier not in nonempty:
                continue
            allowance = float(allowances[tier])
            if not np.isfinite(allowance) or allowance < 0:
                raise ValueError('Tier allowances must be finite and nonnegative.')
            checks.append(dict(key='view_{}_tier_{}'.format(view, tier), view=int(view), tier=tier,
                               sample=sample_arcs([convert(view, arc) for arc in nonempty[tier]]),
                               metric=metrics.get(int(view)), allowance=allowance))
    if not checks:
        raise ValueError('Draw at least one outline arc with two distinct points.')
    return checks


def check_error(check, boundary):
    if check.get('metric') is not None:
        boundary = check['metric'].transform(boundary)
    return weighted_error(check['sample'], boundary) if check['tier'] is not None else outline_error(check['sample'], boundary)


def passes_checks(scores, baseline, checks):
    if len(scores) != len(checks):
        raise ValueError('Every view/tier needs a score.')
    return all(np.isfinite(score) and score <= baseline[check['key']] + check['allowance']
               for score, check in zip(scores, checks))


def move_segment(tiers, source_tier, arc_index, segment_index, target_tier):
    """Move one edge without inventing bridges or discarding adjacent edges."""
    if source_tier == target_tier:
        return
    arc = tiers[source_tier][arc_index]
    if not 0 <= segment_index < len(arc) - 1:
        raise ValueError('Invalid segment index.')
    edge = arc[segment_index:segment_index + 2]
    pieces = [p for p in (arc[:segment_index + 1], arc[segment_index + 1:]) if len(p) >= 2]
    tiers[source_tier][arc_index:arc_index + 1] = pieces
    tiers.setdefault(target_tier, []).append(edge)
