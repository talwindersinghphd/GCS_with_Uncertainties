"""Optionally recover the original JP2 observations into an ignored cache."""
import argparse,hashlib,json
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

root=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,default=root/'data/cache/july12_2012')
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
manifest=json.loads((root/'examples/july12_2012/manifest.json').read_text())
session=requests.Session()
session.mount('https://',HTTPAdapter(max_retries=Retry(total=3,backoff_factor=.5,status_forcelist=[429,500,502,503,504])))
for record in manifest['images']:
    name=record['file']
    if Path(name).name!=name:
        raise ValueError('Invalid manifest filename.')
    path=args.output/name
    if path.exists():
        data=path.read_bytes()
    else:
        response=session.get('https://api.helioviewer.org/v2/getJP2Image/',
            params=dict(date=record['requested_date'],sourceId=record['source_id'],download='true'),timeout=(10,60))
        response.raise_for_status();data=response.content
    if hashlib.sha256(data).hexdigest()!=record['sha256']:
        raise ValueError('Image checksum differs from the recorded reference: '+name)
    if not path.exists():path.write_bytes(data)
    print('Verified',path)
