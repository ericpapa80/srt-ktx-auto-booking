# SRT / KTX Auto Booking

본 레포는 기존의 k-skill을 응용한 연구 목적으로 만들어졌습니다.
이 소스코드는 SRT, KTX가 만들거나 인가하지 않았으며, 언제든지 이용이 제한될 수 있습니다.
즉, 본 레포를 통해 이루어지는 모든 활동에 대한 모든 책임은 전적으로 사용자에게 있습니다.

K-Skill 기반 자산을 바탕으로 정리한 SRT / KTX 예약 도구 모음입니다.
- KTX: 조회, 예약, 예약조회, 취소 CLI
- SRT: 자동감시 및 자동예약 watcher

## 포함 파일
- `scripts/ktx_booking.py` : KTX CLI
- `scripts/srt_autobook_watcher.py` : SRT watcher
- `scripts/srt_watchctl.py` : SRT watcher 시작/상태/중지 컨트롤러
- `scripts/env_utils.py` : `.env` 자동 로더
- `.env.example` : 환경변수 예시
- `examples/` : 실행 예시 스크립트

## 빠른 시작

### 1. 설치
```bash
git clone <YOUR_REPO_URL>
cd srt-ktx-auto-booking
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 환경변수 설정
```bash
mkdir -p ~/.config/srt-ktx-auto-booking
chmod 700 ~/.config/srt-ktx-auto-booking
cp .env.example ~/.config/srt-ktx-auto-booking/.env
chmod 600 ~/.config/srt-ktx-auto-booking/.env
```

필수:
- `KSKILL_SRT_ID`
- `KSKILL_SRT_PASSWORD`
- `KSKILL_KTX_ID`
- `KSKILL_KTX_PASSWORD`

선택:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `OPENCLAW_NOTIFY_TARGET`
- `OPENCLAW_NOTIFY_CHANNEL`
- `OPENCLAW_NOTIFY_ACCOUNT`

bash / zsh:
```bash
# 이제 스크립트가 ~/.config/srt-ktx-auto-booking/.env 를 자동으로 먼저 읽습니다.
# 필요하면 여전히 수동 export 도 가능합니다.
```

PowerShell:
```powershell
Get-Content ~/.config/srt-ktx-auto-booking/.env | ForEach-Object {
  if ($_ -match '^(\s*#|\s*$)') { return }
  $name, $value = $_ -split '=', 2
  [System.Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')
}
```

## KTX 사용법

### 조회
```bash
python3 scripts/ktx_booking.py search 서울 부산 20260328 090000 --limit 5
```

`ktx_booking.py` 는 다음 순서로 자격증명을 찾습니다.
- 현재 셸 환경변수
- `~/.config/srt-ktx-auto-booking/.env`
- 프로젝트 루트의 `.env`
- 기존 호환용 `~/.config/k-skill/secrets.env`

좌석 없음 / 대기 포함:
```bash
python3 scripts/ktx_booking.py search 서울 부산 20260328 090000 --limit 10 --include-no-seats --include-waiting-list
```

### 예약
먼저 `search` 결과의 `train_id` 를 확인한 뒤 예약합니다.
```bash
python3 scripts/ktx_booking.py reserve 서울 부산 20260328 090000 --train-id <train_id> --seat-option general-first
```

2인 예약 예시:
```bash
python3 scripts/ktx_booking.py reserve 서울 부산 20260328 090000 --train-id <train_id> --adults 2 --seat-option general-only
```

### 예약조회
```bash
python3 scripts/ktx_booking.py reservations
```

### KTX 시간대 자동감시
좌석이 있는 첫 열차를 찾아 일반실 우선으로 1석을 예약하고 Telegram으로 알립니다. 입석과 예약대기는 시도하지 않습니다.
```bash
python3 scripts/ktx_autobook_watch_window.py \
  --state-dir ./state/ktx-window-20260815 \
  --dep 서울 \
  --arr 부산 \
  --date 20260815 \
  --start-time 150000 \
  --end-time 180000
```

중지:
```bash
touch ./state/ktx-window-20260815/stop.flag
```

### 취소
```bash
python3 scripts/ktx_booking.py cancel <reservation_id>
```

예시:
```bash
bash examples/ktx_search_example.sh
bash examples/ktx_reservations_example.sh
```

## SRT 사용법

### 추천: 백그라운드 감시 시작
`~/.config/srt-ktx-auto-booking/.env`에 `KSKILL_SRT_ID`, `KSKILL_SRT_PASSWORD`, `OPENCLAW_NOTIFY_TARGET`을 넣으면 별도 Telegram bot token 없이 OpenClaw로 알림을 보낼 수 있습니다.

```bash
.venv/bin/python scripts/srt_watchctl.py start \
  --name suseo-daejeon-20260508 \
  --dep 수서 \
  --arr 대전 \
  --date 20260508 \
  --start-time 200000 \
  --end-time 205959 \
  --poll-seconds 20 \
  --notify openclaw
```

상태 확인:
```bash
.venv/bin/python scripts/srt_watchctl.py status
```

중지:
```bash
.venv/bin/python scripts/srt_watchctl.py stop --name suseo-daejeon-20260508
```

### 기본 실행
```bash
python3 scripts/srt_autobook_watcher.py \
  --state-dir ./state/srt-suseo-daejeon-20260508 \
  --dep 수서 \
  --arr 대전 \
  --date 20260508 \
  --start-time 200000 \
  --end-time 205959 \
  --mode target-total \
  --poll-seconds 20 \
  --seat-preference general-first \
  --notify stdout
```

기존 예약이 있어도 좌석 1석을 추가로 한 번만 예약하려면 `--mode additional-single`을 사용합니다. 입석과 예약대기는 시도하지 않습니다.

`srt_autobook_watcher.py` 도 현재 셸 환경변수 외에 `~/.config/srt-ktx-auto-booking/.env` 를 먼저 읽습니다.
필요하면 `--secrets-path`로 다른 env 파일을 지정할 수 있습니다.

### 특정 열차만 감시
```bash
python3 scripts/srt_autobook_watcher.py \
  --state-dir ./state/srt-train369 \
  --dep 수서 \
  --arr 대전 \
  --date 20260508 \
  --start-time 200000 \
  --end-time 205959 \
  --target-train-number 369 \
  --mode continuous-single \
  --poll-seconds 20 \
  --seat-preference general-first \
  --notify stdout
```

### Telegram 알림
```bash
python3 scripts/srt_autobook_watcher.py \
  --state-dir ./state/srt-suseo-daejeon-20260508 \
  --dep 수서 \
  --arr 대전 \
  --date 20260508 \
  --start-time 200000 \
  --end-time 205959 \
  --mode target-total \
  --poll-seconds 20 \
  --seat-preference general-first \
  --notify telegram \
  --telegram-chat-id <CHAT_ID>
```

### OpenClaw Telegram 알림
```bash
python3 scripts/srt_autobook_watcher.py \
  --state-dir ./state/srt-suseo-daejeon-20260508 \
  --dep 수서 \
  --arr 대전 \
  --date 20260508 \
  --start-time 200000 \
  --end-time 205959 \
  --mode target-total \
  --poll-seconds 20 \
  --seat-preference general-first \
  --notify openclaw \
  --openclaw-target telegram:<CHAT_ID>
```

예시:
```bash
bash examples/srt_watch_example.sh
```

### 중지
```bash
touch ./state/srt-suseo-daejeon-20260508/stop.flag
```

### 상태 파일
watcher 는 `state.json` 을 갱신합니다.
주요 필드:
- `status`
- `last_checked_at`
- `matching_reservations`
- `reserved_seat_total`
- `last_snapshot`
- `last_error`
- `last_notification_at`

## 주의사항
- 결제 자동화는 포함하지 않습니다.
- 실시간 예약 성공을 보장하지 않습니다.
- `.env`, `state/`, 로그 파일은 저장소에 포함하지 마세요.
- KTX 예약은 항상 최신 `search` 결과의 `train_id` 를 사용하세요.
- 같은 조건의 SRT watcher 를 여러 개 동시에 실행하지 마세요.
- macOS 기본 Python(특히 LibreSSL 기반)에서는 최신 `urllib3` 경고가 날 수 있어 `requirements.txt` 에 호환 버전을 고정했습니다.
