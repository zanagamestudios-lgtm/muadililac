from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_WORKBOOK = PROJECT_ROOT / "scripts" / "build_skrs_workbook.py"
PREPARE_ASSET = PROJECT_ROOT / "scripts" / "prepare_android_asset.py"
BUILD_DB = PROJECT_ROOT / "scripts" / "build_prepackaged_db.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    print("+", " ".join(str(item) for item in command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def copy_required(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and publish a validated SKRS data release.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--snapshot-date", required=True, help="ISO date, normally the Tuesday publication date.")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "data" / "releases")
    parser.add_argument("--source-url", default="https://www.titck.gov.tr/dinamikmodul/43")
    parser.add_argument("--documentation-url", default="https://skrs.saglik.gov.tr/doc/index.html#servis7")
    args = parser.parse_args()

    if not args.input.exists():
        raise FileNotFoundError(args.input)
    if len(args.snapshot_date) != 10 or args.snapshot_date[4] != "-" or args.snapshot_date[7] != "-":
        raise ValueError("--snapshot-date must use YYYY-MM-DD format")

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="skrs-release-") as temp_dir:
        staging = Path(temp_dir)
        master_dir = staging / "master"
        run([
            sys.executable,
            str(BUILD_WORKBOOK),
            "--input", str(args.input.resolve()),
            "--output-dir", str(master_dir),
            "--snapshot-date", args.snapshot_date,
        ])

        source_manifest = json.loads((master_dir / "source_manifest.json").read_text(encoding="utf-8"))
        data_version = source_manifest["dataVersion"]
        source_sha = source_manifest["sourceSha256"]
        release_id = f"{args.snapshot_date}-{source_sha[:12]}"
        asset_dir = staging / "asset"
        asset_dir.mkdir(parents=True, exist_ok=True)
        medicines_json = asset_dir / "medicines.json"
        medicines_gz = asset_dir / "medicines.json.gz"
        equivalences = master_dir / "equivalences.json"
        run([
            sys.executable,
            str(PREPARE_ASSET),
            "--input", str(master_dir / "master_medicines.json"),
            "--output", str(medicines_json),
            "--gzip-output", str(medicines_gz),
            "--equivalences", str(equivalences),
            "--data-version", data_version,
            "--source-url", args.source_url,
            "--documentation-url", args.documentation_url,
        ])

        prepackaged_db = asset_dir / "prepackaged.db"
        run([
            sys.executable,
            str(BUILD_DB),
            "--master", str(master_dir / "master_medicines.json"),
            "--manifest", str(master_dir / "source_manifest.json"),
            "--output", str(prepackaged_db),
        ])

        release_dir = output_root / release_id
        latest_dir = output_root / "latest"
        if release_dir.exists():
            shutil.rmtree(release_dir)
        release_dir.mkdir(parents=True, exist_ok=True)
        if latest_dir.exists():
            shutil.rmtree(latest_dir)
        latest_dir.mkdir(parents=True, exist_ok=True)

        files = {
            "medicines.json.gz": medicines_gz,
            "medicines.json": medicines_json,
            "prepackaged.db": prepackaged_db,
            "equivalences.json": equivalences,
            "quality_report.json": master_dir / "quality_report.json",
            "change_report.json": master_dir / "change_report.json",
            "source_manifest.json": master_dir / "source_manifest.json",
        }
        for name, source in files.items():
            copy_required(source, release_dir / name)
            copy_required(source, latest_dir / name)

        manifest = {
            "schemaVersion": 1,
            "releaseId": release_id,
            "dataVersion": data_version,
            "snapshotDate": args.snapshot_date,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "source": args.source_url,
            "documentation": args.documentation_url,
            "sourceFile": source_manifest["sourceFile"],
            "sourceSha256": source_sha,
            "recordCount": source_manifest["recordCount"],
            "activeCount": source_manifest["activeCount"],
            "passiveCount": source_manifest["passiveCount"],
            "newCount": source_manifest["newCount"],
            "files": {
                name: {
                    "path": f"data/releases/{release_id}/{name}",
                    "latestPath": f"data/releases/latest/{name}",
                    "bytes": (release_dir / name).stat().st_size,
                    "sha256": sha256(release_dir / name),
                }
                for name in files
            },
            "updatePolicy": "Kullanıcı onayı olmadan veri değiştirilmez. Uygulama güncelleme önerisini çarşamba günleri gösterir; indirme ve atomik uygulama yalnızca kullanıcı onayından sonra yapılır.",
        }
        for directory in (release_dir, latest_dir):
            (directory / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        print(json.dumps({
            "releaseId": release_id,
            "dataVersion": data_version,
            "releaseDir": str(release_dir),
            "latestDir": str(latest_dir),
            "manifest": manifest,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
