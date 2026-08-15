#!/usr/bin/env bash
set -euo pipefail

# Usage example:
#   mkdir -p ~/.config/srt-ktx-auto-booking
#   cp .env.example ~/.config/srt-ktx-auto-booking/.env
#   edit ~/.config/srt-ktx-auto-booking/.env
#   python3 -m venv .venv
#   source .venv/bin/activate
#   pip install -r requirements.txt
#   bash examples/srt_watch_example.sh

python3 scripts/srt_autobook_watcher.py \
  --state-dir ./state/srt-suseo-daejeon-20260508 \
  --dep 수서 \
  --arr 대전 \
  --date 20260508 \
  --start-time 200000 \
  --end-time 205959 \
  --mode target-total \
  --poll-seconds 20 \
  --seat-preference general-first \
  --notify stdout
