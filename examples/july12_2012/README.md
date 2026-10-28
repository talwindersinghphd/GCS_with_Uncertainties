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
copies these fixtures into ignored `sessions/july12_2012/` before editing.

The frozen `reference_run/` records a 4096-proposal experiment in the original
Python 3.7 / SciPy 1.7.3 GCS environment. With the same arclength-weighted scorer,
297 proposals passed Tier 1 alone; 206 passed all four view/tier checks. The prior
single-tier run used a slightly different endpoint weighting and accepted 288.

The allowances were +3 pixels for Tier 1 and +12 for Tier 2, above each component's
own manual-fit RMS. Tilt, half-angle and aspect ratio still reached the search
bounds. These results are not calibrated confidence intervals or a finalized
512-member forecast ensemble.
