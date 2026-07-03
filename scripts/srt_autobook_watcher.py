#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
from SRT import SRT, SeatType
from SRT.passenger import Adult
from SRT.reservation import SRTReservation
from SRT.train import SRTTrain

try:
    from env_utils import SHARED_ENV_PATH, default_env_candidates, load_env_candidates
except ModuleNotFoundError:  # allow `python -m` / package-style imports in tests
    from scripts.env_utils import SHARED_ENV_PATH, default_env_candidates, load_env_candidates


DEFAULT_SECRETS_PATH = SHARED_ENV_PATH


@dataclass
class TrainSnapshot:
    train_number: str
    dep_time: str
    arr_time: str
    general_seat_state: str | None
    special_seat_state: str | None
    reserve_wait_possible_code: str | None
    seat_available: bool
    general_seat_available: bool
    special_seat_available: bool
    reserve_standby_available: bool


def parse_poll_sequence(value: str) -> list[int]:
    intervals: list[int] = []
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            seconds = int(item)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid poll interval value: {item!r}") from exc
        if seconds <= 0:
            raise argparse.ArgumentTypeError("poll interval values must be positive seconds")
        intervals.append(seconds)
    if not intervals:
        raise argparse.ArgumentTypeError("at least one poll interval value is required")
    return intervals


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def hhmmss_to_hhmm(value: str) -> str:
    return f"{value[0:2]}:{value[2:4]}"


def yyyymmdd_to_kr(value: str) -> str:
    return f"{int(value[4:6])}월 {int(value[6:8])}일"


def payment_deadline_text(reservation: SRTReservation) -> str:
    if reservation.paid:
        return "이미 결제됨"
    return f"{reservation.payment_date[4:6]}월 {reservation.payment_date[6:8]}일 {reservation.payment_time[0:2]}:{reservation.payment_time[2:4]}"


def raw_payment_deadline_text(reservation: SRTReservation) -> str:
    if reservation.paid:
        return "이미 결제됨"
    return f"{reservation.payment_date} {reservation.payment_time}"


def booking_success_message(
    service_name: str,
    reservation: SRTReservation,
    reserved_total: int | None,
    continuous: bool,
) -> str:
    total = int(reserved_total or 1)
    tail = "중지 요청을 받을 때까지 1석 예약을 계속 시도하겠습니다." if continuous else "결제 후 예약 상태를 확인해 주세요."
    return (
        f"{service_name} 자동예약 성공: {yyyymmdd_to_kr(reservation.dep_date)} "
        f"{reservation.dep_station_name}→{reservation.arr_station_name} "
        f"{hhmmss_to_hhmm(reservation.dep_time)} 출발 {reservation.train_number}열차 "
        f"일반실 1석을 예약했습니다. 현재 이 열차 누적 예약 좌석은 {total}석입니다. "
        f"구입기한은 {raw_payment_deadline_text(reservation)}이니 결제해 주세요. "
        f"{tail}"
    )


def standby_available_message(service_name: str, train: SRTTrain, *, apply_enabled: bool = False) -> str:
    tail = (
        "자동으로 예약대기 신청을 시도하겠습니다."
        if apply_enabled
        else "일반석은 아직 매진일 수 있으며, 예약대기 신청은 별도 요청 시 진행하겠습니다."
    )
    return (
        f"{service_name} 예약대기 가능 감지: {yyyymmdd_to_kr(train.dep_date)} "
        f"{train.dep_station_name}→{train.arr_station_name} "
        f"{hhmmss_to_hhmm(train.dep_time)} 출발 {train.train_number}열차가 예약대기 가능 상태로 바뀌었습니다. "
        f"{tail}"
    )


def standby_success_message(service_name: str, reservation: SRTReservation) -> str:
    return (
        f"{service_name} 예약대기 신청 성공: {yyyymmdd_to_kr(reservation.dep_date)} "
        f"{reservation.dep_station_name}→{reservation.arr_station_name} "
        f"{hhmmss_to_hhmm(reservation.dep_time)} 출발 {reservation.train_number}열차 예약대기를 신청했습니다. "
        "좌석 배정/결제 안내가 오면 SRT 앱 또는 홈페이지에서 확인해 주세요."
    )


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


class Watcher:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.state_dir = Path(args.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.state_dir / "state.json"
        self.stop_path = self.state_dir / "stop.flag"
        self.pid_path = self.state_dir / "watcher.pid"
        self.state: dict[str, Any] = read_json(self.state_path)
        self._client: Optional[SRT] = None
        self._terminated = False
        self._loop_count = int(self.state.get("loop_count", 0) or 0)
        self._consecutive_errors = 0
        self._poll_interval_cycle = itertools.cycle(args.poll_sequence)
        self._poll_sequence_label = ",".join(str(value) for value in args.poll_sequence)

    def log(self, message: str) -> None:
        print(f"[{now_iso()}] {message}", flush=True)

    def save_state(self, **updates: Any) -> None:
        self.state.update(updates)
        self.state["updated_at"] = now_iso()
        self.state["config"] = {
            "dep": self.args.dep,
            "arr": self.args.arr,
            "date": self.args.date,
            "start_time": self.args.start_time,
            "end_time": self.args.end_time,
            "target_train_number": self.args.target_train_number,
            "target_dep_time": self.args.target_dep_time,
            "mode": self.args.mode,
            "poll_seconds": self.args.poll_seconds,
            "poll_sequence": self.args.poll_sequence,
            "seat_preference": self.args.seat_preference,
            "standby_action": self.args.standby_action,
            "standby_phone": bool(self.args.standby_phone),
            "notify": self.args.notify,
            "telegram_chat_id": self.args.telegram_chat_id,
            "secrets_path": str(self.args.secrets_path),
        }
        atomic_write_json(self.state_path, self.state)

    def handle_signal(self, signum: int, _frame: Any) -> None:
        self._terminated = True
        self.save_state(status="stopped", stop_reason=f"signal:{signum}")

    def should_stop(self) -> bool:
        return self._terminated or self.stop_path.exists()

    def load_credentials(self) -> None:
        candidates = []
        explicit = Path(self.args.secrets_path)
        if explicit not in candidates:
            candidates.append(explicit)
        for path in default_env_candidates():
            if path not in candidates:
                candidates.append(path)

        loaded = load_env_candidates(candidates)

        missing = [key for key in ("KSKILL_SRT_ID", "KSKILL_SRT_PASSWORD") if not os.environ.get(key)]
        if missing:
            searched = ", ".join(str(path) for path in candidates)
            loaded_text = ", ".join(str(path) for path in loaded) if loaded else "없음"
            raise RuntimeError(
                f"missing SRT credentials: {', '.join(missing)}; searched={searched}; loaded={loaded_text}"
            )

    def validate_notify_config(self) -> None:
        if self.args.notify == "telegram":
            token = os.environ.get("TELEGRAM_BOT_TOKEN")
            chat_id = self.args.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
            missing = []
            if not token:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not chat_id:
                missing.append("TELEGRAM_CHAT_ID or --telegram-chat-id")
            if missing:
                raise RuntimeError(f"missing Telegram notification config: {', '.join(missing)}")
        if self.args.notify == "openclaw" and not (self.args.openclaw_target or os.environ.get("OPENCLAW_NOTIFY_TARGET")):
            raise RuntimeError("missing OpenClaw notification config: OPENCLAW_NOTIFY_TARGET or --openclaw-target")


    def get_client(self) -> SRT:
        if self._client is None:
            self.load_credentials()
            self._client = SRT(os.environ["KSKILL_SRT_ID"], os.environ["KSKILL_SRT_PASSWORD"])
        return self._client

    def reset_client(self) -> None:
        self._client = None

    def seat_preference(self) -> SeatType:
        return {
            "general-first": SeatType.GENERAL_FIRST,
            "general-only": SeatType.GENERAL_ONLY,
            "special-first": SeatType.SPECIAL_FIRST,
            "special-only": SeatType.SPECIAL_ONLY,
        }[self.args.seat_preference]

    @staticmethod
    def normalize_train_number(value: Any) -> str:
        text = str(value or "").strip()
        return text.lstrip("0") or text

    def match_reservation(self, reservation: SRTReservation) -> bool:
        if reservation.dep_date != self.args.date:
            return False
        if reservation.dep_station_name != self.args.dep or reservation.arr_station_name != self.args.arr:
            return False
        if not (self.args.start_time <= reservation.dep_time <= self.args.end_time):
            return False
        if self.args.target_train_number and self.normalize_train_number(reservation.train_number) != self.normalize_train_number(self.args.target_train_number):
            return False
        if self.args.target_dep_time and reservation.dep_time != self.args.target_dep_time:
            return False
        return True

    def match_train(self, train: SRTTrain) -> bool:
        if not (self.args.start_time <= train.dep_time <= self.args.end_time):
            return False
        if self.args.target_train_number and train.train_number != self.args.target_train_number:
            return False
        if self.args.target_dep_time and train.dep_time != self.args.target_dep_time:
            return False
        return True

    def list_matching_reservations(self, client: SRT) -> list[SRTReservation]:
        reservations = client.get_reservations(paid_only=False)
        matched = [reservation for reservation in reservations if self.match_reservation(reservation)]
        matched.sort(key=lambda item: (item.dep_time, item.train_number, item.reservation_number))
        return matched

    def reservations_payload(self, reservations: list[SRTReservation]) -> list[dict[str, Any]]:
        return [
            {
                "reservation_number": r.reservation_number,
                "train_number": r.train_number,
                "dep_date": r.dep_date,
                "dep_time": r.dep_time,
                "arr_time": r.arr_time,
                "dep_station_name": r.dep_station_name,
                "arr_station_name": r.arr_station_name,
                "payment_date": r.payment_date,
                "payment_time": r.payment_time,
                "paid": r.paid,
                "total_cost": r.total_cost,
                "seat_count": r.seat_count,
            }
            for r in reservations
        ]

    def train_snapshots(self, trains: list[SRTTrain]) -> list[dict[str, Any]]:
        return [
            asdict(
                TrainSnapshot(
                    train_number=train.train_number,
                    dep_time=train.dep_time,
                    arr_time=train.arr_time,
                    general_seat_state=getattr(train, "general_seat_state", None),
                    special_seat_state=getattr(train, "special_seat_state", None),
                    reserve_wait_possible_code=getattr(train, "reserve_wait_possible_code", None),
                    seat_available=train.seat_available(),
                    general_seat_available=train.general_seat_available(),
                    special_seat_available=train.special_seat_available(),
                    reserve_standby_available=train.reserve_standby_available(),
                )
            )
            for train in trains
        ]

    def fetch_window_trains(self, client: SRT) -> tuple[list[SRTTrain], list[dict[str, Any]]]:
        trains = client.search_train(
            self.args.dep,
            self.args.arr,
            self.args.date,
            self.args.start_time,
            time_limit=self.args.end_time,
            available_only=False,
        )
        matched = [train for train in trains if self.match_train(train)]
        matched.sort(key=lambda item: (item.dep_time, int(item.train_number)))
        return matched, self.train_snapshots(matched)

    def train_bookable_for_preference(self, train: SRTTrain) -> bool:
        if self.args.seat_preference == "general-only":
            return train.general_seat_available()
        if self.args.seat_preference == "special-only":
            return train.special_seat_available()
        return train.seat_available()

    def choose_bookable_train(self, trains: list[SRTTrain]) -> Optional[SRTTrain]:
        for train in trains:
            if self.train_bookable_for_preference(train):
                return train
        return None

    def choose_standby_train(self, trains: list[SRTTrain]) -> Optional[SRTTrain]:
        for train in trains:
            if train.reserve_standby_available():
                return train
        return None

    def notify_standby_available(self, train: SRTTrain) -> None:
        raw_code = getattr(train, "reserve_wait_possible_code", None)
        key = f"{train.dep_date}:{train.train_number}:{train.dep_time}:{raw_code}"
        if self.state.get("last_standby_notification_key") == key:
            return

        message = standby_available_message("SRT", train, apply_enabled=self.args.standby_action == "apply")
        ok, detail = self.notify(message)
        self.save_state(
            last_standby_notification_key=key,
            last_standby_notification_at=now_iso(),
            last_standby_notification_ok=ok,
            last_standby_notification_detail=detail,
            last_standby_notification_message=message,
            last_standby_snapshot=asdict(
                TrainSnapshot(
                    train_number=train.train_number,
                    dep_time=train.dep_time,
                    arr_time=train.arr_time,
                    general_seat_state=getattr(train, "general_seat_state", None),
                    special_seat_state=getattr(train, "special_seat_state", None),
                    reserve_wait_possible_code=raw_code,
                    seat_available=train.seat_available(),
                    general_seat_available=train.general_seat_available(),
                    special_seat_available=train.special_seat_available(),
                    reserve_standby_available=train.reserve_standby_available(),
                )
            ),
        )
        if ok:
            self.log(f"standby notification recorded for train {train.train_number}: {detail}")
        else:
            self.log(f"standby notification failed for train {train.train_number}: {detail}")

    def send_telegram(self, text: str) -> tuple[bool, str]:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = self.args.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return False, "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=20)
        if resp.ok:
            return True, resp.text[:500]
        return False, f"HTTP {resp.status_code}: {resp.text[:500]}"

    def send_openclaw(self, text: str) -> tuple[bool, str]:
        target = self.args.openclaw_target or os.environ.get("OPENCLAW_NOTIFY_TARGET")
        if not target:
            return False, "OPENCLAW_NOTIFY_TARGET missing"

        channel = self.args.openclaw_channel or os.environ.get("OPENCLAW_NOTIFY_CHANNEL") or "telegram"
        account = self.args.openclaw_account or os.environ.get("OPENCLAW_NOTIFY_ACCOUNT")
        openclaw_bin = os.environ.get("OPENCLAW_BIN") or "openclaw"

        cmd = [
            openclaw_bin,
            "message",
            "send",
            "--channel",
            channel,
            "--target",
            target,
            "--message",
            text,
            "--json",
        ]
        if account:
            cmd.extend(["--account", account])
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as exc:
            return False, str(exc)
        detail = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode == 0:
            return True, detail[:500] or "sent"
        return False, detail[:500] or f"openclaw exited {proc.returncode}"

    def notify(self, text: str) -> tuple[bool, str]:
        if self.args.notify == "stdout":
            self.log(f"NOTIFY: {text}")
            return True, "stdout"
        if self.args.notify == "telegram":
            return self.send_telegram(text)
        if self.args.notify == "openclaw":
            return self.send_openclaw(text)
        return False, f"unknown notify mode: {self.args.notify}"

    def notify_booking(self, reservation: SRTReservation, continuous: bool = False, reserved_total: int | None = None) -> None:
        message = booking_success_message("SRT", reservation, reserved_total, continuous)
        ok, detail = self.notify(message)
        self.save_state(
            last_notification_at=now_iso(),
            last_notification_ok=ok,
            last_notification_detail=detail,
            last_notification_message=message,
        )
        if not ok:
            raise RuntimeError(f"notification failed: {detail}")

    def notify_standby_application(self, reservation: SRTReservation) -> None:
        message = standby_success_message("SRT", reservation)
        ok, detail = self.notify(message)
        self.save_state(
            last_standby_application_notification_at=now_iso(),
            last_standby_application_notification_ok=ok,
            last_standby_application_notification_detail=detail,
            last_standby_application_notification_message=message,
        )
        if not ok:
            raise RuntimeError(f"standby application notification failed: {detail}")

    def apply_standby(self, client: SRT, train: SRTTrain) -> SRTReservation:
        kwargs: dict[str, Any] = {
            "passengers": [Adult(1)],
            "special_seat": self.seat_preference(),
        }
        if self.args.standby_phone:
            kwargs["mblPhone"] = self.args.standby_phone
        return client.reserve_standby(train, **kwargs)

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)
        self.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        self.save_state(status="starting", stop_reason=None, pid=os.getpid(), started_at=self.state.get("started_at") or now_iso())
        self.log(
            f"watcher started for {self.args.date} {self.args.dep}->{self.args.arr} {self.args.start_time}-{self.args.end_time}, "
            f"poll_sequence={self._poll_sequence_label}s, mode={self.args.mode}"
        )
        try:
            self.load_credentials()
            self.validate_notify_config()
        except Exception as exc:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            self.log(detail)
            self.save_state(
                status="blocked",
                stop_reason="startup_validation_failed",
                last_error=str(exc),
                last_error_detail=detail,
                last_error_at=now_iso(),
            )
            return 2

        while not self.should_stop():
            self._loop_count += 1
            try:
                client = self.get_client()
                existing = self.list_matching_reservations(client)
                reserved_total = sum(int(getattr(r, "seat_count", 0) or 0) for r in existing)
                self.save_state(
                    status="watching",
                    loop_count=self._loop_count,
                    last_checked_at=now_iso(),
                    matching_reservations=self.reservations_payload(existing),
                    reserved_seat_total=reserved_total,
                    last_error=None,
                )

                trains, snapshots = self.fetch_window_trains(client)
                self.save_state(
                    last_snapshot=snapshots,
                    last_snapshot_at=now_iso(),
                    train_count=len(trains),
                )
                self._consecutive_errors = 0

                if self.args.mode == "target-total" and existing:
                    self.log(f"matching reservation already exists: {existing[0].reservation_number}")
                    self.save_state(status="done", stop_reason="existing_reservation")
                    return 0

                target_train = self.choose_bookable_train(trains)
                if target_train is not None:
                    self.log(
                        f"seat detected on train {target_train.train_number} at {hhmmss_to_hhmm(target_train.dep_time)}; attempting reserve"
                    )
                    reservation = client.reserve(
                        target_train,
                        passengers=[Adult(1)],
                        special_seat=self.seat_preference(),
                    )
                    self.log(f"reservation succeeded: {reservation.reservation_number}")

                    if self.args.mode == "continuous-single":
                        existing = self.list_matching_reservations(client)
                        reserved_total = sum(int(getattr(r, 'seat_count', 0) or 0) for r in existing)
                        self.notify_booking(reservation, continuous=True, reserved_total=reserved_total)
                        continue

                    self.notify_booking(reservation, continuous=False)
                    self.save_state(status="done", stop_reason="reserved_and_notified")
                    return 0

                standby_train = self.choose_standby_train(trains)
                if standby_train is not None:
                    if self.args.standby_action == "apply":
                        self.log(
                            f"reserve standby detected on train {standby_train.train_number} at "
                            f"{hhmmss_to_hhmm(standby_train.dep_time)}; attempting standby application"
                        )
                        reservation = self.apply_standby(client, standby_train)
                        self.log(f"standby application succeeded: {reservation.reservation_number}")
                        self.notify_standby_application(reservation)
                        self.save_state(
                            status="done",
                            stop_reason="standby_applied_and_notified",
                            last_standby_application_at=now_iso(),
                            last_standby_application_reservation=self.reservations_payload([reservation])[0],
                        )
                        return 0

                    self.log(
                        f"reserve standby detected on train {standby_train.train_number} at "
                        f"{hhmmss_to_hhmm(standby_train.dep_time)}; notifying only"
                    )
                    self.notify_standby_available(standby_train)

                sleep_seconds = next(self._poll_interval_cycle)
                self.save_state(next_poll_seconds=sleep_seconds)
                self.log(f"sleeping {sleep_seconds}s before next poll")
                time.sleep(sleep_seconds)
            except Exception as exc:
                detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
                self.reset_client()
                self._consecutive_errors += 1
                backoff = int(min(300 * (1.5 ** (self._consecutive_errors - 1)), 600))
                self.log(f"error #{self._consecutive_errors}; backing off {backoff}s")
                self.log(detail)
                self.save_state(
                    status="error_retrying",
                    last_error=str(exc),
                    last_error_detail=detail,
                    last_error_at=now_iso(),
                    consecutive_errors=self._consecutive_errors,
                    next_backoff_seconds=backoff,
                )
                if self.should_stop():
                    break
                time.sleep(backoff)

        self.save_state(status="stopped", stop_reason="stop_requested")
        self.log("watcher stopped")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Background SRT auto-book watcher")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--dep", required=True)
    parser.add_argument("--arr", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--start-time", required=True)
    parser.add_argument("--end-time", required=True)
    parser.add_argument("--target-train-number")
    parser.add_argument("--target-dep-time")
    parser.add_argument("--mode", choices=["target-total", "continuous-single"], default="target-total")
    parser.add_argument(
        "--poll-sequence",
        type=parse_poll_sequence,
        default=parse_poll_sequence("7,14,20,17,13,10,16,21,26,15,23"),
        help="Comma-separated polling intervals in seconds, repeated cyclically. Default: 7,14,20,17,13,10,16,21,26,15,23",
    )
    parser.add_argument("--poll-seconds", type=int, default=60, help="Deprecated; poll-sequence is used for normal watch polling")
    parser.add_argument("--notify", choices=["stdout", "telegram", "openclaw"], default="stdout")
    parser.add_argument("--telegram-chat-id")
    parser.add_argument("--openclaw-target")
    parser.add_argument("--openclaw-channel", default="telegram")
    parser.add_argument("--openclaw-account", default="default")
    parser.add_argument(
        "--seat-preference",
        default="general-first",
        choices=["general-first", "general-only", "special-first", "special-only"],
    )
    parser.add_argument(
        "--standby-action",
        choices=["notify", "apply"],
        default="notify",
        help="What to do when 예약대기 becomes available: notify only (default) or apply for standby.",
    )
    parser.add_argument("--standby-phone", help="Optional mobile phone number passed to SRT.reserve_standby().")
    parser.add_argument("--secrets-path", type=Path, default=DEFAULT_SECRETS_PATH)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    watcher = Watcher(args)
    return watcher.run()


if __name__ == "__main__":
    raise SystemExit(main())
