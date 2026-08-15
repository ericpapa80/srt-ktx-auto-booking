from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import srt_watchctl as ctl


def start_args(state_root: Path, env_file: Path) -> Namespace:
    return Namespace(
        name="demo",
        dep="수서",
        arr="대전",
        date="20260624",
        start_time="200000",
        end_time="205959",
        target_train_number=None,
        target_dep_time=None,
        mode="target-total",
        poll_sequence=[7],
        poll_seconds=60,
        seat_preference="general-first",
        standby_action="notify",
        standby_phone=None,
        notify="stdout",
        telegram_chat_id=None,
        openclaw_target=None,
        openclaw_channel="telegram",
        openclaw_account="default",
        env_file=str(env_file),
        state_root=str(state_root),
    )


def test_start_clears_stop_flag_after_terminal_run(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    state_dir = state_root / "demo"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps({"status": "stopped", "pid": 999999}), encoding="utf-8"
    )
    (state_dir / "watcher.pid").write_text("999999", encoding="utf-8")
    stop_flag = state_dir / "stop.flag"
    stop_flag.write_text("stop", encoding="utf-8")

    class DummyProcess:
        pid = 12345

    monkeypatch.setattr(ctl.subprocess, "Popen", lambda *args, **kwargs: DummyProcess())

    assert ctl.command_start(start_args(state_root, tmp_path / "missing.env")) == 0
    assert not stop_flag.exists()
    assert (state_dir / "watcher.pid").read_text(encoding="utf-8") == "12345"


def test_stop_does_not_signal_pid_from_terminal_state(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    state_dir = state_root / "demo"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(
        json.dumps({"status": "done", "pid": 12345}), encoding="utf-8"
    )
    (state_dir / "watcher.pid").write_text("12345", encoding="utf-8")

    signalled = []
    monkeypatch.setattr(ctl, "pid_alive", lambda pid: True)
    monkeypatch.setattr(ctl.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    args = Namespace(state_root=str(state_root), name="demo")
    assert ctl.command_stop(args) == 0
    assert signalled == []
