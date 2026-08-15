#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from env_utils import SHARED_ENV_PATH, parse_env_file
except ModuleNotFoundError:  # allow package-style imports in tests
    from scripts.env_utils import SHARED_ENV_PATH, parse_env_file


REPO_ROOT = Path(__file__).resolve().parents[1]
WATCHER = REPO_ROOT / "scripts" / "srt_autobook_watcher.py"
DEFAULT_ENV_PATH = SHARED_ENV_PATH
DEFAULT_STATE_ROOT = REPO_ROOT / "state"
ACTIVE_STATUSES = {"starting", "watching", "error_retrying"}


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


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


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_is_watcher(pid: int) -> bool | None:
    """Return whether pid belongs to this watcher, or None if it cannot be checked."""
    if sys.platform == "win32":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return None
        command = f'(Get-CimInstance Win32_Process -Filter "ProcessId = {pid}").CommandLine'
        cmd = [powershell, "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        cmd = ["ps", "-p", str(pid), "-o", "args="]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    command_line = (result.stdout or "").strip().lower()
    if not command_line:
        return None
    return str(WATCHER).lower() in command_line or WATCHER.name.lower() in command_line


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return value or "srt-watch"


def default_name(args: argparse.Namespace) -> str:
    parts = ["srt", args.date, args.start_time, args.end_time]
    if args.target_train_number:
        parts.append(f"train{args.target_train_number}")
    return safe_name("-".join(parts))


def state_dir_for(args: argparse.Namespace) -> Path:
    name = args.name or default_name(args)
    return Path(args.state_root).expanduser().resolve() / safe_name(name)


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def command_start(args: argparse.Namespace) -> int:
    state_dir = state_dir_for(args)
    state_dir.mkdir(parents=True, exist_ok=True)
    state = read_json(state_dir / "state.json")
    pid = None
    if state.get("status") in ACTIVE_STATUSES:
        pid = int(state.get("pid") or read_pid(state_dir / "watcher.pid") or 0) or None
    if pid and pid_alive(pid):
        identity = process_is_watcher(pid)
        if identity is None:
            print_json({"started": False, "reason": "process_identity_unknown", "pid": pid, "state_dir": str(state_dir)})
            return 1
        if identity:
            print_json({"started": False, "reason": "already_running", "pid": pid, "state_dir": str(state_dir)})
            return 0

    stop_flag = state_dir / "stop.flag"
    if stop_flag.exists():
        stop_flag.unlink()

    env = os.environ.copy()
    if sys.platform == "darwin" and Path("/opt/homebrew/opt/expat/lib").is_dir():
        existing = env.get("DYLD_LIBRARY_PATH", "")
        env["DYLD_LIBRARY_PATH"] = "/opt/homebrew/opt/expat/lib" + (f":{existing}" if existing else "")
    env.update(parse_env_file(Path(args.env_file).expanduser()))
    log_path = state_dir / "watcher.log"
    log_file = log_path.open("a", encoding="utf-8")
    log_file.write(f"\n[{datetime.now().astimezone().isoformat(timespec='seconds')}] launch requested\n")
    log_file.flush()

    cmd = [
        sys.executable,
        str(WATCHER),
        "--state-dir",
        str(state_dir),
        "--dep",
        args.dep,
        "--arr",
        args.arr,
        "--date",
        args.date,
        "--start-time",
        args.start_time,
        "--end-time",
        args.end_time,
        "--mode",
        args.mode,
        "--poll-sequence",
        ",".join(str(value) for value in args.poll_sequence),
        "--poll-seconds",
        str(args.poll_seconds),
        "--seat-preference",
        args.seat_preference,
        "--standby-action",
        args.standby_action,
        "--notify",
        args.notify,
        "--secrets-path",
        str(Path(args.env_file).expanduser()),
    ]
    optional_flags = {
        "--target-train-number": args.target_train_number,
        "--target-dep-time": args.target_dep_time,
        "--telegram-chat-id": args.telegram_chat_id,
        "--openclaw-target": args.openclaw_target,
        "--openclaw-channel": args.openclaw_channel,
        "--openclaw-account": args.openclaw_account,
        "--standby-phone": args.standby_phone,
    }
    for flag, value in optional_flags.items():
        if value:
            cmd.extend([flag, str(value)])

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (state_dir / "watcher.pid").write_text(str(proc.pid), encoding="utf-8")
    print_json({"started": True, "pid": proc.pid, "state_dir": str(state_dir), "log": str(log_path)})
    return 0


def read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def summarize_state(state_dir: Path) -> dict[str, Any]:
    state = read_json(state_dir / "state.json")
    pid = int(state.get("pid") or read_pid(state_dir / "watcher.pid") or 0) or None
    config = state.get("config") or {}
    return {
        "name": state_dir.name,
        "running": pid_alive(pid),
        "pid": pid,
        "status": state.get("status"),
        "stop_reason": state.get("stop_reason"),
        "last_checked_at": state.get("last_checked_at"),
        "loop_count": state.get("loop_count"),
        "reserved_seat_total": state.get("reserved_seat_total"),
        "last_error": state.get("last_error"),
        "config": {
            "dep": config.get("dep"),
            "arr": config.get("arr"),
            "date": config.get("date"),
            "start_time": config.get("start_time"),
            "end_time": config.get("end_time"),
            "target_train_number": config.get("target_train_number"),
            "mode": config.get("mode"),
            "poll_sequence": config.get("poll_sequence"),
            "poll_seconds": config.get("poll_seconds"),
            "next_poll_seconds": state.get("next_poll_seconds"),
            "standby_action": config.get("standby_action"),
            "notify": config.get("notify"),
        },
        "state_dir": str(state_dir),
        "log": str(state_dir / "watcher.log"),
    }


def command_status(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root).expanduser().resolve()
    if args.name:
        dirs = [state_root / safe_name(args.name)]
    else:
        dirs = sorted([path for path in state_root.glob("*") if path.is_dir()])
    print_json({"watchers": [summarize_state(path) for path in dirs]})
    return 0


def command_stop(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root).expanduser().resolve()
    state_dir = state_root / safe_name(args.name)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "stop.flag").write_text(datetime.now().astimezone().isoformat(timespec="seconds"), encoding="utf-8")
    state = read_json(state_dir / "state.json")
    pid = None
    if state.get("status") in ACTIVE_STATUSES:
        pid = int(state.get("pid") or read_pid(state_dir / "watcher.pid") or 0) or None
    signalled = False
    if pid and pid_alive(pid) and process_is_watcher(pid) is True:
        os.kill(pid, signal.SIGTERM)
        signalled = True
    print_json({"stop_requested": True, "pid": pid, "signalled": signalled, "state_dir": str(state_dir)})
    return 0


def add_start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", help="watcher name under state/")
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
    parser.add_argument("--seat-preference", choices=["general-first", "general-only", "special-first", "special-only"], default="general-first")
    parser.add_argument(
        "--standby-action",
        choices=["notify", "apply"],
        default="notify",
        help="What to do when 예약대기 becomes available: notify only (default) or apply for standby.",
    )
    parser.add_argument("--standby-phone", help="Optional mobile phone number passed to SRT.reserve_standby().")
    parser.add_argument("--notify", choices=["stdout", "telegram", "openclaw"], default="openclaw")
    parser.add_argument("--telegram-chat-id")
    parser.add_argument("--openclaw-target")
    parser.add_argument("--openclaw-channel", default="telegram")
    parser.add_argument("--openclaw-account", default="default")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_PATH))
    parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start, stop, and inspect local SRT watchers")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="start a detached SRT watcher")
    add_start_args(start)
    start.set_defaults(func=command_start)

    status = sub.add_parser("status", help="show watcher status")
    status.add_argument("--name")
    status.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    status.set_defaults(func=command_status)

    stop = sub.add_parser("stop", help="stop a watcher by name")
    stop.add_argument("--name", required=True)
    stop.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    stop.set_defaults(func=command_stop)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
