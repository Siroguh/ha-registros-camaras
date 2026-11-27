#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked python -m compileall -q custom_components package.py
uv run --locked pytest -q
uv run --locked python package.py
