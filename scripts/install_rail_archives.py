"""Install the six approved Rail Data Marketplace archive products locally.

Raw source data is deliberately written below the ignored ``data`` directory,
never committed. The allow-list prevents an accidental bulk import of other
downloads that may have different licence or retention terms.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


APPROVED = {
    "CIF_ALL_FULL_DAILY_toc-full.json.gz": "schedule",
    "NWR_GTCL20261026.zip": "geospatial-track-centre-lines",
    "fares_full.zip": "fares",
    "NWR_TrackModel20261026.zip": "track-model",
    "Darwin Timetable Files.zip": "darwin-timetable",
    "TPS_Data.tar.gz": "tps",
}


def _safe_members(names: list[str]) -> None:
    for name in names:
        path = Path(name.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Unsafe archive member: {name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install(source_dir: Path, data_dir: Path) -> dict:
    missing = [name for name in APPROVED if not (source_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing approved archives: " + ", ".join(missing))
    root = data_dir / "rail_imports"
    root.mkdir(parents=True, exist_ok=True)
    products = []
    for filename, product in APPROVED.items():
        source = source_dir / filename
        target = root / product
        target.mkdir(parents=True, exist_ok=True)
        if filename.endswith(".zip"):
            with zipfile.ZipFile(source) as archive:
                _safe_members(archive.namelist())
                archive.extractall(target)
                members = len(archive.infolist())
        elif filename.endswith(".tar.gz"):
            with tarfile.open(source, "r:gz") as archive:
                names = archive.getnames()
                _safe_members(names)
                archive.extractall(target, filter="data")
                members = len(names)
        else:
            installed = data_dir / "schedule-full.dat"
            temporary = installed.with_suffix(".dat.installing")
            shutil.copyfile(source, temporary)
            temporary.replace(installed)
            # Validate it is line-delimited CIF JSON before advertising it.
            with gzip.open(installed, "rt", encoding="utf-8-sig") as handle:
                first = json.loads(handle.readline())
            if "JsonTimetableV1" not in first:
                raise ValueError("CIF archive did not begin with JsonTimetableV1")
            members = 1
        products.append({
            "file": filename, "product": product, "bytes": source.stat().st_size,
            "sha256": _sha256(source), "members": members,
        })
    manifest = {
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "source_directory": str(source_dir),
        "products": products,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args()
    result = install(args.source.resolve(), args.data.resolve())
    for row in result["products"]:
        print(f"{row['product']}: {row['members']} files ({row['bytes']:,} archive bytes)")


if __name__ == "__main__":
    main()
