"""migrate_blue_records.py

blue_records.json -> yume_admin.db(User.base_wins/base_losses) 마이그레이션.

이 스크립트는 "전적을 SQLite로 옮기기"가 목적이 아니라,
기존 JSON에 쌓여 있던 누적 승/패를 "기본 전적(base)"으로 User 테이블에 반영하기 위한 도구다.

사용 예시(서버):

    cd /opt/yume-web
    source venv/bin/activate
    # 기본 경로: /opt/yume/data/storage/blue_records.json
    python migrate_blue_records.py

환경변수:
    BLUE_RECORDS_JSON_PATH: JSON 파일 경로(기본값: /opt/yume/data/storage/blue_records.json)

주의:
    - JSON 구조가 프로젝트마다 달라질 수 있어서 여러 키를 폭넓게 허용한다.
    - 어떤 키가 실제로 사용되는지는 실행 로그를 보고 확인하면 된다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.database import Base, SessionLocal, engine
from app.models import User


DEFAULT_PATH = "/opt/yume/data/storage/blue_records.json"


def _as_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip() != "":
            return int(float(v))
        return None
    except Exception:
        return None


def _pick_wl(record: Dict[str, Any]) -> Tuple[int, int]:
    """record에서 wins/losses를 최대한 유연하게 뽑는다."""

    # 가장 우선시 하는 키들
    win_candidates = [
        "base_wins",
        "wins",
        "win",
        "pvp_wins",
        "pvp_win",
        "w",
    ]
    loss_candidates = [
        "base_losses",
        "losses",
        "loss",
        "pvp_losses",
        "pvp_loss",
        "l",
    ]

    wins = None
    losses = None
    for k in win_candidates:
        if k in record:
            wins = _as_int(record.get(k))
            if wins is not None:
                break

    for k in loss_candidates:
        if k in record:
            losses = _as_int(record.get(k))
            if losses is not None:
                break

    return int(wins or 0), int(losses or 0)


def _pick_nickname(record: Dict[str, Any]) -> Optional[str]:
    for k in ["nickname", "name", "display_name", "username"]:
        v = record.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def migrate() -> None:
    Base.metadata.create_all(bind=engine)

    json_path = Path(os.getenv("BLUE_RECORDS_JSON_PATH", DEFAULT_PATH))
    if not json_path.exists():
        print(f"[!] blue_records.json not found: {json_path}")
        return

    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        print("[!] JSON root must be a dict: { <discord_id>: <record>, ... }")
        return

    db = SessionLocal()
    updated = 0
    created = 0

    try:
        for discord_id, record in data.items():
            if not isinstance(discord_id, str) or not discord_id.strip():
                continue
            if not isinstance(record, dict):
                # 구조가 다를 수는 있지만, 여기서는 dict만 지원한다.
                continue

            wins, losses = _pick_wl(record)
            nickname = _pick_nickname(record)

            u = db.query(User).filter(User.discord_id == discord_id).first()
            if u is None:
                u = User(
                    discord_id=discord_id,
                    nickname=nickname,
                    note=None,
                    base_wins=wins,
                    base_losses=losses,
                )
                db.add(u)
                created += 1
            else:
                # 기존 유저가 있으면 base 값을 덮어쓴다(=JSON이 source of truth)
                u.base_wins = wins
                u.base_losses = losses
                if (not u.nickname) and nickname:
                    u.nickname = nickname
                updated += 1

        db.commit()

        print(f"[*] Done. created={created} updated={updated}")
        print(f"    JSON: {json_path}")
    except Exception as e:
        db.rollback()
        print(f"[!] Failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate()
