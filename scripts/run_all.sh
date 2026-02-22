#!/usr/bin/env bash
# Convenience wrapper: run the whole pipeline with one command.
set -euo pipefail

CONFIG_PATH="${1:-config/config.yaml}"
# The Python CLI handles sequencing; shell script only passes the config.
python -m src.main all --config "$CONFIG_PATH"
