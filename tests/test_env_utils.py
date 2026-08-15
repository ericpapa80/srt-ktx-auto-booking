from __future__ import annotations

from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from env_utils import parse_env_file


def test_parse_env_file_ignores_placeholders_and_keeps_real_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KSKILL_SRT_ID=your_srt_id_here\n"
        "KSKILL_SRT_PASSWORD=real-password\n"
        "OPENCLAW_NOTIFY_TARGET=\n",
        encoding="utf-8",
    )

    assert parse_env_file(env_file) == {"KSKILL_SRT_PASSWORD": "real-password"}
