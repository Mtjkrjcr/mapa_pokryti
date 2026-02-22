"""Merge per-node binary viewshed rasters into one count raster."""

from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject


def merge_binary_rasters(raster_paths: list[str], out_path: str, template_path: Optional[str] = None) -> dict:
    """Reproject each binary raster into a shared grid and sum visible pixels."""
    if not raster_paths:
        raise ValueError("No viewshed rasters provided for merge")

    base = Path(template_path) if template_path else Path(raster_paths[0])
    with rasterio.open(base) as src0:
        profile = src0.profile.copy()
        profile.update(dtype=rasterio.uint32, count=1, compress="deflate", predictor=2, nodata=0)
        acc = np.zeros((src0.height, src0.width), dtype=np.uint32)
        dst_transform = src0.transform
        dst_crs = src0.crs

    for p in raster_paths:
        # Viewshed outputs can differ slightly in grid alignment; reproject to base grid first.
        with rasterio.open(p) as src:
            src_arr = src.read(1)
            dst_arr = np.zeros_like(acc, dtype=np.uint8)
            reproject(
                source=(src_arr > 0).astype(np.uint8),
                destination=dst_arr,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                src_nodata=0,
                dst_nodata=0,
                resampling=Resampling.nearest,
            )
            acc += dst_arr.astype(np.uint32)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **profile) as dst:
        dst.write(acc, 1)

    return {"coverage_tif": str(out), "max_count": int(acc.max()), "sum": int(acc.sum())}
