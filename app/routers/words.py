# app/routers/words.py

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import models
from app.dependencies import get_db
from app.routers.api_wordlists import ALLOWED_LISTS, _assert_list_name, _current_version
from app.utils.wordlists import WORDLIST_LABELS


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


def _build_pager(page: int, total_pages: int, window: int = 2) -> List[Optional[int]]:
    """페이지 링크에 표시할 번호 목록을 만든다.
    None은 '…' (생략) 표시용.
    """
    if total_pages <= 1:
        return [1]

    items: List[Optional[int]] = []
    for p in range(1, total_pages + 1):
        show = (p == 1) or (p == total_pages) or (abs(p - page) <= window)
        if show:
            items.append(p)
        else:
            if not items or items[-1] is not None:
                items.append(None)
    return items


@router.get("/", response_class=HTMLResponse)
def words_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """공개 단어 보기/검색 페이지.

    - 누구나 접근 가능
    - 보기/검색만 제공 (추가/수정/삭제는 관리자 페이지에서만)
    """

    # 공개 단어 보기(/words)는 "public_words"만 노출한다.
    list_name = "public_words" if "public_words" in ALLOWED_LISTS else "suggestion"
    q_raw = (request.query_params.get("q") or "").strip()

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        name = "suggestion"

    # pagination
    page = _safe_int(request.query_params.get("page"), 1)
    page_size = _safe_int(request.query_params.get("page_size"), 200)
    if page < 1:
        page = 1
    if page_size < 20:
        page_size = 20
    if page_size > 1000:
        page_size = 1000

    # sort
    sort = (request.query_params.get("sort") or "alpha").strip().lower()
    sort_options: List[Tuple[str, str]] = [
        ("alpha", "가나다순"),
        ("alpha_desc", "가나다역순"),
        ("len_desc", "긴 단어"),
        ("len", "짧은 단어"),
        ("recent", "최신 업데이트"),
    ]
    allowed_sorts = {k for k, _ in sort_options}
    if sort not in allowed_sorts:
        sort = "alpha"

    # meta
    metas = {name: _meta_for_public(db, name)}
    meta = metas.get(name)

    base = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)

    # search (공백 정규화 1회)
    q = q_raw
    if q:
        q_nospace = "".join(q.split())
        if q_nospace and q_nospace != q:
            base = base.filter(
                or_(
                    models.BlueWarWord.word.contains(q),
                    func.replace(models.BlueWarWord.word, " ", "").contains(q_nospace),
                )
            )
        else:
            base = base.filter(models.BlueWarWord.word.contains(q))

    total = int(base.count())
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    # ordering
    if sort == "alpha":
        order_by = (models.BlueWarWord.word.asc(), models.BlueWarWord.id.asc())
    elif sort == "alpha_desc":
        order_by = (models.BlueWarWord.word.desc(), models.BlueWarWord.id.desc())
    elif sort == "len":
        order_by = (func.length(models.BlueWarWord.word).asc(), models.BlueWarWord.word.asc(), models.BlueWarWord.id.asc())
    elif sort == "len_desc":
        order_by = (func.length(models.BlueWarWord.word).desc(), models.BlueWarWord.word.asc(), models.BlueWarWord.id.asc())
    else:  # recent
        order_by = (models.BlueWarWord.updated_at.desc(), models.BlueWarWord.id.desc())

    offset = (page - 1) * page_size
    rows = (
        base.order_by(*order_by)
        .offset(int(offset))
        .limit(int(page_size))
        .all()
    )

    # pagination query string (page만 바꿔 끼우기)
    qs_base = "q={q}&page_size={ps}&sort={s}".format(
        q=quote_plus(q),
        ps=page_size,
        s=quote_plus(sort),
    )
    page_items = _build_pager(page, total_pages)

    return templates.TemplateResponse(
        "words.html",
        {
            "request": request,
            "allowed_lists": [list_name],
            "list_labels": WORDLIST_LABELS,
            "list_name": name,
            "list_label": WORDLIST_LABELS.get(name, ALLOWED_LISTS.get(name, name)),
            "q": q,
            "sort": sort,
            "sort_options": sort_options,
            "page": page,
            "page_size": page_size,
            "page_size_options": [50, 100, 200, 500, 1000],
            "total": total,
            "total_pages": total_pages,
            "page_items": page_items,
            "qs_base": qs_base,
            "offset": offset,
            "rows": rows,
            "metas": metas,
            "meta": meta,
        },
    )
