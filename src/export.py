"""Export compute outputs into web-friendly assets (PNG/tiles/overlays)."""

import json
import shutil
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from rasterio.warp import transform_bounds


def _run(cmd: list[str]) -> None:
    """Run GDAL helper command and raise with stdout/stderr if it fails."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )


def export_png(cfg: dict) -> dict:
    """Render aggregate coverage raster into transparent PNG + metadata JSON."""
    tif = Path(cfg["output"]["coverage_tif"])
    png = Path(cfg["output"]["coverage_png"])
    binary_png = Path(cfg["output"].get("coverage_binary_png", "out/coverage_binary.png"))
    meta_json = Path(cfg["output"]["coverage_meta_json"])

    if not tif.exists():
        raise FileNotFoundError(f"Coverage GeoTIFF missing: {tif}")

    with rasterio.open(tif) as src:
        arr = src.read(1).astype(np.float32)
        bounds = src.bounds
        b4326 = transform_bounds(src.crs, "EPSG:4326", bounds.left, bounds.bottom, bounds.right, bounds.top)

    # Clip color scale to percentile so a few high-count pixels don't flatten contrast.
    clip_pct = float(cfg["export"].get("png_percentile_clip", 99))
    vmax = np.percentile(arr, clip_pct) if arr.size else 1
    if vmax <= 0:
        vmax = 1

    png.parent.mkdir(parents=True, exist_ok=True)
    cmap = cfg["export"].get("png_colormap", "viridis")

    plt.figure(figsize=(10, 8), dpi=200)
    plt.axis("off")
    plt.imshow(arr, cmap=cmap, vmin=0, vmax=vmax)
    plt.tight_layout(pad=0)
    plt.savefig(png, bbox_inches="tight", pad_inches=0, transparent=True)
    plt.close()

    # Binary red mask for aggregate "red mode" (any coverage > 0).
    red = np.zeros((arr.shape[0], arr.shape[1], 4), dtype=np.uint8)
    m = arr > 0
    red[m, 0] = 255
    red[m, 1] = 70
    red[m, 2] = 60
    red[m, 3] = 170
    binary_png.parent.mkdir(parents=True, exist_ok=True)
    plt.imsave(binary_png, red)

    meta = {
        "bounds_epsg4326": {
            "west": b4326[0],
            "south": b4326[1],
            "east": b4326[2],
            "north": b4326[3],
        },
        "coverage_tif": str(tif),
        "coverage_png": str(png),
        "coverage_binary_png": str(binary_png),
    }
    meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def export_tiles(cfg: dict) -> dict:
    """Optionally generate XYZ tiles for smoother Leaflet rendering."""
    tiles_cfg = cfg["export"].get("tiles", {})
    if not bool(tiles_cfg.get("enabled", False)):
        return {"tiles_enabled": False}

    gdal2tiles = shutil.which("gdal2tiles.py") or shutil.which("gdal2tiles")
    if not gdal2tiles:
        raise RuntimeError("gdal2tiles.py not found in PATH")

    tif = cfg["output"]["coverage_tif"]
    tmp_byte_tif = Path(cfg["compute"].get("tmp_dir", "tmp/viewsheds")) / "coverage_count_byte.tif"
    out_dir = Path(cfg["output"]["tiles_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_byte_tif.parent.mkdir(parents=True, exist_ok=True)

    # Convert count raster to 8-bit display raster before running gdal2tiles.
    with rasterio.open(tif) as src:
        arr = src.read(1).astype(np.float32)
        profile = src.profile.copy()
        vmax = float(arr.max()) if arr.size else 1.0
        if vmax <= 0:
            vmax = 1.0
        scaled = np.clip((arr / vmax) * 255.0, 0, 255).astype(np.uint8)
        profile.update(dtype=rasterio.uint8, count=1, compress="deflate")
        with rasterio.open(tmp_byte_tif, "w", **profile) as dst:
            dst.write(scaled, 1)

    min_z = int(tiles_cfg.get("min_zoom", 8))
    max_z = int(tiles_cfg.get("max_zoom", 14))

    _run(
        [
            gdal2tiles,
            "-z",
            f"{min_z}-{max_z}",
            "-w",
            "none",
            "--xyz",
            str(tmp_byte_tif),
            str(out_dir),
        ]
    )
    return {"tiles_enabled": True, "tiles_dir": str(out_dir), "zoom": f"{min_z}-{max_z}"}


def export_node_overlays(cfg: dict) -> dict:
    """Create one transparent PNG overlay per node for toggle-in-map UX."""
    node_cfg = cfg["export"].get("node_overlays", {})
    if not bool(node_cfg.get("enabled", True)):
        return {"node_overlays_enabled": False}

    viewshed_dir = Path(cfg["compute"].get("tmp_dir", "tmp/viewsheds"))
    out_dir = Path(node_cfg.get("dir", "out/node_overlays"))
    index_path = Path(node_cfg.get("index_json", "out/node_overlays/index.json"))
    color = node_cfg.get("color_rgba", [255, 92, 64, 170])
    visible_threshold = int(node_cfg.get("visible_threshold", 1))

    out_dir.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove stale overlays from previous runs so the index matches the filesystem.
    for old_png in out_dir.glob("*.png"):
        old_png.unlink()

    entries = {}
    count = 0
    for tif in sorted(viewshed_dir.glob("viewshed_*.tif")):
        node_id = tif.stem.replace("viewshed_", "", 1)
        with rasterio.open(tif) as src:
            arr = src.read(1)
            mask = arr >= visible_threshold
            if not mask.any():
                continue

            rgba = np.zeros((src.height, src.width, 4), dtype=np.uint8)
            rgba[mask, 0] = int(color[0])
            rgba[mask, 1] = int(color[1])
            rgba[mask, 2] = int(color[2])
            rgba[mask, 3] = int(color[3])

            png_path = out_dir / f"{node_id}.png"
            plt.imsave(png_path, rgba)

            b = src.bounds
            b4326 = transform_bounds(src.crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)
            entries[node_id] = {
                "png": str(png_path).replace("\\", "/"),
                "bounds_epsg4326": {
                    "west": b4326[0],
                    "south": b4326[1],
                    "east": b4326[2],
                    "north": b4326[3],
                },
            }
            count += 1

    index_path.write_text(json.dumps({"count": count, "nodes": entries}, indent=2), encoding="utf-8")
    return {
        "node_overlays_enabled": True,
        "node_overlays_count": count,
        "node_overlays_dir": str(out_dir),
        "node_overlays_index": str(index_path),
    }


def export_outputs(cfg: dict) -> dict:
    """Run all export sub-steps and combine their metadata."""
    png_meta = export_png(cfg)
    tiles_meta = export_tiles(cfg)
    node_overlay_meta = export_node_overlays(cfg)
    return {"png": png_meta, "tiles": tiles_meta, "node_overlays": node_overlay_meta}
