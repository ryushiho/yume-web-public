# app/routers/api_wordlists.py

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app import models


router = APIRouter(
    prefix="/api/bluewar/wordlists",
    tags=["api-bluewar-wordlists"],
)


# Phase 1 기준: 웹에서 관리할 리스트 2개(파일명 기준)
ALLOWED_LISTS = {
    "suggestion": "suggestion.txt",
    "blue_archive_words": "blue_archive_words.txt",
}


def _assert_list_name(list_name: str) -> str:
    v = (list_name or "").strip()
    if v not in ALLOWED_LISTS:
        raise HTTPException(status_code=404, detail="unknown wordlist")
    return v


def _build_txt(words: List[str]) -> str:
    # 파일 호환을 위해 마지막에 개행을 하나 붙여준다.
    if not words:
        return ""
    return "\n".join(words) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@router.get("/meta")
def wordlists_meta(db: Session = Depends(get_db)) -> Dict[str, Dict[str, Optional[str]]]:
    """봇/클라이언트가 캐시 갱신 판단에 쓰는 메타."""
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for name in ALLOWED_LISTS.keys():
        q = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
        count = q.count()
        last_updated: Optional[datetime] = (
            db.query(func.max(models.BlueWarWord.updated_at))
            .filter(models.BlueWarWord.list_name == name)
            .scalar()
        )

        words = [w.word for w in q.order_by(models.BlueWarWord.id.asc()).all()]
        txt = _build_txt(words)
        out[name] = {
            "filename": ALLOWED_LISTS[name],
            "count": str(count),
            "updated_at": last_updated.isoformat() if last_updated else None,
            "sha256": _sha256(txt),
        }
    return out


@router.get("/{list_name}.txt", response_class=PlainTextResponse)
def wordlist_txt(list_name: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    """단어 리스트를 txt 형태로 제공한다.

    예)
      - /api/bluewar/wordlists/suggestion.txt
      - /api/bluewar/wordlists/blue_archive_words.txt
    """
    name = _assert_list_name(list_name)
    words = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    )
    txt = _build_txt([w.word for w in words])

    # content-type은 PlainTextResponse가 알아서 text/plain
    return PlainTextResponse(content=txt)
