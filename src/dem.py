import shutil
import subprocess
import json
from pathlib import Path


def _bbox_from_cfg(cfg: dict) -> tuple[float, float, float, float]:
    aoi = cfg["aoi"]
    if "bbox" in aoi and aoi["bbox"]:
        b = aoi["bbox"]
        return b["minLon"], b["minLat"], b["maxLon"], b["maxLat"]

    if "polygon_geojson" in aoi:
        gj = Path(aoi["polygon_geojson"])
        data = json.loads(gj.read_text(encoding="utf-8"))
        coords = []
        if data.get("type") == "FeatureCollection":
            for f in data.get("features", []):
                geom = f.get("geometry", {})
                if geom.get("type") == "Polygon":
                    for ring in geom.get("coordinates", []):
                        coords.extend(ring)
                elif geom.get("type") == "MultiPolygon":
                    for poly in geom.get("coordinates", []):
                        for ring in poly:
                            coords.extend(ring)
        elif data.get("type") == "Feature":
            geom = data.get("geometry", {})
            if geom.get("type") == "Polygon":
                for ring in geom.get("coordinates", []):
                    coords.extend(ring)
            elif geom.get("type") == "MultiPolygon":
                for poly in geom.get("coordinates", []):
                    for ring in poly:
                        coords.extend(ring)
        elif data.get("type") == "Polygon":
            for ring in data.get("coordinates", []):
                coords.extend(ring)

        if not coords:
            raise ValueError(f"AOI polygon_geojson has no Polygon coordinates: {gj}")

        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        return min(lons), min(lats), max(lons), max(lats)

    raise ValueError("AOI must define either aoi.bbox or aoi.polygon_geojson")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def _utm_epsg_for_lonlat(lon: float, lat: float) -> str:
    zone = int((lon + 180.0) // 6.0) + 1
    if lat >= 0:
        return f"EPSG:{32600 + zone}"
    return f"EPSG:{32700 + zone}"


def prepare_dem(cfg: dict) -> str:
    dem_cfg = cfg["dem"]
    mode = dem_cfg.get("mode", "local")
    raw_dem_path = Path(dem_cfg.get("path", "data/dem/dem.tif"))
    prepared_dem_path = Path(dem_cfg.get("prepared_path", "data/dem/dem_prepared.tif"))
    raw_dem_path.parent.mkdir(parents=True, exist_ok=True)
    prepared_dem_path.parent.mkdir(parents=True, exist_ok=True)

    if mode == "local":
        if not raw_dem_path.exists():
            raise FileNotFoundError(
                f"DEM not found at {raw_dem_path}. Add your DEM.tif or switch dem.mode to srtm."
            )
        source_path = raw_dem_path

    elif mode == "srtm":
        eio = shutil.which("eio")
        if not eio:
            raise RuntimeError(
                "Missing `eio` CLI (package `elevation`). Install requirements and retry, "
                "or use dem.mode=local with your own dem.tif."
            )

        product = dem_cfg.get("srtm_product", "SRTM3")
        min_lon, min_lat, max_lon, max_lat = _bbox_from_cfg(cfg)
        tmp_path = raw_dem_path.parent / "_srtm_raw.tif"

        _run(
            [
                eio,
                "--product",
                str(product),
                "clip",
                "-o",
                str(tmp_path),
                "--bounds",
                f"{min_lon}",
                f"{min_lat}",
                f"{max_lon}",
                f"{max_lat}",
            ]
        )
        source_path = tmp_path
    else:
        raise ValueError(f"Unsupported dem.mode: {mode}")

    gdalwarp = shutil.which("gdalwarp")
    if not gdalwarp:
        raise RuntimeError("gdalwarp not found in PATH")

    min_lon, min_lat, max_lon, max_lat = _bbox_from_cfg(cfg)
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    target_srs = _utm_epsg_for_lonlat(center_lon, center_lat)

    max_distance_m = float(cfg["viewshed"].get("max_distance_m", 20000))
    buffer_deg = max_distance_m / 111320.0
    te_min_lon = min_lon - buffer_deg
    te_min_lat = min_lat - buffer_deg
    te_max_lon = max_lon + buffer_deg
    te_max_lat = max_lat + buffer_deg
    resolution_m = float(cfg["raster"].get("resolution_m", 30))

    _run(
        [
            gdalwarp,
            "-overwrite",
            "-t_srs",
            target_srs,
            "-r",
            "bilinear",
            "-tr",
            str(resolution_m),
            str(resolution_m),
            "-tap",
            "-te",
            str(te_min_lon),
            str(te_min_lat),
            str(te_max_lon),
            str(te_max_lat),
            "-te_srs",
            "EPSG:4326",
            str(source_path),
            str(prepared_dem_path),
        ]
    )
    return str(prepared_dem_path)
