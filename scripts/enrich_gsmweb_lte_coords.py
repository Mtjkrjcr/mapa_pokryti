#!/usr/bin/env python3
"""Enrich GSMweb LTE exports with GPS and generate a B20-only subset for the map."""

from __future__ import annotations

import csv
import html
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

BASE_URL = "https://gsmweb.cz"
DATA_DIR = Path("data/gsmweb_lte")
OUTPUT_ALL = DATA_DIR / "all_operators_lte_utf8_with_coords.csv"
OUTPUT_B20 = DATA_DIR / "all_operators_lte_b20_utf8_with_coords.csv"
LOOKUP_CSV = DATA_DIR / "gsmweb_lte_gps_lookup.csv"

SOURCE_CSVS = {
    "o2": DATA_DIR / "o2lte.csv",
    "tmobile": DATA_DIR / "tmobile_lte.csv",
    "vodafone": DATA_DIR / "vodafone_lte.csv",
}

OPERATORS = {
    "o2": {"seznam": "o2lte", "search_op": "o2lte"},
    "tmobile": {"seznam": "Tlte", "search_op": "Tlte"},
    "vodafone": {"seznam": "vflte", "search_op": "vflte"},
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Codex GSMweb LTE coord enrich script)"}


def fetch(url: str) -> str:
    """Fetch one GSMweb page with a browser-like User-Agent."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="ignore")


def strip_tags(value: str) -> str:
    """Convert HTML cell content to normalized plain text."""
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_districts(seznam: str) -> list[str]:
    """Collect district codes for one LTE operator listing."""
    page = fetch(f"{BASE_URL}/seznamy/okresy.php?seznam={urllib.parse.quote(seznam)}")
    codes = sorted(set(re.findall(r"udaj=([A-Z0-9]{2})&amp;gps=only", page)))
    if not codes:
        raise RuntimeError(f"No LTE district codes found for seznam={seznam}")
    return codes


def parse_lte_search_page(operator_key: str, search_op: str, district: str) -> dict[tuple[str, int, int], tuple[float, float]]:
    """Parse one LTE district page and return {(operator, CellID, TAC): (lat, lon)}."""
    q = urllib.parse.urlencode({"op": search_op, "park": "okres", "udaj": district, "gps": "only"})
    page = fetch(f"{BASE_URL}/search.php?{q}")
    out: dict[tuple[str, int, int], tuple[float, float]] = {}
    for row in re.findall(r"<tr\b.*?>.*?</tr>", page, flags=re.IGNORECASE | re.DOTALL):
        if "mapy.com" not in row:
            continue
        tds = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(tds) < 9:
            continue
        # LTE search rows contain [CI human, CellID, TAC, Band, PhysCID, Datum, Okr, Umisteni, GPS, ...].
        try:
            cellid_txt = strip_tags(tds[-10])
            tac_txt = strip_tags(tds[-9])
            cellid = int(cellid_txt, 10)
            tac = int(tac_txt, 10)
        except Exception:
            continue
        m = re.search(r"[?&]x=([0-9.+-]+)(?:&amp;|&)y=([0-9.+-]+)", row)
        if not m:
            continue
        lon = float(m.group(1))
        lat = float(m.group(2))
        out[(operator_key, cellid, tac)] = (lat, lon)
    return out


def build_lookup() -> dict[tuple[str, int, int], tuple[float, float]]:
    """Build global coordinate lookup for all LTE operators/districts."""
    lookup: dict[tuple[str, int, int], tuple[float, float]] = {}
    counts = Counter()
    for op_key, cfg in OPERATORS.items():
        for district in parse_districts(cfg["seznam"]):
            rows = parse_lte_search_page(op_key, cfg["search_op"], district)
            counts[op_key] += len(rows)
            lookup.update(rows)
    print("LTE lookup rows parsed:", dict(counts), "total", len(lookup))
    return lookup


def write_lookup(lookup: dict[tuple[str, int, int], tuple[float, float]]) -> None:
    """Persist lookup for debugging/reuse."""
    with LOOKUP_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["operator", "CellID", "TAC", "lat", "lon"])
        for (op, cellid, tac), (lat, lon) in sorted(lookup.items()):
            w.writerow([op, cellid, tac, lat, lon])


def iter_source_rows():
    """Yield raw CSV rows from all LTE exports with normalized operator name."""
    for operator, path in SOURCE_CSVS.items():
        if not path.exists():
            raise FileNotFoundError(path)
        with path.open("r", newline="", encoding="cp1250", errors="replace") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                yield operator, row


def enrich_and_write(lookup: dict[tuple[str, int, int], tuple[float, float]]) -> None:
    """Write full LTE+GPS export and B20-only export used by the web layer."""
    all_rows = []
    stats = Counter()
    for operator, row in iter_source_rows():
        stats["rows_total"] += 1
        out = {"operator": operator, **row}
        try:
            key = (operator, int(row["CellID"]), int(row["TAC"]))
        except Exception:
            stats["rows_bad_key"] += 1
            all_rows.append(out)
            continue
        coords = lookup.get(key)
        if coords:
            lat, lon = coords
            out["lat"] = f"{lat:.12f}".rstrip("0").rstrip(".")
            out["lon"] = f"{lon:.12f}".rstrip("0").rstrip(".")
            out["coord_source"] = "gsmweb_search_gps_only"
            stats["rows_matched"] += 1
        else:
            out["lat"] = ""
            out["lon"] = ""
            out["coord_source"] = ""
            stats["rows_unmatched"] += 1
        all_rows.append(out)

    fieldnames = [
        "operator",
        "CellID",
        "PhysCID",
        "TAC",
        "Band",
        "GSMCID",
        "Datum",
        "Okr",
        "Umisteni",
        "lat",
        "lon",
        "coord_source",
    ]

    with OUTPUT_ALL.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        w.writeheader()
        for row in all_rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})

    with OUTPUT_B20.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        w.writeheader()
        for row in all_rows:
            band = str(row.get("Band", "")).strip()
            # LTE 800 / B20 is the relevant adjacent band for 868 MHz interference checks.
            if band != "800":
                continue
            w.writerow({k: row.get(k, "") for k in fieldnames})

    print("Enrich stats:", dict(stats))
    print("Wrote:", OUTPUT_ALL)
    print("Wrote:", OUTPUT_B20)


def main() -> int:
    """CLI entrypoint for one-shot LTE enrichment."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lookup = build_lookup()
    write_lookup(lookup)
    enrich_and_write(lookup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
