"""Dataset download and loading for SKAB (pump rig) and the UCI hydraulic rig.

Neither dataset is redistributed in this repository. SKAB is GPL-3.0 and the
UCI hydraulic data is CC BY 4.0; both are fetched from their original sources.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import BASELINE_ROWS, HYDRAULIC_DIR, SIGNALS, SKAB_DIR

SKAB_GIT = "https://github.com/waico/SKAB.git"
SKAB_ZIP = "https://codeload.github.com/waico/SKAB/zip/refs/heads/master"
HYDRAULIC_ZIP = (
    "https://archive.ics.uci.edu/static/public/447/"
    "condition+monitoring+of+hydraulic+systems.zip"
)
HYDRAULIC_SENSORS = [
    "PS1", "PS2", "PS3", "PS4", "PS5", "PS6", "EPS1", "FS1", "FS2",
    "TS1", "TS2", "TS3", "TS4", "VS1", "CE", "CP", "SE",
]
HYDRAULIC_TARGETS = ["cooler", "valve", "pump_leak", "accumulator", "stable"]


@dataclass
class Run:
    """One SKAB experiment: a single machine run containing one fault."""

    name: str          # e.g. "valve1/3"
    group: str         # experiment family: valve1 | valve2 | other
    frame: pd.DataFrame

    @property
    def signals(self) -> pd.DataFrame:
        return self.frame[SIGNALS]

    @property
    def anomaly(self) -> np.ndarray:
        return self.frame["anomaly"].to_numpy().astype(bool)

    @property
    def test_slice(self) -> slice:
        return slice(BASELINE_ROWS, len(self.frame))


# --------------------------------------------------------------------------- SKAB
def download_skab(target: Path = SKAB_DIR) -> Path:
    """Fetch SKAB into ``target`` (git clone, falling back to the zip archive)."""
    if (_skab_data_dir(target) / "valve1").exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "clone", "--depth", "1", SKAB_GIT, str(target)],
                       check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        import requests

        response = requests.get(SKAB_ZIP, timeout=300)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            tmp = target.parent / "_skab_tmp"
            _safe_extract(archive, tmp)
            shutil.move(str(next(tmp.iterdir())), str(target))
            shutil.rmtree(tmp, ignore_errors=True)
    return target


def _read_skab_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=";", parse_dates=["datetime"])
    # The flow column is named "Volume Flow RateRMS" in the CSVs ("RateRMS" in docs).
    frame = frame.rename(columns={"RateRMS": "Volume Flow RateRMS"})
    missing = set(SIGNALS) - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing columns {missing}")
    return frame.sort_values("datetime").reset_index(drop=True)


def _skab_data_dir(root: Path) -> Path:
    """SKAB CSVs live in <root>/data (git clone) or directly in <root> (flat copy)."""
    return root / "data" if (root / "data" / "valve1").exists() else root


def load_runs(root: Path = SKAB_DIR) -> list[Run]:
    """All 34 SKAB experiments, sorted by family and number."""
    data = _skab_data_dir(root)
    if not (data / "valve1").exists():
        raise FileNotFoundError(f"SKAB not found in {root}. Run `python -m lineguard download`.")
    runs = []
    for group in ("valve1", "valve2", "other"):
        files = sorted((data / group).glob("*.csv"), key=lambda p: int(p.stem))
        for path in files:
            runs.append(Run(f"{group}/{path.stem}", group, _read_skab_csv(path)))
    return runs


def load_anomaly_free(root: Path = SKAB_DIR) -> pd.DataFrame:
    return _read_skab_csv(_skab_data_dir(root) / "anomaly-free" / "anomaly-free.csv")


# ------------------------------------------------------------------- UCI hydraulic
def download_hydraulic(target: Path = HYDRAULIC_DIR) -> Path:
    if (target / "profile.txt").exists():
        return target
    import requests

    target.mkdir(parents=True, exist_ok=True)
    response = requests.get(HYDRAULIC_ZIP, timeout=600)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        _safe_extract(archive, target)
    (target / "manifest.json").write_text(json.dumps(
        {"url": HYDRAULIC_ZIP, "sha256": hashlib.sha256(response.content).hexdigest()}, indent=2))
    return target


def load_hydraulic_profile(root: Path = HYDRAULIC_DIR) -> pd.DataFrame:
    return pd.read_csv(root / "profile.txt", sep="\t", header=None, names=HYDRAULIC_TARGETS)


def load_hydraulic_sensor(name: str, root: Path = HYDRAULIC_DIR) -> np.ndarray:
    """Cycles x samples matrix for one sensor (cached as .npy after first read)."""
    cache = root / f"{name}.npy"
    if cache.exists():
        return np.load(cache)
    values = np.loadtxt(root / f"{name}.txt", dtype=np.float32)
    np.save(cache, values)
    return values


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    target = target.resolve()
    for member in archive.infolist():
        destination = (target / member.filename).resolve()
        if not destination.is_relative_to(target):
            raise ValueError(f"Unsafe path in archive: {member.filename}")
    archive.extractall(target)
