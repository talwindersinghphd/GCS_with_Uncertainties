"""Locations independent of the current working directory."""
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_ROOT.parent
CACHE_ROOT = PROJECT_ROOT / 'data' / 'cache'
SESSION_ROOT = PROJECT_ROOT / 'sessions'
EXAMPLE_ROOT = SOURCE_ROOT / 'examples' / 'july12_2012'
