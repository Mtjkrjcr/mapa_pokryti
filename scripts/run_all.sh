#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-config/config.yaml}"
python -m src.main all --config "$CONFIG_PATH"
