# Method and interpretation

## Manual reference and observed arcs

The six geometric parameters are latitude, longitude, tilt, height, half-angle and
aspect ratio. A manually fitted parameter vector remains the frozen reference for
each search. Observed arcs are drawn in each image's native pixel coordinates.
Tier 1 denotes clear sections; Tier 2 denotes less-certain sections. Confidence
assignments come from the observer.

The projected GCS surface is rasterized as a union of surface triangles. Its exterior
boundary is extracted, retaining concavity and filling enclosed holes. Neither a
convex hull nor the entire interior wireframe is used as the fitted outline. Image
crop edges are excluded from possible model boundaries.

For new searches both arcs and boundaries are converted through the image WCS
to projected solar radii before measuring distances or arc-length weights.
See [distance units](distance_units.md) for the projection and migration rules.
Distances go from the observed arc to the closest model exterior. Partial traces
therefore leave untraced model regions unconstrained. Within a view/tier, arcs are
resampled and pooled with arclength/trapezoidal weights; gaps are never bridged.

For performance the surface is rasterized within an integer-aligned bounding box
with an empty halo, retaining the original raster grid and boundary extraction.
Physical coordinate conversion and the nearest-boundary index are shared between
tiers in each view. A candidate rejected by one view needs no projection of later
views, because all view/tier checks must pass. Scores for skipped rejected views
are not computed; every accepted fit retains all component scores. These changes
preserve the one-way metric, mesh/raster resolution and acceptance rule.

## Independent baseline-relative acceptance

For every populated view/tier component, accept only when:

```text
E(candidate, view, tier) <= E(manual, view, tier) * (1 + extra_percent(view, tier) / 100)
```

All components must pass independently. There is no absolute error cap and no
requirement that even a clear boundary be exactly representable by GCS geometry.
The fresh defaults are 10% for the clear tier and 20% for the uncertain tier.
For example, a manual RMS of 0.05 RSun and 10% extra permits 0.055 RSun.
Each percentage is a user choice, not an automatically calibrated standard deviation.
A zero manual RMS permits only a zero candidate RMS. The manual fit is the user's
reference; the software does not assume it is a global optimum. RMS baselines stay
frozen throughout a search and are recomputed for the current manual fit on a new run.
Old configurations without an allowance-mode tag retain additive allowances.

## Historical proposals

The proposal file represents 137 fits of 56 curated matched events. It retains
signed latitude, shortest longitude, and shortest 180-degree-periodic geometric tilt
differences within each event. Missing tilt stays missing. Cases 3, 29 and 52 are
excluded pending convention review; notes remain in the file. The target date is
also excluded. July 12, 2012 is therefore held out of its own proposals and initial
angular search-scale calculation.

For each historical draw, select an eligible event uniformly and then a pair
uniformly within that event. Flip the joint angular vector together with probability
one half and smooth it with small independent Gaussian jitter. Full fit-to-fit
differences are used, not converted to single-fit standard errors. Missing tilt and
all shape parameters retain broad proposals. The requested broad-proposal fraction
is configurable from 0% to 100%; the default is 30%. Bounds rejection changes the frequencies after
initial event-uniform selection; retry/fallback counts are reported.

This is a proposal mechanism, not a hierarchical posterior fit or a correction for
catalog bias. No tilt-speed error correlation is inferred from this dataset, which
has no events with two complete four-parameter reconstructions.

## Diagnostics and limitations

- Inspect the reference scores and overlays before sampling.
- Widen search bounds if compatible candidates approach their edges.
- Compare broad-only and mixed proposals, seeds, and sample sizes.
- Check mesh and raster resolution, especially for small allowances.
- Outline scores are in projected solar radii, independent of pixel scale; extra
  allowances are percentages of each component's manual score. Different boundary
  clarity can still require different percentages.
  The rasterized silhouette retains finite resolution, so numerical convergence
  must still be checked.
- Compatibility ranges are conditional on the drawn arcs, the GCS shape family,
  the sampling choices and the selected observations. They do not include all CME
  or background-solar-wind uncertainties.

Longitude is wrapped modulo 360 and geometric tilt modulo 180. Angular summaries
use continuous offsets around the manual reference. Magnetic polarity is not
inferred. Speed requires a time series; poloidal flux requires magnetic information.
