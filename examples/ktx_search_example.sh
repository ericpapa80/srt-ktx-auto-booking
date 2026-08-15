#!/usr/bin/env bash
set -euo pipefail

# Quick example:
#   edit ~/.config/srt-ktx-auto-booking/.env
#   bash examples/ktx_search_example.sh

python3 scripts/ktx_booking.py search 서울 부산 20260328 090000 --limit 5
