#!/usr/bin/env python3
"""
Seed crawled Stockopedia ranks into Atlas screener.sqlite.
Updates matching stocks with: stockrank, quality_rank, value_rank, momentum_rank, qm_score, qv_score, stock_style.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "screener.sqlite"
RANKS_PATH = ROOT / "seed" / "stockranks.json"


def seed_stockranks(database: Path = DB_PATH, ranks_path: Path = RANKS_PATH) -> int:
    if not ranks_path.exists():
        print(f"Ranks file not found: {ranks_path}")
        return 0

    with open(ranks_path, encoding="utf-8") as f:
        sr_data = json.load(f)

    sr_map = {}
    for s in sr_data:
        t = s.get("ticker", "")
        if ":" in t:
            ex, code = t.split(":", 1)
            code_clean = code.replace(".", "")
            sr_map[code] = s
            sr_map[code_clean] = s
            if ex == "LON" or s.get("country") == "GB":
                sr_map[code + ".L"] = s
                sr_map[code_clean + ".L"] = s
        if "raw_ticker" in s and ":" in s["raw_ticker"]:
            ex2, code2 = s["raw_ticker"].split(":", 1)
            code2_clean = code2.replace(".", "").replace("-", "")
            sr_map[code2] = s
            sr_map[code2_clean] = s
            if ex2 == "LON" or s.get("country") == "GB":
                sr_map[code2 + ".L"] = s
                sr_map[code2_clean + ".L"] = s

    conn = sqlite3.connect(database)
    cursor = conn.cursor()

    cursor.execute("SELECT symbol, data FROM stocks;")
    rows = cursor.fetchall()

    updated = 0
    updates = []

    for sym, data_str in rows:
        try:
            d = json.loads(data_str)
        except Exception:
            continue

        clean = sym.replace(".L", "").replace(".", "").replace("-", "")
        sp = sr_map.get(sym) or sr_map.get(clean)

        if sp and sp.get("quality") is not None and sp.get("value") is not None and sp.get("stockRank") is not None:
            q = sp["quality"]
            v = sp["value"]
            m = sp.get("momentum")
            sr = sp["stockRank"]
            qm = round((q + m) / 2, 1) if m is not None else None
            qv = round((q + v) / 2, 1)

            d["stockrank"] = sr
            d["quality_rank"] = q
            d["value_rank"] = v
            d["momentum_rank"] = m
            d["qm_score"] = qm
            d["qv_score"] = qv
            d["stock_style"] = sp.get("style", "Neutral")

            updates.append((json.dumps(d, ensure_ascii=False, separators=(",", ":")), sym))
            updated += 1

    cursor.executemany("UPDATE stocks SET data = ? WHERE symbol = ?;", updates)
    conn.commit()
    conn.close()

    print(f"Enriched {updated} stocks in {database} with Stockopedia ranks.")
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--seed", type=Path, default=RANKS_PATH)
    args = parser.parse_args()
    seed_stockranks(args.database, args.seed)


if __name__ == "__main__":
    main()
