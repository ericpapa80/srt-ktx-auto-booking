#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

HERMES_AGENT = Path('/Users/ldh/.hermes/hermes-agent')
if str(HERMES_AGENT) not in sys.path:
    sys.path.insert(0, str(HERMES_AGENT))

from gateway.config import Platform, load_gateway_config  # type: ignore
from tools.send_message_tool import _send_to_platform  # type: ignore


def extract_payload(line: str) -> dict:
    m = re.search(r'(RESERVATION_SUCCESS|RESERVATION_ALREADY_EXISTS)\s+(\{.*\})', line)
    if not m:
        return {}
    try:
        return json.loads(m.group(2))
    except Exception:
        return {"raw": line.strip()}


def yyyymmdd_to_kr(value: str | None) -> str:
    if not value or len(value) < 8:
        return "날짜 미상"
    return f"{int(value[4:6])}월 {int(value[6:8])}일"


def hhmmss_to_hhmm(value: str | None) -> str:
    if not value or len(value) < 4:
        return "시간 미상"
    return f"{value[0:2]}:{value[2:4]}"


def compact_success_message(marker: str, payload: dict) -> str:
    label = "자동예약 성공" if marker == "RESERVATION_SUCCESS" else "기존 예약 감지"
    verb = "예약했습니다" if marker == "RESERVATION_SUCCESS" else "이미 예약되어 있습니다"
    dep_date = yyyymmdd_to_kr(str(payload.get("dep_date") or ""))
    dep = payload.get("dep_name") or "?"
    arr = payload.get("arr_name") or "?"
    dep_time = hhmmss_to_hhmm(str(payload.get("dep_time") or ""))
    train_no = payload.get("train_no") or "?"
    seat_count = int(payload.get("seat_count") or 1)
    buy_limit_date = str(payload.get("buy_limit_date") or "")
    buy_limit_time = str(payload.get("buy_limit_time") or "")
    deadline = f"{buy_limit_date} {buy_limit_time}".strip() or "확인 필요"
    return (
        f"KTX {label}: {dep_date} {dep}→{arr} {dep_time} 출발 {train_no}열차 "
        f"일반실 {seat_count}석을 {verb}. 현재 이 열차 누적 예약 좌석은 {seat_count}석입니다. "
        f"구입기한은 {deadline}이니 결제해 주세요. "
        f"중지 요청을 받을 때까지 1석 예약을 계속 시도하겠습니다."
    )


def format_message(marker: str, payload: dict, log_path: Path) -> str:
    if payload:
        return compact_success_message(marker, payload)
    label = "예약 성공" if marker == "RESERVATION_SUCCESS" else "기존 예약 감지"
    parts = [f"KTX 자동예약 {label} 알림"]
    if payload:
        rid = payload.get('reservation_id')
        if rid:
            parts.append(f"예약번호: {rid}")
        train_no = payload.get('train_no')
        train_type = payload.get('train_type')
        if train_no or train_type:
            parts.append(f"열차: {train_type or ''} {train_no or ''}".strip())
        dep = payload.get('dep_name')
        arr = payload.get('arr_name')
        dd = payload.get('dep_date')
        dt = payload.get('dep_time')
        ad = payload.get('arr_date')
        at = payload.get('arr_time')
        if dep or arr or dd or dt:
            parts.append(f"구간/시간: {dep or '?'}→{arr or '?'} {dd or ''} {dt or ''} 도착 {ad or ''} {at or ''}".strip())
        seat = payload.get('seat_count')
        price = payload.get('price')
        if seat or price:
            parts.append(f"좌석/금액: {seat or '?'}석, {price or '?'}원")
        bld = payload.get('buy_limit_date')
        blt = payload.get('buy_limit_time')
        if bld or blt:
            parts.append(f"결제기한: {bld or ''} {blt or ''}".strip())
        desc = payload.get('description')
        if desc:
            parts.append(str(desc))
    parts.append("코레일 앱/웹에서 결제기한 내 결제 확인해 주세요.")
    return "\n".join(parts)


async def send_telegram(text: str, chat_id: str):
    cfg = load_gateway_config()
    pconfig = cfg.platforms.get(Platform.TELEGRAM)
    if not pconfig or not pconfig.enabled:
        raise RuntimeError('telegram platform is not configured/enabled')
    return await _send_to_platform(Platform.TELEGRAM, pconfig, chat_id, text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--log', required=True)
    ap.add_argument('--sentinel', required=True)
    ap.add_argument('--chat-id', default=os.environ.get('TELEGRAM_CHAT_ID'))
    ap.add_argument('--poll', type=float, default=2.0)
    args = ap.parse_args()
    if not args.chat_id:
        ap.error('--chat-id or TELEGRAM_CHAT_ID is required')
    log_path = Path(args.log)
    sentinel = Path(args.sentinel)
    print(f"MONITOR_STARTED log={log_path} sentinel={sentinel}", flush=True)
    while True:
        if sentinel.exists():
            print('MONITOR_EXIT sentinel_exists', flush=True)
            return 0
        try:
            text = log_path.read_text(encoding='utf-8', errors='replace') if log_path.exists() else ''
        except Exception as exc:
            print(f'MONITOR_WARN read_failed {type(exc).__name__}: {exc}', flush=True)
            text = ''
        for marker in ('RESERVATION_SUCCESS', 'RESERVATION_ALREADY_EXISTS'):
            idx = text.find(marker)
            if idx >= 0:
                line = text[idx:].splitlines()[0]
                payload = extract_payload(line)
                msg = format_message(marker, payload, log_path)
                try:
                    result = asyncio.run(send_telegram(msg, str(args.chat_id)))
                    sentinel.write_text(f'{time.time()} {marker}\n{line}\nresult={result}\n', encoding='utf-8')
                    print(f'MONITOR_NOTIFIED {marker} result={result}', flush=True)
                    return 0
                except Exception as exc:
                    print(f'MONITOR_ERROR notify_failed {type(exc).__name__}: {exc}', flush=True)
        time.sleep(args.poll)


if __name__ == '__main__':
    raise SystemExit(main())
