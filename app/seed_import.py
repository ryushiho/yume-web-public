# app/seed_import.py
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict

from sqlalchemy.orm import Session

from app import models


SEED_PATH = Path(__file__).parent / "seed" / "blue_records.json"
META_KEY = "blue_records_seed_sha256"


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def import_blue_records_base_stats(db: Session, payload: Dict[str, Any]) -> None:
    """blue_records.json의 users.wins/losses 를 User.base_wins/base_losses 로 덮어쓴다."""
    users = payload.get("users", {}) or {}
    for discord_id, info in users.items():
        if not isinstance(info, dict):
            continue
        wins = int(info.get("wins", 0) or 0)
        losses = int(info.get("losses", 0) or 0)
        name = (info.get("name") or "").strip() or None

        u = db.query(models.User).filter(models.User.discord_id == str(discord_id)).first()
        if not u:
            u = models.User(discord_id=str(discord_id))
            db.add(u)

        u.base_wins = wins
        u.base_losses = losses
        if name and (not u.nickname):
            u.nickname = name


def ensure_blue_records_seed(db: Session) -> bool:
    """시드 파일이 변경되었을 때만 1회 적용. 적용했으면 True."""
    if not SEED_PATH.exists():
        return False

    raw = SEED_PATH.read_bytes()
    sha = _sha256_bytes(raw)

    meta = db.query(models.AppMeta).filter(models.AppMeta.key == META_KEY).first()
    if meta and (meta.value == sha):
        return False

    payload = json.loads(raw.decode("utf-8"))
    import_blue_records_base_stats(db, payload)

    if not meta:
        meta = models.AppMeta(key=META_KEY, value=sha)
        db.add(meta)
    else:
        meta.value = sha

    db.commit()
    return True
