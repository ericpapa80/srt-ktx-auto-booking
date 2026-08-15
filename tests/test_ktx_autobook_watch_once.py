from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ktx_autobook_watch_once as watcher


def test_reservation_precheck_propagates_api_errors():
    class BrokenClient:
        def reservations(self):
            raise RuntimeError("reservation API unavailable")

    args = Namespace(
        train_no="4054",
        dep="순천",
        arr="서대전",
        date="20260503",
        dep_time="164800",
    )

    with pytest.raises(RuntimeError, match="reservation API unavailable"):
        watcher.existing_matching_reservation(BrokenClient(), args)
