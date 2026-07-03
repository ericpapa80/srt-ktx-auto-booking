#!/usr/bin/env bash
set -euo pipefail

# Quick example:
#   edit /Users/ldh/.config/srt-ktx-auto-booking/.env
#   bash examples/ktx_reservations_example.sh

python3 scripts/ktx_booking.py reservations
