#!/usr/bin/env python3
"""Watch a KTX time window and reserve one seated ticket when available."""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import ktx_booking as kb  # noqa: E402


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_interval_sequence(value: str) -> list[int]:
    intervals: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            seconds = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid interval value: {item!r}") from exc
        if seconds <= 0:
            raise argparse.ArgumentTypeError("interval values must be positive seconds")
        intervals.append(seconds)
    if not intervals:
        raise argparse.ArgumentTypeError("at least one interval value is required")
    return intervals


def in_window(train, args: argparse.Namespace) -> bool:
    return (
        args.start_time <= train.dep_time <= args.end_time
        and train.dep_name == args.dep
        and train.arr_name == args.arr
    )


def matching_reservation(client, args: argparse.Namespace):
    for reservation in client.reservations():
        if (
            reservation.dep_name == args.dep
            and reservation.arr_name == args.arr
            and reservation.dep_date == args.date
            and args.start_time <= reservation.dep_time <= args.end_time
        ):
            return reservation
    return None


def reservation_message(reservation, *, existing: bool = False) -> str:
    label = "기존 예약 감지" if existing else "자동예약 성공"
    verb = "이미 예약되어 있습니다" if existing else "예약했습니다"
    date = f"{int(reservation.dep_date[4:6])}월 {int(reservation.dep_date[6:8])}일"
    dep_time = f"{reservation.dep_time[0:2]}:{reservation.dep_time[2:4]}"
    deadline = ""
    if getattr(reservation, "buy_limit_date", None) and getattr(reservation, "buy_limit_time", None):
        deadline = f" 구입기한은 {reservation.buy_limit_date} {reservation.buy_limit_time}이니 결제해 주세요."
    return (
        f"KTX {label}: {date} {reservation.dep_name}→{reservation.arr_name} "
        f"{dep_time} 출발 {reservation.train_no}열차 일반/특실 좌석을 {verb}."
        f"{deadline}"
    )


class WindowWatcher:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state_dir = Path(args.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.stop_path = self.state_dir / "stop.flag"
        self.pid_path = self.state_dir / "watcher.pid"
        self.state: dict[str, object] = {}
        self.client = None
        self.intervals = itertools.cycle(args.interval_sequence)

    def log(self, message: str) -> None:
        print(f"[{now_iso()}] {message}", flush=True)

    def save_state(self, **updates: object) -> None:
        self.state.update(updates)
        self.state["updated_at"] = now_iso()
        self.state["config"] = {
            "dep": self.args.dep,
            "arr": self.args.arr,
            "date": self.args.date,
            "start_time": self.args.start_time,
            "end_time": self.args.end_time,
            "train_type": self.args.train_type,
            "interval_sequence": self.args.interval_sequence,
        }
        self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def finish(self, status: str, reason: str) -> None:
        self.save_state(status=status, stop_reason=reason, pid=None)
        try:
            if self.pid_path.exists() and self.pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                self.pid_path.unlink()
        except OSError:
            pass

    def notify(self, message: str) -> None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=20,
        )
        payload = response.json()
        if not response.ok or not payload.get("ok"):
            raise RuntimeError(f"Telegram notification failed: HTTP {response.status_code}")

    def get_client(self):
        if self.client is None:
            self.client = kb.build_client()
        return self.client

    def run(self) -> int:
        self.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        self.save_state(status="starting", pid=os.getpid(), started_at=now_iso())
        self.log(f"KTX window watcher started: {self.args.dep}->{self.args.arr} {self.args.start_time}-{self.args.end_time}")

        try:
            self.get_client()
            if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
                raise RuntimeError("Telegram credentials are missing")
        except Exception as exc:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            self.log(detail)
            self.finish("blocked", "startup_validation_failed")
            self.save_state(last_error=str(exc), last_error_detail=detail)
            return 2

        loop = 0
        while not self.stop_path.exists():
            loop += 1
            try:
                client = self.get_client()
                existing = matching_reservation(client, self.args)
                if existing is not None:
                    message = reservation_message(existing, existing=True)
                    self.notify(message)
                    self.save_state(last_notification_at=now_iso(), last_notification_ok=True, last_notification_message=message)
                    self.finish("done", "existing_reservation")
                    return 0

                try:
                    trains = client.search_train(
                        self.args.dep,
                        self.args.arr,
                        self.args.date,
                        self.args.start_time,
                        train_type=kb.TRAIN_TYPE_MAP[self.args.train_type],
                        passengers=kb.parse_passengers(self.args),
                        include_no_seats=False,
                        include_waiting_list=False,
                    )
                except kb.NoResultsError:
                    trains = []
                candidates = [train for train in trains if in_window(train, self.args) and train.has_seat()]
                candidates.sort(key=lambda train: (train.dep_time, int(train.train_no)))
                if candidates:
                    train = candidates[0]
                    self.log(f"seat detected on KTX {train.train_no} at {train.dep_time}; attempting reserve")
                    reservation = client.reserve(
                        train,
                        passengers=kb.parse_passengers(self.args),
                        option=kb.RESERVE_OPTION_MAP["general-first"],
                        try_waiting=False,
                        allow_standing=False,
                    )
                    message = reservation_message(reservation)
                    self.notify(message)
                    self.save_state(
                        status="done",
                        stop_reason="reserved_and_notified",
                        reservation=kb.normalize_reservation(reservation),
                        last_notification_at=now_iso(),
                        last_notification_ok=True,
                        last_notification_message=message,
                        pid=None,
                    )
                    self.finish("done", "reserved_and_notified")
                    return 0

                interval = next(self.intervals)
                self.save_state(status="watching", loop_count=loop, next_poll_seconds=interval, last_checked_at=now_iso())
                self.log(f"no seated KTX available; sleeping {interval}s")
                time.sleep(interval)
            except Exception as exc:
                detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
                self.client = None
                self.save_state(status="error_retrying", last_error=str(exc), last_error_detail=detail, last_error_at=now_iso())
                self.log(f"error: {exc}; retrying in 60s")
                time.sleep(60)

        self.finish("stopped", "stop_requested")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch a KTX time window and reserve one seated ticket")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--dep", required=True)
    parser.add_argument("--arr", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--train-type", choices=sorted(kb.TRAIN_TYPE_MAP), default="ktx")
    parser.add_argument("--interval-sequence", type=parse_interval_sequence, default=parse_interval_sequence("7,14,20,17,13,10,16,21,26,15,23"))
    parser.add_argument("--adults", type=int, default=1)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--toddlers", type=int, default=0)
    parser.add_argument("--seniors", type=int, default=0)
    return parser


if __name__ == "__main__":
    raise SystemExit(WindowWatcher(build_parser().parse_args()).run())
