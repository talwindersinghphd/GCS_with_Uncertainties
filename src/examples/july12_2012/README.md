# July 12, 2012 worked example

This example contains Talwinder Singh's manual fit and two-tier outlines produced
while developing this tool. It is an illustration, not a ground-truth reconstruction.

- STEREO-A COR2: 2012-07-12 17:54:00 UTC.
- STEREO-B COR2: 2012-07-12 17:54:26 UTC.
- Pre-event backgrounds: approximately 16:24 UTC.
- SOHO is excluded because the available C3 image was about 48 minutes earlier.
- Image source: Helioviewer API, STEREO/SECCHI COR2, NRL.

`manifest.json` records source IDs, observation metadata, original filenames and
checksums. `data/` contains the original JPEG2000 files plus a canonical, safely loaded
NumPy cache of decoded pixels, fixed normalization coefficients, and observer metadata.
It reconstructs the standardized pre-event differences without decoding JP2 files.
Canonical arrays avoid decoder and floating-point normalization differences when
loading the saved native-pixel annotations in another environment. No pickle is used.

`manual_fit.json` contains the manual parameters. `manual_traces.json` contains the
two confidence tiers, additional allowances and saved search settings. The launcher
opens these fixtures with `python launch.py` from the project root. Edits are
saved to ignored `sessions/july12_2012/frames/<UTC time>/`.

The bundled annotations use `gcs-outlines-v3` with projected solar-radius units.
Its saved additive allowances are +0.05 RSun for Tier 1 and +0.20 RSun for
Tier 2 in each view. The GUI converts them to equivalent percentages of each
component's saved manual RMS; **Defaults** switches to 10% / 20%. New session
annotations use version 4 with an explicit percentage tag. The original fixtures
stay unchanged. The half-angle search scale is 5.0° and the κ scale is 0.05.
All can be edited in Uncertainty and saved with the local session.

Run an uncertainty search to generate accepted fits, live overlays and parameter
histograms. Search results are written to your local session when you choose Save;
generated runs are not part of the bundled example. CSV columns identify physical
errors as `rms_rsun_*`, with distance geometry and settings recorded in `search.json`.
