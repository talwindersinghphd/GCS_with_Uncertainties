# GCS_with_Uncertainties

Interactive CME reconstruction with manual GCS fitting, two confidence tiers of
observed outlines, and history-guided searches for compatible geometries.

![July 12, 2012 example](docs/images/july12_two_tiers.png)

This is a separate project based on
[Talwinder Singh's GCS_Python](https://github.com/talwindersinghphd/GCS_Python),
which derives from [Johan von Forstner's Python GCS implementation](https://github.com/johan12345/gcs_python)
of the Thernisien GCS model. It preserves the manual fitting workflow and adds
outline-based uncertainty exploration.

## What it does

- Overlay a manually adjusted GCS mesh on coronagraph images.
- Draw **Tier 1** arcs on clear boundaries and **Tier 2** arcs on less-certain sections.
- Reassign existing segments between tiers without redrawing them.
- Keep disconnected arcs separate and weight their errors by arclength.
- Explore all six geometric parameters with a mixture of historical fit differences
  and broad sampling, or broad sampling alone.
- Export all compatible parameter vectors, per-view/tier errors, diagnostics, and
  a reproducible ensemble of the requested size (512 fits by default).

The rule is deliberately **relative to your manual fit**, separately for every view/tier:

```text
candidate RMS <= manual RMS for the same view/tier + additional allowance
```

Every check must pass. Better Tier 2 agreement cannot compensate for a Tier 1
failure. There is no new absolute distance threshold. Initial additional allowances
are +0.05 solar radii for Tier 1 and +0.20 for Tier 2; both are editable starting
values, not measured uncertainties. Scores and arc-length weights use the observer's
projected Sun-centred plane in fixed solar radii (1 RSun = 695700 km). Each map's
helioprojective WCS and `DSUN_OBS` are required; unequal pixel scales and rotations
are handled before measuring distances. This is a projected distance, not a 3D
CME separation. See [distance units](docs/distance_units.md).

The bundled example uses these physical-distance defaults. Older pixel-based
annotations can still be loaded with per-image unit conversion.

## Install

Python 3.11 is recommended. Conda provides the native OpenJPEG library used when
reading JPEG2000 coronagraph images:

```sh
git clone https://github.com/talwindersinghphd/GCS_with_Uncertainties.git
cd GCS_with_Uncertainties
conda env create -f environment.yml
conda activate GCS_with_Uncertainties
```

The GUI needs a graphical desktop. On Ubuntu/Debian, install the native display
and image libraries if they are not already present:

```sh
sudo apt-get update
sudo apt-get install libegl1 libopengl0 libxkbcommon-x11-0 libopenjp2-7
```

If you already have the native image libraries and a suitable Python environment:

```sh
python -m pip install -r requirements.txt
```

## Try the saved July 12, 2012 example

```sh
python fit_july12_2012.py
```

The project includes the saved manual fit, both confidence tiers, real STEREO-A/B
COR2 images, and canonical processed image arrays with observer metadata. The
canonical arrays let the example run offline without requiring a JPEG2000 decoder.
Inputs are copied into ignored `sessions/july12_2012/` before editing, preserving
the tracked example.

1. Start in **Draw Outline**, choose a view and tier, then draw or reassign segments.
2. Use **New arc** across gaps, **Undo point** to remove the last traced point,
   and **Delete arc** to remove the selected arc.
3. Switch to **Manual fit** to adjust the model to your outlines.
4. Open **Uncertainty**. Choose **CPUs to use** (1 for sequential scoring) and **Target accepted fits**,
   adjust **History-guided** / **Broad search**, then **Run uncertainty search**.
   Defaults are up to 4 CPUs, 512 accepted fits, and a 70% / 30% proposal mix.
   Live counts show fits tried and accepted against the target; the same button
   becomes **Cancel search** while it runs.
5. In **Uncertainty**, choose **Save** to save the July 12 session and completed
   search results. **Manual fit** also retains its Save button.

The figure area has **Coronagraphs** and **Accepted parameter histograms** tabs.
During or after a search, switch to the histogram tab to inspect all six parameters
across all retained accepted fits. While visible, it updates at most once a second
and shows the accepted-fit count so far. Bars show the fraction of accepted fits
in each bin and sum to 1 for each parameter. Dashed lines mark the
manual fit; longitude and tilt are unwrapped around it. The figure toolbar follows
the selected tab. Save also writes `accepted_parameter_histograms.png` into the
search export folder. Changing the fit or outlines clears the old histograms.

The controls have three tabs: **Draw Outline** (opened by default), **Manual fit**,
and **Uncertainty**. Drawing and segment assignment automatically turn off
toolbar pan/zoom while keeping the current zoom. An explicit mode label and
**Stop tracing** / **Stop assigning segments** button show when editing is active.
Selecting pan/zoom or leaving the outline view ends editing.
The last tab contains search settings, linked mixture
percentages, editable scales and multiplier, and a read-only **Historical difference**
column for reference. Latitude, longitude and tilt show the historical differences
beside their editable scales; these references stay fixed when scales or the
multiplier change. Height, half-angle and κ have blank historical cells and keep
their exploratory default scales. New CME sessions start with a half-angle scale
of 5.0° and a κ scale of 0.05. The search half-width is scale × multiplier. Zero
fixes a parameter. The **Extra RMS allowance [RSun]** table lets you edit Tier 1 and
Tier 2 acceptance allowances separately for each view. **Defaults** resets every
view to +0.05 / +0.20 projected solar radii. Saved sessions retain their existing
allowances. Settings are saved with the session and search. Numerical resolution
remains internal. Run, Save and progress stay
visible below the settings so small windows need no scrolling to reach the actions.
Save exports available results into a new
`gcs_uncertainty_*` folder inside the session, including accepted fits and parameter
summaries. Save without running a search still saves the fit and outlines.

Accepted outline examples appear on the coronagraphs during the search, with a
preview refresh at most twice a second. The display keeps up to 20 representative
accepted outlines in rose pink; the thicker bright pink outline is the manual
fit. Existing scoring results are reused, and plot artists are updated in place.
Preview painting pauses on the histogram tab and catches up when you return.
All accepted fits remain in the live histograms and saved results. Histogram axes
and bars are reused between updates; hidden histograms catch up on reopening.

The mixture percentages sum to 100% and can be set anywhere from 0% to 100%.
History guides the angular offsets; height, half-angle and aspect ratio remain
broadly sampled. Broad search covers the configured parameter ranges around the
manual fit. Bounded historical retries can fall back to broad proposals, and
filtering/early stopping can change actual proportions. At 100% broad search the
history sampler is bypassed. Save each run before changing the mix to keep its
settings and histogram image for comparison; changing the mix clears current results.

The search generates independently scrambled Sobol batches with a recorded seed
per batch, but stops scoring as soon as the first requested number of accepted
fits is established in candidate order. It stops submitting work, lets fits already
running finish, and excludes later matches from the retained results. It does not
finish the rest of the batch. `selected_fits.csv` and `accepted_all.csv` contain the
retained fits, exactly the requested number when the target is reached.
`parameter_summary.csv` describes those same retained fits.
The default safety budget is 65,536 proposed candidates; if that limit is reached
first, the GUI reports the shortfall and exports the smaller, partial ensemble.
Ranges and allowances stay fixed throughout. Historical mixing and filtering do
not preserve pure Sobol balance in the accepted fits; stopping mid-batch also
means the evaluated candidates are not a complete Sobol design.

Parallel searches use separate worker processes and preserve the candidate order
in saved results, so a completed search gives the same accepted fits for the same
settings and seed regardless of CPU count. The tried count can differ slightly
because of work already in flight. Cancellation stops new submissions and
waits for fits already running; partial results can be saved. The CPU setting is
saved with the session and capped to the CPUs available when reopening it.

Scoring rasterizes only the occupied model region on the original pixel grid,
shares each view's distance calculation between tiers, and stops evaluating a
candidate's later views once an earlier view rejects it. CPU workers are reused
between Sobol batches. Mesh/raster resolution and acceptance thresholds are
unchanged. Actual search time also
depends on how many candidates must be tried to reach the accepted-fit target.

The example observations are at 17:54:00 and 17:54:26 UTC. A non-simultaneous SOHO
frame is intentionally excluded. The saved manual fit and outlines illustrate
the workflow; they are not a validated forecast or a target for other events.

[Detailed tier-editing guide](docs/two_tier_workflow.md) ·
[Method and interpretation](docs/method.md) ·
[Example provenance](examples/july12_2012/README.md)

## Other CMEs

```sh
python gui.py "2012-07-12 17:54" STA STB -stereo COR2 -rd
```

The general GUI downloads images through the inherited Helioviewer/solar-data
workflow and needs network access. Cached images and saved sessions stay out of
Git. The saved July 12 example can be run without a network connection.

## Interpretation

These are **outline-compatible parameter ranges**, not calibrated confidence or
credible intervals. Frequencies depend on the proposal distribution, bounds, and
allowances. Widen bounds when accepted fits approach their limits and assess
sampling, mesh, and raster convergence.

This single-time reconstruction estimates neither speed uncertainty nor poloidal
flux. Speed needs multiple height-time reconstructions; magnetic flux needs separate
observational constraints. Preserve complete accepted vectors when using their
joint geometric relationships.

## Attribution

See [NOTICE](NOTICE) and [LICENSE](LICENSE). Historical starting information comes
from the matched GCS reconstructions used in Singh et al. (2022),
[ApJ 933, 123](https://doi.org/10.3847/1538-4357/ac73f3). Observer/catalog disagreement
is not ground-truth reconstruction error.
