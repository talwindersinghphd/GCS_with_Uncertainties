"""Check the frozen acceptance experiment; no projections or network needed."""
import json
from pathlib import Path
import numpy as np

root=Path(__file__).resolve().parent.parent
folder=root/'examples/july12_2012/reference_run'
metadata=json.loads((folder/'search.json').read_text())
data=np.load(folder/'all_candidate_scores.npz',allow_pickle=False)
components=metadata['score_components']
limits=np.array([metadata['reference_scores'][c['key']]+c['allowance'] for c in components])
passed=np.isfinite(data['errors'])&(data['errors']<=limits)
accepted=passed.all(axis=1)
np.testing.assert_array_equal(accepted,data['accepted'])
clear=[i for i,c in enumerate(components) if c['tier']=='1']
assert int(passed[:,clear].all(axis=1).sum())==297
assert int(accepted.sum())==206
assert len(np.unique(data['parameters'][accepted],axis=0))==206
assert data['parameters'].shape==(4096,6)
assert metadata['config']['tier_tolerances']=={'0':{'1':3.,'2':12.},'1':{'1':3.,'2':12.}}
print('Reference run verified: 297 pass Tier 1; 206 pass both tiers; 4096 distinct proposals.')
