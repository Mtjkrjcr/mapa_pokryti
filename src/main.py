import argparse
import json
from pathlib import Path

import yaml

from .dem import prepare_dem
from .export import export_outputs
from .viewshed import compute_coverage


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def ensure_dirs(cfg: dict) -> None:
    paths = [
        cfg["dem"].get("cache_dir", "data/dem"),
        Path(cfg["dem"].get("path", "data/dem/dem.tif")).parent,
        Path(cfg["dem"].get("prepared_path", "data/dem/dem_prepared.tif")).parent,
        cfg["compute"].get("tmp_dir", "tmp/viewsheds"),
        Path(cfg["output"]["coverage_tif"]).parent,
        Path(cfg["output"]["nodes_geojson"]).parent,
    ]
    for p in paths:
        Path(p).mkdir(parents=True, exist_ok=True)


def cmd_prepare_dem(cfg: dict) -> None:
    ensure_dirs(cfg)
    dem_path = prepare_dem(cfg)
    print(f"DEM ready: {dem_path}")


def cmd_compute(cfg: dict) -> None:
    ensure_dirs(cfg)
    result = compute_coverage(cfg)
    print(json.dumps(result, indent=2))


def cmd_export(cfg: dict) -> None:
    ensure_dirs(cfg)
    result = export_outputs(cfg)
    print(json.dumps(result, indent=2))


def cmd_all(cfg: dict) -> None:
    cmd_prepare_dem(cfg)
    cmd_compute(cfg)
    cmd_export(cfg)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coverage/viewshed pipeline")
    parser.add_argument("command", choices=["prepare-dem", "compute", "export", "all"])
    parser.add_argument("--config", required=True, help="Path to YAML config")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "prepare-dem":
        cmd_prepare_dem(cfg)
    elif args.command == "compute":
        cmd_compute(cfg)
    elif args.command == "export":
        cmd_export(cfg)
    elif args.command == "all":
        cmd_all(cfg)


if __name__ == "__main__":
    main()
