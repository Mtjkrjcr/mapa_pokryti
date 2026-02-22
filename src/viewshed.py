import json
import multiprocessing as mp
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol
from rasterio.warp import transform as rio_transform

from .merge import merge_binary_rasters


@dataclass
class Node:
    node_id: str
    name: str
    lat: float
    lon: float
    input_height_m: float
    min_required_height_m: float
    height_m: float
    ground_elev_m: float


def _fresnel_radius_m(distance_m: float, frequency_mhz: float, split_ratio: float = 0.5) -> float:
    # First Fresnel zone radius:
    # r[m] = 17.32 * sqrt((d1_km * d2_km) / (f_GHz * D_km))
    if distance_m <= 0 or frequency_mhz <= 0:
        return 0.0
    d1_km = (distance_m * split_ratio) / 1000.0
    d2_km = (distance_m * (1.0 - split_ratio)) / 1000.0
    d_km = distance_m / 1000.0
    f_ghz = frequency_mhz / 1000.0
    if d1_km <= 0 or d2_km <= 0 or d_km <= 0 or f_ghz <= 0:
        return 0.0
    return 17.32 * np.sqrt((d1_km * d2_km) / (f_ghz * d_km))


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def _load_nodes_dataframe(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Nodes file not found: {p}")

    if p.suffix.lower() == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
        nodes = raw.get("nodes", [])
        if not isinstance(nodes, list):
            raise ValueError("JSON nodes file must contain key `nodes` as array")
        return pd.DataFrame(nodes)

    first_line = p.read_text(encoding="utf-8").splitlines()[0] if p.stat().st_size > 0 else ""
    header_like = all(k in first_line.lower() for k in ["id", "lat", "lon"])

    if header_like:
        return pd.read_csv(p)

    # Fallback for export CSV without header:
    # id,name,lat,lon,type,lastHeardAt
    return pd.read_csv(
        p,
        header=None,
        names=["id", "name", "lat", "lon", "type", "last_heard_at"],
    )


def _estimate_min_height_from_local_terrain(
    src: rasterio.io.DatasetReader,
    ox: float,
    oy: float,
    ground_elev_m: float,
    local_radius_m: float,
    clearance_margin_m: float,
) -> float:
    row, col = rowcol(src.transform, ox, oy)
    pixel_x = abs(float(src.transform.a))
    pixel_y = abs(float(src.transform.e))
    pixel_size_m = max(min(pixel_x, pixel_y), 1e-6)
    px_radius = max(int(np.ceil(local_radius_m / pixel_size_m)), 1)

    r0 = max(row - px_radius, 0)
    r1 = min(row + px_radius + 1, src.height)
    c0 = max(col - px_radius, 0)
    c1 = min(col + px_radius + 1, src.width)
    if r0 >= r1 or c0 >= c1:
        return 0.0

    arr = src.read(1, window=((r0, r1), (c0, c1)), masked=True)
    if arr.size == 0:
        return 0.0
    local_max = float(arr.max())
    needed = local_max - ground_elev_m + clearance_margin_m
    return max(0.0, needed)


def _load_nodes(cfg: dict, dem_path: str) -> list[Node]:
    df = _load_nodes_dataframe(cfg["input"]["nodes_csv"])
    required = {"id", "lat", "lon"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing node columns: {missing}")

    validation = cfg.get("input", {}).get("validation", {})
    stats: dict[str, int] = {}

    rows_input = len(df)
    df = df.copy()
    df["id"] = df["id"].astype(str).str.strip()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    rows_nan = int(df[["id", "lat", "lon"]].isna().any(axis=1).sum())
    df = df.dropna(subset=["id", "lat", "lon"]).copy()

    rows_empty_id = int((df["id"] == "").sum())
    df = df[df["id"] != ""].copy()

    invalid_coord_mask = (df["lat"] < -90) | (df["lat"] > 90) | (df["lon"] < -180) | (df["lon"] > 180)
    rows_invalid_range = int(invalid_coord_mask.sum())
    if bool(validation.get("drop_invalid_coords", True)):
        df = df[~invalid_coord_mask].copy()

    zero_coord_mask = (df["lat"] == 0) & (df["lon"] == 0)
    rows_zero_coords = int(zero_coord_mask.sum())
    if bool(validation.get("drop_zero_coords", True)):
        df = df[~zero_coord_mask].copy()

    rows_before_dedup = len(df)
    if bool(validation.get("dedupe_by_id", True)):
        df = df.drop_duplicates(subset=["id"], keep="first").copy()
    rows_deduped = rows_before_dedup - len(df)

    stats["rows_input"] = int(rows_input)
    stats["rows_dropped_nan"] = int(rows_nan)
    stats["rows_dropped_empty_id"] = int(rows_empty_id)
    stats["rows_dropped_invalid_range"] = int(rows_invalid_range)
    stats["rows_dropped_zero_coords"] = int(rows_zero_coords)
    stats["rows_dropped_duplicate_id"] = int(rows_deduped)
    stats["rows_valid"] = int(len(df))

    rejected_csv = cfg.get("output", {}).get("nodes_rejected_csv")
    if rejected_csv:
        rejected = _load_nodes_dataframe(cfg["input"]["nodes_csv"]).copy()
        rejected["id"] = rejected["id"].astype(str).str.strip()
        rejected["lat"] = pd.to_numeric(rejected["lat"], errors="coerce")
        rejected["lon"] = pd.to_numeric(rejected["lon"], errors="coerce")
        keep_ids = set(df["id"].astype(str).tolist())
        rej = rejected[~rejected["id"].astype(str).isin(keep_ids)].copy()
        out = Path(rejected_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        rej.to_csv(out, index=False)

    default_h = float(cfg["viewshed"].get("observer_height_default_m", 6.0))
    hs = cfg.get("input", {}).get("height_strategy", {})
    height_mode = str(hs.get("mode", "adaptive_min"))
    min_height_m = float(hs.get("min_height_m", 4.0))
    max_height_m = float(hs.get("max_height_m", 120.0))
    local_radius_m = float(hs.get("local_radius_m", 300.0))
    clearance_margin_m = float(hs.get("clearance_margin_m", 2.0))
    use_fresnel = bool(hs.get("use_fresnel", True))
    fresnel_clearance_ratio = float(hs.get("fresnel_clearance_ratio", 0.6))
    fresnel_sample_ratio = float(hs.get("fresnel_sample_ratio", 0.5))
    frequency_mhz = float(cfg.get("radio", {}).get("frequency_mhz", 868.0))
    max_distance_m = float(cfg["viewshed"].get("max_distance_m", 20000))

    nodes: list[Node] = []

    height_column = None
    for c in ["height_m", "height", "antenna_height_m", "observer_height_m"]:
        if c in df.columns:
            height_column = c
            break

    ground_vals: list[float] = []
    input_vals: list[float] = []
    min_required_vals: list[float] = []
    used_vals: list[float] = []
    adapted_count = 0
    fresnel_margin_vals: list[float] = []

    with rasterio.open(dem_path) as src:
        for _, r in df.iterrows():
            raw_h = float(r[height_column]) if height_column and not pd.isna(r[height_column]) else default_h
            xs, ys = rio_transform("EPSG:4326", src.crs, [float(r["lon"])], [float(r["lat"])])
            ox, oy = xs[0], ys[0]

            row, col = rowcol(src.transform, ox, oy)
            ground_elev = np.nan
            min_required_h = 0.0
            if 0 <= row < src.height and 0 <= col < src.width:
                ground_elev = float(src.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
                if height_mode == "adaptive_min":
                    min_required_h = _estimate_min_height_from_local_terrain(
                        src,
                        ox,
                        oy,
                        ground_elev,
                        local_radius_m,
                        clearance_margin_m,
                    )

            fresnel_margin = 0.0
            if use_fresnel:
                fresnel_margin = _fresnel_radius_m(max_distance_m, frequency_mhz, fresnel_sample_ratio)
                fresnel_margin = max(0.0, fresnel_margin * fresnel_clearance_ratio)

            used_h = max(raw_h, min_height_m, min_required_h)
            used_h = max(used_h, min_required_h + fresnel_margin)
            used_h = min(used_h, max_height_m)
            if used_h > raw_h + 1e-9:
                adapted_count += 1

            node_name = str(r["name"]).strip() if "name" in df.columns and not pd.isna(r["name"]) else str(r["id"])
            node = Node(
                str(r["id"]),
                node_name,
                float(r["lat"]),
                float(r["lon"]),
                raw_h,
                min_required_h,
                used_h,
                float(ground_elev) if not np.isnan(ground_elev) else float("nan"),
            )
            nodes.append(node)
            input_vals.append(raw_h)
            min_required_vals.append(min_required_h)
            used_vals.append(used_h)
            ground_vals.append(node.ground_elev_m)
            fresnel_margin_vals.append(fresnel_margin)

    df["height_input_m"] = input_vals
    df["height_min_required_m"] = min_required_vals
    df["height_fresnel_margin_m"] = fresnel_margin_vals
    df["height_used_m"] = used_vals
    df["ground_elev_m"] = ground_vals

    stats["height_mode"] = height_mode
    stats["height_nodes_adapted"] = int(adapted_count)
    stats["height_input_min_m"] = float(np.nanmin(input_vals)) if input_vals else 0.0
    stats["height_input_max_m"] = float(np.nanmax(input_vals)) if input_vals else 0.0
    stats["height_used_min_m"] = float(np.nanmin(used_vals)) if used_vals else 0.0
    stats["height_used_max_m"] = float(np.nanmax(used_vals)) if used_vals else 0.0
    stats["radio_frequency_mhz"] = float(frequency_mhz)
    stats["height_fresnel_enabled"] = bool(use_fresnel)
    stats["height_fresnel_margin_m"] = float(fresnel_margin_vals[0]) if fresnel_margin_vals else 0.0

    cleaned_csv = cfg.get("output", {}).get("nodes_clean_csv")
    if cleaned_csv:
        out = Path(cleaned_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)

    stats_json = cfg.get("output", {}).get("nodes_stats_json")
    if stats_json:
        out = Path(stats_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    return nodes, stats


def _nodes_to_geojson(nodes: list[Node], out_path: str) -> None:
    features = []
    for n in nodes:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": n.node_id,
                    "name": n.name,
                    "height_m": n.height_m,
                    "height_input_m": n.input_height_m,
                    "height_min_required_m": n.min_required_height_m,
                    "ground_elev_m": n.ground_elev_m,
                },
                "geometry": {"type": "Point", "coordinates": [n.lon, n.lat]},
            }
        )

    fc = {"type": "FeatureCollection", "features": features}
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(fc, indent=2), encoding="utf-8")


def _compute_one(args: tuple[dict, Node, str]) -> str:
    cfg, node, dem_path = args
    tmp_dir = Path(cfg["compute"]["tmp_dir"])
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = tmp_dir / f"viewshed_{node.node_id}.tif"

    with rasterio.open(dem_path) as src:
        xs, ys = rio_transform("EPSG:4326", src.crs, [node.lon], [node.lat])
        ox, oy = xs[0], ys[0]

        row, col = rowcol(src.transform, ox, oy)
        if row < 0 or col < 0 or row >= src.height or col >= src.width:
            return ""

    gdal_viewshed = shutil.which("gdal_viewshed")
    if not gdal_viewshed:
        raise RuntimeError("gdal_viewshed not found in PATH")

    vcfg = cfg["viewshed"]
    cmd = [
        gdal_viewshed,
        "-b",
        "1",
        "-ox",
        str(ox),
        "-oy",
        str(oy),
        "-oz",
        str(node.height_m),
        "-tz",
        str(vcfg.get("target_height_m", 0)),
        "-md",
        str(vcfg.get("max_distance_m", 20000)),
        "-vv",
        "1",
        "-iv",
        "0",
        "-ov",
        "0",
    ]

    if bool(vcfg.get("curvature_correction", True)):
        cmd.extend(["-cc", str(vcfg.get("refraction_coeff", 0.13))])

    cmd.extend([dem_path, str(out_path)])
    _run(cmd)
    return str(out_path)


def compute_coverage(cfg: dict) -> dict:
    dem_path = cfg["dem"].get("prepared_path", cfg["dem"]["path"])
    if not Path(dem_path).exists():
        dem_path = cfg["dem"]["path"]
    nodes, node_stats = _load_nodes(cfg, dem_path)

    if not nodes:
        raise ValueError("No nodes found in CSV")

    worker_count = int(cfg["compute"].get("workers", 4))
    parallel = bool(cfg["compute"].get("parallel", True))

    jobs = [(cfg, n, dem_path) for n in nodes]

    if parallel and worker_count > 1:
        with mp.Pool(processes=worker_count) as pool:
            rasters = pool.map(_compute_one, jobs)
    else:
        rasters = [_compute_one(j) for j in jobs]

    rasters = [r for r in rasters if r]
    if not rasters:
        raise ValueError("No viewshed rasters generated. Check AOI/DEM coverage vs node coordinates.")

    merge_result = merge_binary_rasters(
        rasters,
        cfg["output"]["coverage_tif"],
        template_path=dem_path,
    )
    _nodes_to_geojson(nodes, cfg["output"]["nodes_geojson"])

    return {
        "nodes": len(nodes),
        "nodes_skipped_outside_dem": len(nodes) - len(rasters),
        "node_input_stats": node_stats,
        "viewshed_rasters": len(rasters),
        "coverage_tif": cfg["output"]["coverage_tif"],
        "nodes_geojson": cfg["output"]["nodes_geojson"],
        **merge_result,
    }
