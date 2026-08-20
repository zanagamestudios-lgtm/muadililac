from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SOURCE_URL = "https://www.titck.gov.tr/dinamikmodul/43"
DOCUMENTATION_URL = "https://skrs.saglik.gov.tr/doc/index.html#servis7"
BASE_SHEETS = {
    "AKTİF ÜRÜNLER LİSTESİ": "ACTIVE",
    "PASİF ÜRÜNLER LİSTESİ": "PASSIVE",
}
AUX_SHEETS = {
    "PASİFE ALINACAK ÜRÜNLER": "PENDING_PASSIVE",
    "LİSTEYE YENİ EKLENEN ÜRÜNLER": "NEW",
    "DEĞİŞİKLİK YAPILAN ÜRÜNLER": "CHANGED",
}
CORE_KEYS = {
    "ilac_adi", "barkod", "atc_kodu", "atc_adi", "firma_adi", "recete_turu",
    "durumu", "aciklama", "aktif_urunler_listesine_alindigi_tarih",
    "pasif_urunler_listesine_alindigi_tarih",
}


def clean(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.strftime("%Y-%m-%d")
    text = str(value).replace("\u00a0", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize(text: Any) -> str:
    table = str.maketrans({
        "İ": "I", "I": "i", "ı": "i", "Ş": "s", "ş": "s",
        "Ğ": "g", "ğ": "g", "Ü": "u", "ü": "u", "Ö": "o", "ö": "o",
        "Ç": "c", "ç": "c",
    })
    return re.sub(r"[^a-z0-9]+", " ", clean(text).translate(table).lower()).strip()


def barcode_key(value: Any) -> str:
    return re.sub(r"[^0-9]", "", clean(value))


def parse_date(value: Any, fallback: str = "") -> str:
    text = clean(value)
    if not text:
        return fallback
    for dayfirst in (True, False):
        parsed = pd.to_datetime(text, dayfirst=dayfirst, errors="coerce")
        if not pd.isna(parsed):
            return parsed.date().isoformat()
    return fallback


def canonical_column(value: Any) -> str:
    return normalize(value).replace(" ", "_")


def find_header(frame: pd.DataFrame) -> int | None:
    for idx in range(min(len(frame), 20)):
        values = {canonical_column(v) for v in frame.iloc[idx].tolist()}
        if "ilac_adi" in values and "barkod" in values:
            return idx
    return None


def resolve_sheet(sheet_names: list[str], expected: str) -> str | None:
    wanted = normalize(expected)
    for sheet in sheet_names:
        if normalize(sheet) == wanted:
            return sheet
    for sheet in sheet_names:
        if normalize(sheet).startswith(wanted):
            return sheet
    return None


def load_sheet(path: Path, sheet: str) -> tuple[list[dict[str, str]], dict[str, Any]]:
    frame = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object, engine="openpyxl")
    header = find_header(frame)
    if header is None:
        return [], {"sheet": sheet, "header_row": None, "records": 0, "reason": "header_not_found"}
    raw_headers = [clean(v) for v in frame.iloc[header].tolist()]
    columns: list[str] = []
    seen: Counter[str] = Counter()
    for raw in raw_headers:
        key = canonical_column(raw) or "extra"
        seen[key] += 1
        columns.append(key if seen[key] == 1 else f"{key}_{seen[key]}")
    records: list[dict[str, str]] = []
    for _, row in frame.iloc[header + 1 :].iterrows():
        values = {columns[i]: clean(row.iloc[i]) for i in range(min(len(columns), len(row)))}
        if not any(values.values()):
            continue
        name = values.get("ilac_adi", "")
        if not name or name.lower() in {"ilaç adı", "ilac adi"}:
            continue
        records.append(values)
    return records, {"sheet": sheet, "header_row": header + 1, "records": len(records), "columns": columns}


def make_record(values: dict[str, str], status: str, source_file: str, snapshot_date: str, source_sheet: str) -> dict[str, Any]:
    product_name = clean(values.get("ilac_adi", ""))
    barcode = barcode_key(values.get("barkod", ""))
    atc_name = clean(values.get("atc_adi", ""))
    explicit_ingredient = next((values.get(k, "") for k in values if "etken_madde" in k or "etkin_madde" in k), "")
    active_ingredient = clean(explicit_ingredient) or atc_name
    date_from_active = parse_date(values.get("aktif_urunler_listesine_alindigi_tarih", ""), snapshot_date)
    date_from_passive = parse_date(values.get("pasif_urunler_listesine_alindigi_tarih", ""), snapshot_date)
    source_key = f"barcode:{barcode}" if barcode else f"name:{normalize(product_name)}|manufacturer:{normalize(values.get('firma_adi', ''))}"
    extra = {k: v for k, v in values.items() if k not in CORE_KEYS and v}
    extra.update({
        "source_sheet": source_sheet,
        "sheet_status": status,
        "active_ingredient_source": "explicit" if explicit_ingredient else "atc_name_fallback",
    })
    return {
        "id": hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:24],
        "product_name": product_name,
        "normalized_product_name": normalize(product_name),
        "barcode": barcode,
        "atc_code": clean(values.get("atc_kodu", "")),
        "atc_name": atc_name,
        "active_ingredient": active_ingredient,
        "normalized_active_ingredient": normalize(active_ingredient),
        "manufacturer": clean(values.get("firma_adi", "")),
        "prescription_status": clean(values.get("recete_turu", "")),
        "status": status,
        "description": clean(values.get("aciklama", "")),
        "source_file": source_file,
        "source_date": snapshot_date,
        "source_url": SOURCE_URL,
        "first_seen_at": date_from_active if status == "ACTIVE" else snapshot_date,
        "last_seen_at": snapshot_date,
        "activated_at": date_from_active if status == "ACTIVE" else "",
        "deactivated_at": date_from_passive if status == "PASSIVE" else "",
        "new_in_latest_snapshot": False,
        "extra_fields": extra,
    }


def match_record(records: list[dict[str, Any]], values: dict[str, str]) -> dict[str, Any] | None:
    barcode = barcode_key(values.get("barkod", ""))
    if barcode:
        matches = [r for r in records if r.get("barcode") == barcode]
        if len(matches) == 1:
            return matches[0]
        name = normalize(values.get("ilac_adi", ""))
        exact = [r for r in matches if r.get("normalized_product_name") == name]
        if exact:
            return exact[0]
    name = normalize(values.get("ilac_adi", ""))
    manufacturer = normalize(values.get("firma_adi", ""))
    for record in records:
        if record.get("normalized_product_name") == name and normalize(record.get("manufacturer", "")) == manufacturer:
            return record
    return None


def process(input_path: Path, output_dir: Path, snapshot_date: str | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
    snapshot_date = snapshot_date or "2026-08-18"
    book = pd.ExcelFile(input_path, engine="openpyxl")
    available = book.sheet_names
    sheet_reports: list[dict[str, Any]] = []
    base_rows: list[tuple[str, str, dict[str, str]]] = []
    aux_rows: dict[str, list[dict[str, str]]] = {}

    for expected, status in BASE_SHEETS.items():
        actual = resolve_sheet(available, expected)
        if actual is None:
            raise ValueError(f"Gerekli kanonik sayfa bulunamadı: {expected}")
        rows, report = load_sheet(input_path, actual)
        report.update({"expected_sheet": expected, "role": "canonical", "status": status})
        sheet_reports.append(report)
        base_rows.extend((status, actual, row) for row in rows)

    for expected, role in AUX_SHEETS.items():
        actual = resolve_sheet(available, expected)
        if actual is None:
            sheet_reports.append({"expected_sheet": expected, "role": role, "records": 0, "reason": "sheet_not_found"})
            aux_rows[role] = []
            continue
        rows, report = load_sheet(input_path, actual)
        report.update({"expected_sheet": expected, "role": role})
        sheet_reports.append(report)
        aux_rows[role] = rows

    canonical_by_barcode: dict[str, list[dict[str, str]]] = defaultdict(list)
    for status, sheet, values in base_rows:
        barcode = barcode_key(values.get("barkod", ""))
        if barcode:
            canonical_by_barcode[barcode].append({
                "status": status,
                "sheet": sheet,
                "product_name": clean(values.get("ilac_adi", "")),
                "active_date": parse_date(values.get("aktif_urunler_listesine_alindigi_tarih", ""), ""),
                "passive_date": parse_date(values.get("pasif_urunler_listesine_alindigi_tarih", ""), ""),
            })
    canonical_status_conflicts = [
        {"barcode": barcode, "rows": rows}
        for barcode, rows in sorted(canonical_by_barcode.items())
        if len({row["status"] for row in rows}) > 1
    ]

    records: list[dict[str, Any]] = []
    for status, sheet, values in base_rows:
        record = make_record(values, status, input_path.name, snapshot_date, sheet)
        existing = next((r for r in records if r.get("barcode") == record.get("barcode") and record.get("barcode")), None)
        if existing is None:
            records.append(record)
        else:
            # If the same barcode appears twice, keep the richer/current active row without silently duplicating it.
            if existing.get("status") != "ACTIVE" and record.get("status") == "ACTIVE":
                records[records.index(existing)] = record

    annotations: dict[str, list[str]] = defaultdict(list)
    for row in aux_rows.get("NEW", []):
        matched = match_record(records, row)
        if matched is None:
            matched = make_record(row, "ACTIVE" if clean(row.get("durumu", "")).lower() == "aktif" else "PASSIVE", input_path.name, snapshot_date, "LİSTEYE YENİ EKLENEN ÜRÜNLER")
            records.append(matched)
        matched["new_in_latest_snapshot"] = True
        if row.get("aciklama"):
            annotations[matched["id"]].append(f"Yeni kayıt: {row['aciklama']}")

    for row in aux_rows.get("CHANGED", []):
        matched = match_record(records, row)
        if matched is None:
            matched = make_record(row, "ACTIVE" if clean(row.get("durumu", "")).lower() == "aktif" else "PASSIVE", input_path.name, snapshot_date, "DEĞİŞİKLİK YAPILAN ÜRÜNLER")
            records.append(matched)
        if row.get("aciklama"):
            annotations[matched["id"]].append(f"Değişiklik: {row['aciklama']}")

    for record in records:
        if annotations.get(record["id"]):
            record["extra_fields"]["change_notes"] = " | ".join(annotations[record["id"]])
            record["description"] = record["description"] or annotations[record["id"]][0]

    records.sort(key=lambda item: (item.get("normalized_product_name", ""), item.get("id", "")))
    status_counts = Counter(item["status"] for item in records)
    missing_fields = {field: sum(not item.get(field) for item in records) for field in ("product_name", "barcode", "manufacturer", "atc_code", "active_ingredient")}
    pending_rows = aux_rows.get("PENDING_PASSIVE", [])
    change_report = {
        "source_file": input_path.name,
        "source_sha256": source_sha256,
        "snapshot_date": snapshot_date,
        "canonical_records": len(records),
        "active_records": status_counts.get("ACTIVE", 0),
        "passive_records": status_counts.get("PASSIVE", 0),
        "new_records_sheet": len(aux_rows.get("NEW", [])),
        "changed_records_sheet": len(aux_rows.get("CHANGED", [])),
        "pending_passive_records": len(pending_rows),
        "new_records_marked": sum(bool(item.get("new_in_latest_snapshot")) for item in records),
        "sheet_reports": sheet_reports,
        "pending_passive_samples": pending_rows[:20],
        "missing_fields": missing_fields,
        "duplicate_barcodes": [barcode for barcode, count in Counter(r.get("barcode") for r in records if r.get("barcode")).items() if count > 1],
        "canonical_status_conflicts": canonical_status_conflicts,
        "policy": "AKTİF/PASİF kanonik listeler esas alınır; aynı barkod iki kanonik listede bulunursa AKTİF listesi güncel durum kabul edilir ve çatışma raporlanır; PASİFE ALINACAK geçici liste nihai pasiflik olarak uygulanmaz; yeni/değişiklik sayfaları metadata ile işaretlenir.",
    }
    version = f"SKRS-XLSX-{source_sha256[:12]}"
    manifest = {
        "dataVersion": version,
        "source": SOURCE_URL,
        "documentation": DOCUMENTATION_URL,
        "sourceFile": input_path.name,
        "sourceSha256": source_sha256,
        "snapshotDate": snapshot_date,
        "recordCount": len(records),
        "activeCount": status_counts.get("ACTIVE", 0),
        "passiveCount": status_counts.get("PASSIVE", 0),
        "newCount": sum(bool(item.get("new_in_latest_snapshot")) for item in records),
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "workbookSheets": sheet_reports,
    }
    (output_dir / "master_medicines.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "source_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "quality_report.json").write_text(json.dumps(change_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "change_report.json").write_text(json.dumps(change_report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "equivalences.json").write_text("[]\n", encoding="utf-8")
    print(json.dumps({"manifest": manifest, "quality": change_report}, ensure_ascii=False, indent=2))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--snapshot-date", default="2026-08-18")
    args = parser.parse_args()
    process(args.input, args.output_dir, args.snapshot_date)


if __name__ == "__main__":
    main()
