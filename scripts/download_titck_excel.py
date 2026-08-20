from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SOURCE_PAGE = "https://www.titck.gov.tr/dinamikmodul/43"
USER_AGENT = "MuadilIlac-SKRS-Automation/1.0 (+https://www.titck.gov.tr)"
DATE_PATTERN = re.compile(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b")
ROW_PATTERN = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
XLSX_PATTERN = re.compile(r"href\s*=\s*[\"']([^\"']+\.xlsx(?:\?[^\"']*)?)[\"']", re.IGNORECASE)
TAG_PATTERN = re.compile(r"<[^>]+>")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_html_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(TAG_PATTERN.sub(" ", value))).strip()


def find_latest_source() -> tuple[str, str | None]:
    request = Request(
        SOURCE_PAGE,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=30) as response:
        page = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    candidates: list[tuple[str, str | None]] = []
    for row in ROW_PATTERN.findall(page):
        match = XLSX_PATTERN.search(row)
        if not match:
            continue
        row_text = clean_html_text(row)
        date_match = DATE_PATTERN.search(row_text)
        source_url = urljoin(SOURCE_PAGE, html.unescape(match.group(1)))
        source_date = None
        if date_match:
            day, month, year = date_match.groups()
            source_date = f"{year}-{month}-{day}"
        candidates.append((source_url, source_date))
    if not candidates:
        raise RuntimeError("TİTCK resmi sayfasında indirilebilir XLSX bağlantısı bulunamadı.")
    return candidates[0]


def download_xlsx(source_url: str) -> bytes:
    request = Request(
        source_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream",
        },
    )
    with urlopen(request, timeout=120) as response:
        data = response.read()
    if len(data) < 1_000 or data[:2] != b"PK":
        raise RuntimeError("TİTCK yanıtı geçerli bir XLSX ZIP dosyası değil.")
    return data


def read_previous_hash(manifest: Path | None) -> str | None:
    if manifest is None or not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        value = payload.get("sourceSha256")
        return value if isinstance(value, str) and len(value) == 64 else None
    except (OSError, json.JSONDecodeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument("--latest-manifest", type=Path, default=None)
    args = parser.parse_args()

    source_url, source_date = find_latest_source()
    data = download_xlsx(source_url)
    checksum = sha256_bytes(data)
    previous_hash = read_previous_hash(args.latest_manifest)
    unchanged = previous_hash == checksum

    metadata = {
        "status": "unchanged" if unchanged else "updated",
        "sourcePage": SOURCE_PAGE,
        "sourceUrl": source_url,
        "sourceDate": source_date,
        "sourceSha256": checksum,
        "bytes": len(data),
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }
    args.metadata_output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if unchanged:
        print(json.dumps(metadata, ensure_ascii=False))
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="titck-", suffix=".xlsx.part", dir=args.output.parent, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    temporary_path.replace(args.output)
    print(json.dumps({**metadata, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
