#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv sync --locked
sh scripts/setup-gitleaks.sh
uv run --locked pre-commit install --hook-type pre-commit --hook-type pre-push
