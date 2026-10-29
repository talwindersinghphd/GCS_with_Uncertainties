"""Histograms of all accepted GCS geometries, with continuous angular axes."""
import numpy as np
from matplotlib.ticker import MaxNLocator

from outline_uncertainty import PARAMETERS

PANELS = (
    ('half_angle', 'Half angle', 'Degrees'),
    ('height', 'Apex height', 'RSun'),
    ('kappa', 'Aspect ratio', 'Dimensionless'),
    ('lat', 'Latitude', 'Degrees'),
    ('lon', 'Longitude', 'Degrees (unwrapped)'),
    ('tilt', 'Tilt', 'Degrees (unwrapped)'),
)


def draw_parameter_histograms(figure, result=None):
    """Plot every retained accepted fit from the current search."""
    if result is None or len(result['accepted']) == 0:
        figure.clear()
        figure._parameter_histogram_state = None
        figure.set_constrained_layout(True)
        message = ('Run uncertainty search to see accepted parameter histograms.' if result is None else
                   'Search running — waiting for the first accepted fit.' if result.get('running', False) else
                   'No fits were accepted. There are no parameter histograms to display.')
        figure.text(.5, .5, message, ha='center', va='center', wrap=True, fontsize=12)
        return

    state = getattr(figure, '_parameter_histogram_state', None)
    if state is None or len(figure.axes) != len(PANELS) or any(axis not in figure.axes for axis in state['axes']):
        figure.clear()
        figure.set_constrained_layout(True)
        axes = figure.subplots(2, 3).ravel()
        state = dict(axes=axes, bars=[None] * len(PANELS), reference_lines=[])
        for axis, (key, title, unit) in zip(axes, PANELS):
            state['reference_lines'].append(axis.axvline(0, color='#c74936', linestyle='--', linewidth=1.5))
            axis.set_title(title, fontsize=11)
            axis.set_xlabel(unit)
            axis.set_ylabel('Fraction')
            axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
            axis.xaxis.set_major_locator(MaxNLocator(nbins=3))
            axis.ticklabel_format(axis='x', style='plain', useOffset=False)
            axis.tick_params(labelsize=9)
            axis.grid(axis='y', alpha=.2)
            axis.set_axisbelow(True)
        figure._parameter_histogram_state = state

    values = np.asarray(result['accepted'])
    offsets = np.asarray(result['offsets'])
    reference = result['config']['reference']
    count = len(values)
    partial = result.get('cancelled', False) or result.get('stop_reason') == 'proposal_limit'
    title = ('Search running — {:,} accepted fits so far'.format(count) if result.get('running', False) else
             '{}All {:,} accepted fits'.format('Partial search — ' if partial else '', count))
    figure.suptitle(title + '\nDashed red line: manual fit', fontsize=13)
    for panel, (axis, (key, _, _)) in enumerate(zip(state['axes'], PANELS)):
        index = PARAMETERS.index(key)
        # Match the exported summary: avoid splitting a narrow distribution
        # across the equivalent 0/360 longitude or -90/90 tilt boundaries.
        samples = reference[key] + offsets[:, index] if key in ('lon', 'tilt') else values[:, index]
        bins = min(40, max(5, int(np.ceil(np.sqrt(count)))))
        limits = None
        if np.ptp(samples) == 0:
            width = max(abs(float(samples[0])) * .01, .001)
            limits = (samples[0] - width, samples[0] + width)
            bins = 1
        counts, edges = np.histogram(samples, bins=bins, range=limits)
        fractions = counts / float(count)
        bars = state['bars'][panel]
        if bars is None or len(bars) != bins:
            if bars is not None:
                bars.remove()
            bars = axis.bar(edges[:-1], fractions, width=np.diff(edges), align='edge',
                            color='#377eb8', edgecolor='white', linewidth=.5)
            state['bars'][panel] = bars
        else:
            for bar, left, width, fraction in zip(bars, edges[:-1], np.diff(edges), fractions):
                bar.set_x(left)
                bar.set_width(width)
                bar.set_height(fraction)
        state['reference_lines'][panel].set_xdata([reference[key], reference[key]])
        axis.relim()
        axis.autoscale_view()
