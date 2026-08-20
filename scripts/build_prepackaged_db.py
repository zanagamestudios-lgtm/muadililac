from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS medicines (
  id TEXT NOT NULL,
  productName TEXT NOT NULL,
  normalizedProductName TEXT NOT NULL,
  barcode TEXT NOT NULL,
  atcCode TEXT NOT NULL,
  atcName TEXT NOT NULL,
  activeIngredient TEXT NOT NULL,
  normalizedActiveIngredient TEXT NOT NULL,
  manufacturer TEXT NOT NULL,
  prescriptionStatus TEXT NOT NULL,
  status TEXT NOT NULL,
  description TEXT NOT NULL,
  sourceFile TEXT NOT NULL,
  sourceDate TEXT NOT NULL,
  sourceUrl TEXT NOT NULL,
  firstSeenAt TEXT NOT NULL,
  lastSeenAt TEXT NOT NULL,
  activatedAt TEXT NOT NULL,
  deactivatedAt TEXT NOT NULL,
  newInLatestSnapshot INTEGER NOT NULL,
  extraFieldsJson TEXT NOT NULL,
  isFavorite INTEGER NOT NULL,
  PRIMARY KEY(id)
);
CREATE INDEX IF NOT EXISTS index_medicines_normalizedProductName ON medicines (normalizedProductName);
CREATE INDEX IF NOT EXISTS index_medicines_barcode ON medicines (barcode);
CREATE INDEX IF NOT EXISTS index_medicines_status ON medicines (status);
CREATE INDEX IF NOT EXISTS index_medicines_normalizedActiveIngredient ON medicines (normalizedActiveIngredient);
CREATE VIRTUAL TABLE IF NOT EXISTS medicine_fts USING FTS4(
  productName TEXT NOT NULL,
  normalizedProductName TEXT NOT NULL,
  barcode TEXT NOT NULL,
  atcCode TEXT NOT NULL,
  atcName TEXT NOT NULL,
  activeIngredient TEXT NOT NULL,
  normalizedActiveIngredient TEXT NOT NULL,
  manufacturer TEXT NOT NULL,
  content=medicines
);
CREATE TRIGGER IF NOT EXISTS room_fts_content_sync_medicine_fts_BEFORE_UPDATE BEFORE UPDATE ON medicines BEGIN DELETE FROM medicine_fts WHERE docid=OLD.rowid; END;
CREATE TRIGGER IF NOT EXISTS room_fts_content_sync_medicine_fts_BEFORE_DELETE BEFORE DELETE ON medicines BEGIN DELETE FROM medicine_fts WHERE docid=OLD.rowid; END;
CREATE TRIGGER IF NOT EXISTS room_fts_content_sync_medicine_fts_AFTER_UPDATE AFTER UPDATE ON medicines BEGIN INSERT INTO medicine_fts(docid, productName, normalizedProductName, barcode, atcCode, atcName, activeIngredient, normalizedActiveIngredient, manufacturer) VALUES (NEW.rowid, NEW.productName, NEW.normalizedProductName, NEW.barcode, NEW.atcCode, NEW.atcName, NEW.activeIngredient, NEW.normalizedActiveIngredient, NEW.manufacturer); END;
CREATE TRIGGER IF NOT EXISTS room_fts_content_sync_medicine_fts_AFTER_INSERT AFTER INSERT ON medicines BEGIN INSERT INTO medicine_fts(docid, productName, normalizedProductName, barcode, atcCode, atcName, activeIngredient, normalizedActiveIngredient, manufacturer) VALUES (NEW.rowid, NEW.productName, NEW.normalizedProductName, NEW.barcode, NEW.atcCode, NEW.atcName, NEW.activeIngredient, NEW.normalizedActiveIngredient, NEW.manufacturer); END;
CREATE TABLE IF NOT EXISTS recent_searches (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, query TEXT NOT NULL, normalizedQuery TEXT NOT NULL, searchedAt INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS index_recent_searches_normalizedQuery ON recent_searches (normalizedQuery);
CREATE TABLE IF NOT EXISTS dataset_metadata (id INTEGER NOT NULL, dataVersion TEXT NOT NULL, sourceUrl TEXT NOT NULL, importedAt INTEGER NOT NULL, recordCount INTEGER NOT NULL, PRIMARY KEY(id));
CREATE TABLE IF NOT EXISTS room_master_table (id INTEGER PRIMARY KEY, identity_hash TEXT);
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--master', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--identity-hash', type=str, default='04434bfbd0c9c53fcce9d808d09fa168')
    args = parser.parse_args()
    master = json.loads(args.master.read_text(encoding='utf-8'))
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    con = sqlite3.connect(args.output)
    con.execute('PRAGMA journal_mode=DELETE')
    con.execute('PRAGMA synchronous=OFF')
    con.executescript(SCHEMA)
    con.execute('PRAGMA user_version=3')
    con.execute("INSERT OR REPLACE INTO room_master_table (id, identity_hash) VALUES (42, ?)", (args.identity_hash,))
    now = int(time.time() * 1000)
    con.execute(
        'INSERT OR REPLACE INTO dataset_metadata (id, dataVersion, sourceUrl, importedAt, recordCount) VALUES (1, ?, ?, ?, ?)',
        (manifest['dataVersion'], manifest['source'], now, len(master)),
    )
    rows = []
    for item in master:
        rows.append((
            item['id'], item.get('product_name', ''), item.get('normalized_product_name', ''),
            item.get('barcode', ''), item.get('atc_code', ''), item.get('atc_name', ''),
            item.get('active_ingredient', ''), item.get('normalized_active_ingredient', ''),
            item.get('manufacturer', ''), item.get('prescription_status', ''), item.get('status', 'UNKNOWN'),
            item.get('description', ''), item.get('source_file', ''), item.get('source_date', ''),
            item.get('source_url', ''), item.get('first_seen_at', ''), item.get('last_seen_at', ''),
            item.get('activated_at', ''), item.get('deactivated_at', ''),
            1 if item.get('new_in_latest_snapshot', False) else 0,
            json.dumps(item.get('extra_fields', {}), ensure_ascii=False, separators=(',', ':')),
            0,
        ))
    con.executemany('INSERT INTO medicines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    con.commit()
    count = con.execute('SELECT COUNT(*) FROM medicines').fetchone()[0]
    fts_count = con.execute("SELECT COUNT(*) FROM medicine_fts").fetchone()[0]
    search_count = con.execute("SELECT COUNT(*) FROM medicine_fts WHERE medicine_fts MATCH ?", ('gardasil*',)).fetchone()[0]
    ingredient_count = con.execute("SELECT COUNT(*) FROM medicines WHERE normalizedActiveIngredient != ''").fetchone()[0]
    integrity = con.execute('PRAGMA integrity_check').fetchone()[0]
    if count != len(master) or fts_count != count or integrity != 'ok' or search_count < 1:
        raise RuntimeError({'count': count, 'expected': len(master), 'fts_count': fts_count, 'integrity': integrity, 'search_count': search_count})
    con.execute('ANALYZE')
    con.commit()
    con.close()
    print(json.dumps({'output': str(args.output), 'records': count, 'fts_records': fts_count, 'ingredient_records': ingredient_count, 'gardasil_matches': search_count, 'integrity': integrity, 'bytes': args.output.stat().st_size}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
