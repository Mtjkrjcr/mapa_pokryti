#!/usr/bin/env python3
"""Enrich GSMweb GSM CSV exports with GPS coordinates scraped from `gps=only` pages."""

from __future__ import annotations

import csv
import html
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path


BASE_URL = "https://gsmweb.cz"
INPUT_CSV = Path("data/gsmweb/all_operators_gsm_utf8.csv")
OUTPUT_CSV = Path("data/gsmweb/all_operators_gsm_utf8_with_coords.csv")
LOOKUP_CSV = Path("data/gsmweb/gsmweb_gps_lookup.csv")

OPERATORS = {
    "o2": {"seznam": "o2", "search_op": "eurotel"},
    "tmobile": {"seznam": "tmobile", "search_op": "t-mobile"},
    "vodafone": {"seznam": "vodafone", "search_op": "oskar"},
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Codex GSMweb coord enrich script)",
}


def fetch(url: str) -> str:
    """Fetch one GSMweb page with a browser-like User-Agent."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    # GSMweb encoding is inconsistent; lossy UTF-8 decode still preserves numeric fields/URLs.
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="ignore")


def strip_tags(value: str) -> str:
    """Convert HTML cell content to normalized plain text."""
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_cid_int(value: str) -> int:
    """Parse normal CID and repeater forms like `12345r`."""
    m = re.match(r"^\s*(\d+)", value or "")
    if not m:
        raise ValueError(f"Bad CID: {value!r}")
    return int(m.group(1), 10)


def parse_districts(seznam: str) -> list[str]:
    """Collect district codes that can be queried with `gps=only`."""
    url = f"{BASE_URL}/seznamy/okresy.php?seznam={urllib.parse.quote(seznam)}"
    page = fetch(url)
    codes = sorted(set(re.findall(r"udaj=([A-Z0-9]{2})&amp;gps=only", page)))
    if not codes:
        raise RuntimeError(f"No district codes found for seznam={seznam}")
    return codes


def parse_search_page(operator_key: str, search_op: str, district: str) -> dict[tuple[str, int, int], tuple[float, float]]:
    """Parse one district search page and extract {(operator, CID, LAC): (lat, lon)}."""
    q = urllib.parse.urlencode(
        {
            "op": search_op,
            "park": "okres",
            "udaj": district,
            "gps": "only",
        }
    )
    url = f"{BASE_URL}/search.php?{q}"
    page = fetch(url)
    out: dict[tuple[str, int, int], tuple[float, float]] = {}
    for row in re.findall(r"<tr\b.*?>.*?</tr>", page, flags=re.IGNORECASE | re.DOTALL):
        if "mapy.com" not in row:
            continue
        tds = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(tds) < 11:
            continue
        # Mapy.com link encodes coordinates as x=lon, y=lat.
        m = re.search(r"[?&]x=([0-9.+-]+)(?:&amp;|&)y=([0-9.+-]+)", row)
        if not m:
            continue
        cid_txt = strip_tags(tds[-11])
        lac_txt = strip_tags(tds[-9])
        if not cid_txt or not lac_txt:
            continue
        try:
            cid = parse_cid_int(cid_txt)
            lac = int(lac_txt, 10)
            lon = float(m.group(1))
            lat = float(m.group(2))
        except ValueError:
            continue
        out[(operator_key, cid, lac)] = (lat, lon)
    return out


def build_lookup() -> dict[tuple[str, int, int], tuple[float, float]]:
    """Build full coordinate lookup across all supported GSM operators and districts."""
    lookup: dict[tuple[str, int, int], tuple[float, float]] = {}
    counts = Counter()
    for operator_key, cfg in OPERATORS.items():
        districts = parse_districts(cfg["seznam"])
        for district in districts:
            page_rows = parse_search_page(operator_key, cfg["search_op"], district)
            counts[operator_key] += len(page_rows)
            for key, value in page_rows.items():
                lookup[key] = value
    print("Lookup rows parsed:", dict(counts), "total", len(lookup))
    return lookup


def write_lookup_csv(lookup: dict[tuple[str, int, int], tuple[float, float]]) -> None:
    """Persist lookup table for debugging/reuse."""
    LOOKUP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with LOOKUP_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["operator", "CID", "LAC", "lat", "lon"])
        for (operator, cid, lac), (lat, lon) in sorted(lookup.items()):
            w.writerow([operator, cid, lac, f"{lat:.12f}".rstrip("0").rstrip("."), f"{lon:.12f}".rstrip("0").rstrip(".")])


def enrich_csv(lookup: dict[tuple[str, int, int], tuple[float, float]]) -> None:
    """Join coordinates into the merged UTF-8 GSM CSV used by this repo."""
    stats = Counter()
    unmatched_by_op = Counter()
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with INPUT_CSV.open("r", newline="", encoding="utf-8") as fin, OUTPUT_CSV.open(
        "w", newline="", encoding="utf-8"
    ) as fout:
        reader = csv.DictReader(fin, delimiter=";")
        fieldnames = list(reader.fieldnames or [])
        for extra in ["lat", "lon", "coord_source"]:
            if extra not in fieldnames:
                fieldnames.append(extra)
        writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for row in reader:
            stats["rows_total"] += 1
            try:
                key = (row["operator"], parse_cid_int(row["CID"]), int(row["LAC"]))
            except Exception:
                stats["rows_bad_key"] += 1
                writer.writerow(row)
                continue
            coords = lookup.get(key)
            if coords:
                lat, lon = coords
                row["lat"] = f"{lat:.12f}".rstrip("0").rstrip(".")
                row["lon"] = f"{lon:.12f}".rstrip("0").rstrip(".")
                row["coord_source"] = "gsmweb_search_gps_only"
                stats["rows_matched"] += 1
            else:
                unmatched_by_op[row.get("operator", "")] += 1
                stats["rows_unmatched"] += 1
            writer.writerow(row)
    print("Enrich stats:", dict(stats))
    if unmatched_by_op:
        print("Unmatched by operator:", dict(unmatched_by_op))


def main() -> int:
    """CLI entrypoint for one-shot GSM enrichment."""
    if not INPUT_CSV.exists():
        print(f"Missing input CSV: {INPUT_CSV}", file=sys.stderr)
        return 1
    lookup = build_lookup()
    write_lookup_csv(lookup)
    enrich_csv(lookup)
    print(f"Wrote: {LOOKUP_CSV}")
    print(f"Wrote: {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
