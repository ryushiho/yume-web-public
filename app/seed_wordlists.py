# app/seed_wordlists.py

from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import func

from app.database import SessionLocal
from app import models
from app.routers.api_wordlists import ALLOWED_LISTS, _build_txt, _sha256


def _words_for(db, list_name: str) -> List[str]:
    return [
        w.word
        for w in db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == list_name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    ]


def seed_wordlist_snapshots() -> None:
    """Phase 6: 기존 운영 데이터가 이미 있을 때 최초 스냅샷을 한 번 생성.

    - 이미 스냅샷이 존재하면 아무것도 하지 않는다.
    - 각 리스트별로 "bootstrap" 액션으로 version=1을 만든다.
    """

    db = SessionLocal()
    try:
        for name in ALLOWED_LISTS.keys():
            has_snapshot = (
                db.query(func.max(models.BlueWarWordListSnapshot.version))
                .filter(models.BlueWarWordListSnapshot.list_name == name)
                .scalar()
            )
            if has_snapshot:
                continue

            words = _words_for(db, name)
            if not words:
                continue

            txt = _build_txt(words)
            snap = models.BlueWarWordListSnapshot(
                list_name=name,
                version=1,
                sha256=_sha256(txt),
                count=len(words),
                content_text=txt,
                action="bootstrap",
                note="initial snapshot",
                created_by="system",
                created_by_name=None,
                created_at=datetime.utcnow(),
            )
            db.add(snap)

        db.commit()
    finally:
        db.close()
