from __future__ import annotations

import sys
import types
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ktx_log_telegram_monitor imports Hermes gateway modules only for delivery.
# Unit tests exercise formatting, so provide light import stubs.
gateway_config = types.ModuleType("gateway.config")
gateway_config.Platform = types.SimpleNamespace(TELEGRAM="telegram")
gateway_config.load_gateway_config = lambda: None
send_message_tool = types.ModuleType("tools.send_message_tool")
send_message_tool._send_to_platform = None
sys.modules.setdefault("gateway.config", gateway_config)
sys.modules.setdefault("tools.send_message_tool", send_message_tool)

import ktx_log_telegram_monitor as ktx_monitor  # noqa: E402
import srt_autobook_watcher as srt_watcher  # noqa: E402


def test_ktx_success_message_uses_compact_user_requested_format():
    payload = {
        "train_no": "4054",
        "train_type": "KTX-산천",
        "dep_name": "순천",
        "arr_name": "서대전",
        "dep_date": "20260503",
        "dep_time": "164800",
        "seat_count": 1,
        "buy_limit_date": "20260424",
        "buy_limit_time": "052533",
    }

    message = ktx_monitor.format_message("RESERVATION_SUCCESS", payload, Path("dummy.log"))

    assert message == (
        "KTX 자동예약 성공: 5월 3일 순천→서대전 16:48 출발 4054열차 "
        "일반실 1석을 예약했습니다. 현재 이 열차 누적 예약 좌석은 1석입니다. "
        "구입기한은 20260424 052533이니 결제해 주세요. "
        "중지 요청을 받을 때까지 1석 예약을 계속 시도하겠습니다."
    )


def test_srt_success_message_uses_compact_user_requested_format(tmp_path):
    class DummyReservation:
        dep_date = "20260503"
        dep_station_name = "순천"
        arr_station_name = "서대전"
        dep_time = "164800"
        train_number = "4054"
        payment_date = "20260424"
        payment_time = "052533"
        paid = False
        total_cost = 23400

    message = srt_watcher.booking_success_message(
        "SRT",
        DummyReservation(),
        reserved_total=1,
        continuous=True,
    )

    assert message == (
        "SRT 자동예약 성공: 5월 3일 순천→서대전 16:48 출발 4054열차 "
        "일반실 1석을 예약했습니다. 현재 이 열차 누적 예약 좌석은 1석입니다. "
        "구입기한은 20260424 052533이니 결제해 주세요. "
        "중지 요청을 받을 때까지 1석 예약을 계속 시도하겠습니다."
    )


def test_srt_standby_messages():
    class DummyTrain:
        dep_date = "20260624"
        dep_station_name = "대전"
        arr_station_name = "수서"
        dep_time = "222000"
        train_number = "372"

    notify_only = srt_watcher.standby_available_message("SRT", DummyTrain())
    apply_enabled = srt_watcher.standby_available_message("SRT", DummyTrain(), apply_enabled=True)

    assert notify_only == (
        "SRT 예약대기 가능 감지: 6월 24일 대전→수서 22:20 출발 372열차가 "
        "예약대기 가능 상태로 바뀌었습니다. 일반석은 아직 매진일 수 있으며, "
        "예약대기 신청은 별도 요청 시 진행하겠습니다."
    )
    assert apply_enabled == (
        "SRT 예약대기 가능 감지: 6월 24일 대전→수서 22:20 출발 372열차가 "
        "예약대기 가능 상태로 바뀌었습니다. 자동으로 예약대기 신청을 시도하겠습니다."
    )


def test_srt_standby_success_message():
    class DummyReservation:
        dep_date = "20260624"
        dep_station_name = "대전"
        arr_station_name = "수서"
        dep_time = "222000"
        train_number = "372"

    assert srt_watcher.standby_success_message("SRT", DummyReservation()) == (
        "SRT 예약대기 신청 성공: 6월 24일 대전→수서 22:20 출발 372열차 예약대기를 신청했습니다. "
        "좌석 배정/결제 안내가 오면 SRT 앱 또는 홈페이지에서 확인해 주세요."
    )


def test_srt_match_reservation_normalizes_leading_zero_train_numbers(tmp_path):
    class DummyReservation:
        dep_date = "20260624"
        dep_station_name = "대전"
        arr_station_name = "수서"
        dep_time = "222000"
        train_number = "00372"

    args = Namespace(
        state_dir=str(tmp_path),
        dep="대전",
        arr="수서",
        date="20260624",
        start_time="220000",
        end_time="223000",
        target_train_number="372",
        target_dep_time=None,
        mode="target-total",
        poll_seconds=60,
        poll_sequence=[7],
        seat_preference="general-first",
        standby_action="notify",
        standby_phone=None,
        notify="stdout",
        telegram_chat_id=None,
        openclaw_target=None,
        openclaw_channel="telegram",
        openclaw_account="default",
        secrets_path=Path("dummy.env"),
    )

    assert srt_watcher.Watcher(args).match_reservation(DummyReservation())
