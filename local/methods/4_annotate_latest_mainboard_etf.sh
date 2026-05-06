#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CALENDAR_PATH="$ROOT_DIR/data/cn_day_qlib/calendars/day.txt"

if [[ ! -d "$ROOT_DIR/results" ]]; then
  echo "results dir not found: $ROOT_DIR/results" >&2
  exit 1
fi

if [[ ! -f "$CALENDAR_PATH" ]]; then
  echo "calendar file not found: $CALENDAR_PATH" >&2
  exit 1
fi

LATEST_RUN="$(find "$ROOT_DIR/results" -mindepth 1 -maxdepth 1 -type d -name '*_mainboard_etf*' | sort | tail -n 1)"

if [[ -z "${LATEST_RUN:-}" ]]; then
  echo "no mainboard_etf run found under $ROOT_DIR/results" >&2
  exit 1
fi

cd "$ROOT_DIR"
python3 scripts/annotate_prediction_results.py --run-dir "$LATEST_RUN" --calendar-path "$CALENDAR_PATH" --in-place
