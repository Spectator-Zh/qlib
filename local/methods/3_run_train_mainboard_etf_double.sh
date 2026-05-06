#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CALENDAR_PATH="$ROOT_DIR/instruments/day_mainboard_etf.txt"
TRAIN_SCRIPT="$ROOT_DIR/scripts/train_alpha158_lightgbm.py"
UNIVERSE_FILE="$ROOT_DIR/instruments/mainboard_etf.txt"

if [[ ! -f "$CALENDAR_PATH" ]]; then
  echo "calendar file not found: $CALENDAR_PATH" >&2
  exit 1
fi
if [[ ! -f "$UNIVERSE_FILE" ]]; then
  echo "universe file not found: $UNIVERSE_FILE" >&2
  exit 1
fi

LATEST_DAY="$(tail -n 1 "$CALENDAR_PATH")"

cd "$ROOT_DIR"
python3 "$TRAIN_SCRIPT" \
  --model doubleensemble \
  --kernels 2 \
  --universe-mode file \
  --universe-file "$UNIVERSE_FILE" \
  --calendar-path "$CALENDAR_PATH" \
  --num-boost-round 200 \
  --early-stopping-rounds 50 \
  --num-threads 4 \
  --double-enable-sr \
  --double-enable-fs \
  --double-decay 0.5 \
  --train-start 2015-01-01 \
  --train-end 2022-12-31 \
  --valid-start 2023-01-01 \
  --valid-end 2024-12-31 \
  --test-start 2025-01-01 \
  --test-end "$LATEST_DAY" \
  --label-end "$LATEST_DAY"
