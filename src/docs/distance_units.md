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

Every view/tier still uses its own manual-fit baseline plus its additional
allowance. There is no absolute error cap. Fresh session defaults are +0.05 RSun
for Tier 1 and +0.20 RSun for Tier 2; both are editable starting values.
Physical units standardize the distance scale, not image quality or outline clarity.
The mesh and silhouette raster are still discretized; compare higher resolutions
when the extra allowances approach the numerical boundary uncertainty.

## Saved annotations and old searches

Version 3 keeps annotation coordinates in native image pixels, with an explicit
coordinate-unit tag, but stores allowances and scoring geometry in projected solar
radii. Images and metadata must still match exactly. Migration provenance records
the original pixel allowances and conversion factors. Reloading version 3 never
converts its allowances a second time.

For version 2, a scalar pixel allowance converts exactly only when the pixel-to-plane
mapping is an isotropic similarity. The loader checks scale isotropy and linearity
across the image; it rejects migration for unequal scales or nonlinear mappings
instead of guessing an average. Version 1 has no stored allowances, so the current
physical defaults apply. Loading does not overwrite the original files.

The bundled July 12 annotations already use version 3 and physical-distance
allowances. Saved search results are never relabelled. Old configurations without
`score_unit` continue to mean native pixels for reproducibility. To run
physical scoring programmatically, specify `projected_solar_radii`, supply the maps,
and express all allowances in solar radii.

New exports identify errors as `projected_solar_radii` in JSON and NPZ metadata,
use `rms_rsun_*` CSV columns and retain D, the fixed solar radius and projection
definition. Legacy pixel search exports keep `rms_px_*` columns.
