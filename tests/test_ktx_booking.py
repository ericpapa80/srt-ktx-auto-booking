from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pytest

import ktx_booking as kb


def test_cancel_does_not_report_success_when_api_returns_false(monkeypatch):
    reservation = type("Reservation", (), {"rsv_id": "R-1"})()

    class DummyClient:
        def reservations(self):
            return [reservation]

        def cancel(self, _reservation):
            return False

    monkeypatch.setattr(kb, "build_client", lambda: DummyClient())

    with pytest.raises(kb.KorailError, match="failed to cancel reservation R-1"):
        kb.command_cancel(Namespace(reservation_id="R-1"))
