# Projected physical outline distances

New GUI searches use `score_unit: projected_solar_radii`. Both the observed arcs
and the projected model exterior are mapped from native pixels through the map's
helioprojective WCS into a common physical plane before nearest-boundary distances
and arc-length weights are computed. One fixed solar radius is 695700 km.

For helioprojective longitude Tx and latitude Ty, and Sun-observer distance D:

```text
X / RSun = (D / RSun) * tan(Tx)
Y / RSun = (D / RSun) * tan(Ty) / cos(Tx)
```

This intersects each viewing ray with the plane through the Sun centre normal to
the observer–Sun line. It handles pixel rotation, anisotropic scale, pointing and
observer distance via the WCS. Each observer has their own projected plane. It does
not recover a 3D distance to the observed CME material. `DSUN_OBS` must be finite
and in metres; missing geometry is an error, not an assumed Earth distance.

Every view/tier uses its own manual-fit baseline multiplied by
`1 + extra_percent / 100`. Fresh session defaults are 10% for Tier 1 and 20% for
Tier 2; both are editable starting values. RMS values remain in projected solar radii.
Physical units standardize the distance scale, not image quality or outline clarity.
The mesh and silhouette raster are still discretized; compare higher resolutions
when the extra allowances approach the numerical boundary uncertainty.

## Saved annotations and old searches

Version 4 keeps annotation coordinates in native image pixels, explicitly tags
`allowance_mode: percent_of_manual_rms`, and retains scoring geometry in projected
solar radii. Images and metadata must still match exactly. Version-3 additive
allowances convert to `100 * absolute_allowance_rsun / manual_rms` for each traced
view/tier, using its saved manual parameters, mesh and raster resolution. The
threshold is preserved and the conversion is recorded. Reloading version 4 never
converts its allowances again. A positive additive allowance with exactly zero
manual RMS cannot be represented as a finite percentage and requires a new session.

For version 2, a scalar pixel allowance converts exactly only when the pixel-to-plane
mapping is an isotropic similarity. The loader checks scale isotropy and linearity
across the image; it rejects migration for unequal scales or nonlinear mappings
instead of guessing an average. Version 1 has no stored allowances, so the current
percentage defaults apply. Loading does not overwrite the original files.

The bundled July 12 annotations already use version 3 and physical-distance
allowances; the GUI converts these to equivalent percentages on load. Saved search
results are never relabelled. Old configurations without `score_unit` continue to
mean native pixels, and configurations without `allowance_mode` retain additive
allowances. To use current scoring programmatically, specify
`score_unit: projected_solar_radii` and `allowance_mode: percent_of_manual_rms`,
supply the maps, and express the allowances as percentages.

New exports identify errors as `projected_solar_radii` in JSON and NPZ metadata,
use `rms_rsun_*` CSV columns and retain D, the fixed solar radius and projection
definition. Score components also record the allowance mode, manual RMS and allowed
RMS, so the percentage and physical threshold are explicit. Legacy pixel search
exports keep `rms_px_*` columns.
