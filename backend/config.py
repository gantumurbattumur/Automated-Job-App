import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# Personal state (settings, profile, status, backups). JOBPREP_DATA / JOBPREP_DB let tests use a scratch copy.
DATA = Path(os.environ.get("JOBPREP_DATA", ROOT / "data"))
DB_PATH = Path(os.environ.get("JOBPREP_DB", ROOT / "jobs.db"))


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / f"{name}.yaml").read_text())
