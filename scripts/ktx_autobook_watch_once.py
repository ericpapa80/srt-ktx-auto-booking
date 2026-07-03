#!/usr/bin/env python3
"""Watch a Korail/KTX-family train and reserve one general seat when available.

Purpose-built for unattended monitoring from Hermes. It exits after one successful
reservation and prints stable markers for background notifications.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import ktx_booking as kb  # noqa: E402


def ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_interval_sequence(value: str) -> list[int]:
    intervals: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            interval = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid interval value: {item!r}") from exc
        if interval <= 0:
            raise argparse.ArgumentTypeError("interval values must be positive seconds")
        intervals.append(interval)
    if not intervals:
        raise argparse.ArgumentTypeError("at least one interval value is required")
    return intervals


def train_matches(train, args: argparse.Namespace) -> bool:
    return (
        getattr(train, "train_no", None) == args.train_no
        and getattr(train, "dep_name", None) == args.dep
        and getattr(train, "arr_name", None) == args.arr
        and getattr(train, "dep_date", None) == args.date
        and getattr(train, "dep_time", None) == args.dep_time
    )


def existing_matching_reservation(client, args: argparse.Namespace):
    try:
        for r in client.reservations():
            if (
                getattr(r, "train_no", None) == args.train_no
                and getattr(r, "dep_name", None) == args.dep
                and getattr(r, "arr_name", None) == args.arr
                and getattr(r, "dep_date", None) == args.date
                and getattr(r, "dep_time", None) == args.dep_time
            ):
                return r
    except Exception as exc:  # read-only guard; do not fail watcher startup solely on list issue
        print(f"{ts()} WARN reservation_precheck_failed type={type(exc).__name__} msg={exc}", flush=True)
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dep", required=True)
    parser.add_argument("--arr", required=True)
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    parser.add_argument("--dep-time", required=True, help="HHMMSS")
    parser.add_argument("--train-no", required=True)
    parser.add_argument("--train-type", default="all", choices=sorted(kb.TRAIN_TYPE_MAP))
    parser.add_argument(
        "--interval-sequence",
        type=parse_interval_sequence,
        default=parse_interval_sequence("7,14,20,17,13,10,16,21,26,15,23"),
        help="Comma-separated polling intervals in seconds, repeated cyclically. Default: 7,14,20,17,13,10,16,21,26,15,23",
    )
    parser.add_argument("--interval", type=int, default=20, help="Deprecated fallback; interval-sequence is used by default")
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--toddlers", type=int, default=0)
    parser.add_argument("--seniors", type=int, default=0)
    parser.add_argument("--max-loops", type=int, default=0, help="0 means infinite")
    args = parser.parse_args()
    interval_cycle = itertools.cycle(args.interval_sequence)
    interval_label = ",".join(str(v) for v in args.interval_sequence)

    print(f"{ts()} BOOT starting Korail client/login", flush=True)
    client = kb.build_client()
    passengers = kb.parse_passengers(args)

    preexisting = existing_matching_reservation(client, args)
    if preexisting is not None:
        payload = kb.normalize_reservation(preexisting)
        print(f"{ts()} RESERVATION_ALREADY_EXISTS " + json.dumps(payload, ensure_ascii=False), flush=True)
        return 0

    print(
        f"{ts()} WATCH_STARTED dep={args.dep} arr={args.arr} date={args.date} "
        f"dep_time={args.dep_time} train_no={args.train_no} interval_sequence={interval_label}s seat=general-only adults={args.adults}",
        flush=True,
    )

    loop = 0
    consecutive_errors = 0
    while True:
        loop += 1
        try:
            trains = client.search_train(
                args.dep,
                args.arr,
                args.date,
                args.dep_time,
                train_type=kb.TRAIN_TYPE_MAP[args.train_type],
                passengers=passengers,
                include_no_seats=True,
                include_waiting_list=False,
            )
            target = next((t for t in trains if train_matches(t, args)), None)
            if target is None:
                print(f"{ts()} LOOP {loop} target_not_found", flush=True)
            else:
                info = kb.normalize_train(target, 1)
                print(
                    f"{ts()} LOOP {loop} found general={info['has_general_seat']} "
                    f"special={info['has_special_seat']} waiting={info['has_waiting_list']} desc={info['description']}",
                    flush=True,
                )
                if target.has_general_seat():
                    reservation = client.reserve(
                        target,
                        passengers=passengers,
                        option=kb.RESERVE_OPTION_MAP["general-only"],
                        try_waiting=False,
                        allow_standing=False,
                    )
                    payload = kb.normalize_reservation(reservation)
                    print(f"{ts()} RESERVATION_SUCCESS " + json.dumps(payload, ensure_ascii=False), flush=True)
                    return 0
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            print(f"{ts()} ERROR loop={loop} consecutive={consecutive_errors} type={type(exc).__name__} msg={exc}", flush=True)
            # Keep watching through transient Korail/API errors.

        if args.max_loops and loop >= args.max_loops:
            print(f"{ts()} WATCH_ENDED max_loops_reached", flush=True)
            return 2
        sleep_seconds = next(interval_cycle)
        print(f"{ts()} SLEEP next_interval={sleep_seconds}s", flush=True)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
