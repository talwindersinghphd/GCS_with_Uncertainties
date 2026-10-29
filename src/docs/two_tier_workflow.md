# Two-tier outline workflow

The saved July 12 example restores the manual fit and both confidence tiers.
For old single-tier annotation files, the previous trace imports as an editable
Tier 1 copy. Review its confidence assignments before saving.

1. Start in **Draw Outline**, the first and default tab. Choose the view, then
   Tier 1 (cyan, clear) or Tier 2 (yellow, less certain).
   **Trace outline** starts drawing, changes to **Stop tracing**, and shows
   **Mode: TRACING — ON**. Right-click or **Stop tracing** ends drawing.
2. To reclassify an existing segment, enable **Assign segments to selected tier**
   and click the middle of a segment in the other tier. Its position is unchanged.
3. Right-click to finish assignment.
4. To draw a new section, choose **New arc**, then click along the observed boundary.
   Use separate arcs across gaps. An arc needs at least two distinct points.
   **Undo point** removes the last point from the selected arc without ending
   tracing. Click repeatedly to step back through more points.
5. **Delete arc** removes the selected arc from the selected view/tier.
   Starting drawing or assignment turns toolbar pan/zoom off automatically,
   keeping the current zoom. Selecting pan/zoom stops editing and updates the
   mode label. Leaving Draw Outline or switching to histograms also ends editing.
6. Switch to **Manual fit** to adjust the model to your outlines.
7. Open the **Uncertainty** tab. Edit the **Scale** values directly; the read-only
   **Historical difference** column shows latitude, longitude and tilt references
   that stay fixed as you edit. Height, half-angle and κ have no historical entry
   and start from exploratory defaults. The multiplier remains editable, with
   search half-width = scale × multiplier. There is no preset selector.
   Set **Extra RMS allowance [RSun]** separately for each view and tier: a candidate
   passes if its RMS is no greater than the manual RMS plus this allowance.
   Fresh defaults are 0.05 for Tier 1 and 0.20 for Tier 2, in projected solar radii.
   Saved values are restored; **Defaults** applies 0.05 / 0.20 to every view.
   Set **CPUs to use**, **Target accepted fits**, and the linked **History-guided** /
   **Broad search** percentages, then choose **Run uncertainty search**.
   Defaults are up to 4 CPUs, 512 fits, and a 70% history / 30% broad mix;
   either percentage can range from 0% to 100%. Choosing 1 CPU uses sequential
   scoring. Counts update as fits complete: **Fits tried: 8420 | Accepted: 387 / 512**.
   Each generated Sobol batch uses a new recorded scramble seed. Scoring stops
   once the first requested number of accepted candidates is known in proposal
   order; the remainder of the batch is not evaluated. Fits already running can
   finish, but later matches are excluded so the retained count stays at the target.
   The default proposal budget is 262,144; hitting it before the target is
   reported as a partial result. During the run, the button becomes **Cancel search** and fit
   and outline editing is disabled. Cancelling stops new fits, lets fits already
   running finish, and retains the partial results.
8. Switch the figure area between **Coronagraphs** and **Accepted parameter
   histograms** to inspect the overlays and all six parameter distributions.
   The coronagraph preview updates about once a second, keeping every accepted
   outline found so far in rose pink and the manual outline in thicker bright
   pink. Completed, cancelled and reopened searches show all accepted outlines.
   It reuses scored silhouettes; projections for untraced views run separately
   so candidate scoring keeps moving. Save finishes the final display before export.
   Histograms update during the search, up to once a second while visible, and
   include every accepted fit. Each bar is the fraction of fits in that bin;
   fractions sum to 1 for each parameter. The title shows the number accepted
   so far, with a dashed manual-fit marker. Longitude
   and tilt use continuous angles around the manual fit. Partial searches are labelled.
9. Choose **Save** in **Uncertainty** after completion to save parameters, both tiers, internal
   settings, and search results. In the July 12 example these go to the ignored
   session directory and the window stays open. Search exports use a new
   `gcs_uncertainty_*` subfolder, including `accepted_parameter_histograms.png`.
   Changing the fit or outlines clears the current
   search results; run again before saving results for the revised inputs.

`accepted_all.csv` and `selected_fits.csv` keep the first requested number of
distinct accepted fits in candidate order, or all retained fits if the search
ended short. The selection preserves each complete parameter vector
and its scores. Summary ranges describe all accepted fits. `search.json` records
the target, stop reason, per-batch seeds and completion, in-flight matches excluded
after reaching the target, and actual ensemble size. The retained fits are the
same across CPU counts; the number tried can vary slightly due to in-flight work.
Search bounds and acceptance allowances stay fixed between batches. The accepted
ensemble is not itself a balanced Sobol sequence.

The mixture percentages are saved with the session and each search. Save before
changing the mix to preserve a run for comparison. Changing either percentage
clears the previous results and histograms; rerun to see the new distributions.
Broad search uses the current ranges around the manual fit. History guides angular
offsets; shape parameters remain broad, and retries/filtering can change the actual mix.

The **Images** tab provides separate brightness and contrast controls for every
observation view. Use the sliders or enter percentages; brightness 0% and contrast
100% keep the original display. **Reset this image** restores both defaults for that
view. Adjustments remain available while searching and preserve the original image
data, traced coordinates and accepted fits. **Save** stores them for the current
time step and updates the exported figures. Older sessions start with the original
display settings.

Scales and the multiplier are editable in the compact **Uncertainty** tab and
persist with the session. Run, Save and progress are pinned below the settings;
only the settings scroll when space is limited. Batch size, resolution,
scoring and separate export controls remain internal. There is no **Move arc** button;
use segment assignment to change tiers.

Default extra allowances are +0.05 projected solar radii for Tier 1 and +0.20 for Tier 2. They
are editable starting values, not measured statistical uncertainties. There is no
absolute outline-error cap. If both tiers are used in a view, Tier 2 must have at
least as much additional flexibility as Tier 1.

Version-3 annotation files record solar-radius units and distance geometry and preserve both tiers, disconnected arcs, exact image
identities, the manual reference and search settings. Original example fixtures
are not overwritten. Old version-1 and version-2 annotations remain readable. Saved pixel allowances
are converted per image only when its WCS is an exact isotropic similarity in the
projected plane. Otherwise loading asks for a new physical-distance session;
no average pixel scale is guessed. Migration provenance is saved in version 3.
The original annotation files are not changed by loading them.

Search exports include separate error columns such as `rms_rsun_view_0_tier_1` and
`rms_rsun_view_0_tier_2`. Full configuration, reference scores and allowances are in
`search.json`. Compatible ranges are not calibrated confidence intervals.
