from __future__ import annotations

import argparse
import json
from pathlib import Path

KEEP = [
    "id", "product_name", "normalized_product_name", "barcode", "atc_code", "atc_name",
    "active_ingredient", "normalized_active_ingredient", "manufacturer", "prescription_status", "status", "description", "source_file", "source_date",
    "source_url", "first_seen_at", "last_seen_at", "activated_at", "deactivated_at",
    "new_in_latest_snapshot", "extra_fields"
]

parser = argparse.ArgumentParser()
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--equivalences", type=Path, required=True)
parser.add_argument("--data-version", type=str, required=True)
parser.add_argument("--source-url", type=str, default="https://skrs.saglik.gov.tr/")
parser.add_argument("--documentation-url", type=str, default="https://skrs.saglik.gov.tr/doc/index.html#servis7")
parser.add_argument("--gzip-output", type=Path, default=None)
args = parser.parse_args()

items = json.loads(args.input.read_text(encoding="utf-8"))
slim = [{k: item.get(k, "") for k in KEEP} for item in items]
args.output.parent.mkdir(parents=True, exist_ok=True)
payload = json.dumps({"dataVersion": args.data_version, "source": args.source_url, "documentation": args.documentation_url, "medicines": slim}, ensure_ascii=False, separators=(",", ":"))
args.output.write_text(payload, encoding="utf-8")
if args.gzip_output:
    import gzip
    args.gzip_output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.gzip_output, "wt", encoding="utf-8", compresslevel=9) as gz:
        gz.write(payload)
args.equivalences.parent.mkdir(parents=True, exist_ok=True)
eq = json.loads(args.equivalences.read_text(encoding="utf-8"))
args.equivalences.write_text(json.dumps({"source": args.source_url, "documentation": args.documentation_url, "equivalences": eq}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(json.dumps({"medicines": len(slim), "asset_bytes": args.output.stat().st_size, "gzip_bytes": args.gzip_output.stat().st_size if args.gzip_output else None, "equivalences": len(eq)}, ensure_ascii=False))
