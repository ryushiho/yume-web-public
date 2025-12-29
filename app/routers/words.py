# app/routers/words.py

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.dependencies import get_db
from app.routers.api_wordlists import ALLOWED_LISTS, _assert_list_name, _current_version


router = APIRouter(prefix="/words", tags=["words"])
templates = Jinja2Templates(directory="app/templates")


def _safe_int(v: Optional[str], default: int) -> int:
    try:
        if v is None:
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _meta_for_public(db: Session, list_name: str) -> Dict[str, Optional[str]]:
    q = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == list_name)
    count = int(q.count())
    last_updated: Optional[datetime] = (
        db.query(func.max(models.BlueWarWord.updated_at))
        .filter(models.BlueWarWord.list_name == list_name)
        .scalar()
    )
    version = int(_current_version(db, list_name))
    return {
        "list_name": list_name,
        "filename": ALLOWED_LISTS[list_name],
        "count": str(count),
        "updated_at": last_updated.isoformat() if last_updated else None,
        "version": str(version),
    }


@router.get("/", response_class=HTMLResponse)
def words_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """공개 단어 보기/검색 페이지.

    - 누구나 접근 가능
    - 보기/검색만 제공 (추가/수정/삭제는 관리자 페이지에서만)
    """

    # 기본은 suggestion이지만, 공개용 리스트(public_words)가 있고 단어가 존재하면 그쪽을 기본으로 쓴다.
    list_param = request.query_params.get("list")
    if list_param is None or not str(list_param).strip():
        default_list = "suggestion"
        if "public_words" in ALLOWED_LISTS:
            try:
                has_any = (
                    db.query(models.BlueWarWord.id)
                    .filter(models.BlueWarWord.list_name == "public_words")
                    .first()
                    is not None
                )
            except Exception:
                has_any = False
            if has_any:
                default_list = "public_words"
        list_name = default_list
    else:
        list_name = str(list_param).strip()
    q = (request.query_params.get("q") or "").strip()

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        name = "suggestion"

    page = _safe_int(request.query_params.get("page"), 1)
    page_size = _safe_int(request.query_params.get("page_size"), 200)

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 200
    if page_size > 1000:
        page_size = 1000

    metas = {n: _meta_for_public(db, n) for n in ALLOWED_LISTS.keys()}
    meta = metas.get(name) or _meta_for_public(db, name)

    base = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
    if q:
        base = base.filter(models.BlueWarWord.word.contains(q))

    total = int(base.count())
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size
    rows = (
        base.order_by(models.BlueWarWord.id.asc())
        .offset(int(offset))
        .limit(int(page_size))
        .all()
    )

    return templates.TemplateResponse(
        "words.html",
        {
            "request": request,
            "allowed_lists": list(ALLOWED_LISTS.keys()),
            "list_name": name,
            "q": q,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "offset": offset,
            "rows": rows,
            "metas": metas,
            "meta": meta,
        },
    )
