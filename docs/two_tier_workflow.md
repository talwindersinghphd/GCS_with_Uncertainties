# Two-tier outline workflow

The saved July 12 example restores the manual fit and both confidence tiers.
For old single-tier annotation files, the previous trace imports as an editable
Tier 1 copy. Review its confidence assignments before rerunning.

1. Choose Tier 1 (cyan, clear) or Tier 2 (yellow, less certain).
2. To reclassify an existing segment, enable **Assign segments to selected tier**
   and click the middle of a segment in the other tier. Its position is unchanged.
3. Right-click to finish assignment. **Move arc** moves an entire connected arc.
4. To draw a new section, choose **New arc**, then click along the observed boundary.
   Use separate arcs across gaps. An arc needs at least two distinct points.
5. **Undo point**, **Delete arc**, and **Clear tier** affect the selected view/tier/arc.
   Turn toolbar pan/zoom off before drawing or assigning; zoom first if useful.
6. **Score manual fit only** previews each view/tier baseline and candidate limit.
7. Review the additional allowances, parameter ranges and sampling settings.
8. **Freeze current fit and sample** evaluates candidates. Every view/tier must pass
   relative to its own manual baseline. Better agreement elsewhere cannot compensate.
9. **Save July 12 session** saves parameters, both tiers, allowances and search
   settings to the ignored session directory. The window stays open.

Default extra allowances are +3 native pixels for Tier 1 and +12 for Tier 2. They
are editable starting values, not measured statistical uncertainties. There is no
absolute outline-error cap. If both tiers are used in a view, Tier 2 must have at
least as much additional flexibility as Tier 1.

Version-2 annotation files preserve both tiers, disconnected arcs, exact image
identities, the manual reference and search settings. Original example fixtures
are not overwritten. Old version-1 annotations remain readable.

Search exports include separate error columns such as `rms_px_view_0_tier_1` and
`rms_px_view_0_tier_2`. Full configuration, reference scores and allowances are in
`search.json`. Compatible ranges are not calibrated confidence intervals.
