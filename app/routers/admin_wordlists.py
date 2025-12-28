# app/routers/admin_wordlists.py

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from urllib.parse import urlencode, quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.dependencies import get_db, get_current_admin_user
from app.routers.api_wordlists import (
    ALLOWED_LISTS,
    _assert_list_name,
    _build_txt,
    _parse_txt,
    _sha256,
    _upsert_wordlist_overwrite,
    _clean_word,
    _create_snapshot,
    _current_version,
)


router = APIRouter(prefix="/admin/wordlists", tags=["admin-wordlists"])
templates = Jinja2Templates(directory="app/templates")


def _meta_for(db: Session, list_name: str) -> Dict[str, Optional[str]]:
    q = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == list_name)
    count = q.count()
    last_updated: Optional[datetime] = (
        db.query(func.max(models.BlueWarWord.updated_at))
        .filter(models.BlueWarWord.list_name == list_name)
        .scalar()
    )
    words = [w.word for w in q.order_by(models.BlueWarWord.id.asc()).all()]
    txt = _build_txt(words)
    version = _current_version(db, list_name)
    return {
        "list_name": list_name,
        "filename": ALLOWED_LISTS[list_name],
        "count": str(count),
        "updated_at": last_updated.isoformat() if last_updated else None,
        "version": str(version),
        "sha256": _sha256(txt),
    }


def _safe_int(v: Optional[str], default: int) -> int:
    try:
        if v is None:
            return default
        return int(str(v).strip())
    except Exception:
        return default


def _clean_word_form(word: Optional[str]) -> str:
    """관리자 폼 입력용 단어 검증/정규화."""
    w = (word or "").strip()
    if not w:
        raise ValueError("empty")
    if len(w) > 200:
        raise ValueError("toolong")
    if any(ch.isspace() for ch in w):
        raise ValueError("whitespace")
    return w


def _safe_next_url(next_url: str) -> str:
    """open redirect 방지: 내부 경로만 허용."""
    u = (next_url or "").strip()
    if not u:
        return ""
    if u.startswith("/admin/wordlists"):
        return u
    return ""


@router.get("/")
def admin_wordlists_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    # 상태 메시지
    ok = request.query_params.get("ok")
    error = request.query_params.get("error")
    which = request.query_params.get("which")

    metas = {name: _meta_for(db, name) for name in ALLOWED_LISTS.keys()}
    return templates.TemplateResponse(
        "admin_wordlists.html",
        {
            "request": request,
            "metas": metas,
            "ok": ok,
            "error": error,
            "which": which,
        },
    )


@router.get("/{list_name}")
def admin_wordlist_detail(
    request: Request,
    list_name: str,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Phase 3: 검색/페이지네이션 포함 단어 리스트 보기."""
    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    # 상태 메시지
    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    q = (request.query_params.get("q") or "").strip()
    page = _safe_int(request.query_params.get("page"), 1)
    page_size = _safe_int(request.query_params.get("page_size"), 200)
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 200
    if page_size > 1000:
        page_size = 1000

    meta = _meta_for(db, name)

    # Phase 6: 최근 버전(스냅샷) 기록
    versions = (
        db.query(models.BlueWarWordListSnapshot)
        .filter(models.BlueWarWordListSnapshot.list_name == name)
        .order_by(models.BlueWarWordListSnapshot.version.desc())
        .limit(20)
        .all()
    )

    base = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
    if q:
        base = base.filter(models.BlueWarWord.word.contains(q))

    total = int(base.count())
    # 총 페이지 수(최소 1)
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

    # 현재 목록 URL(액션 후 복귀용)
    current_url = f"/admin/wordlists/{name}?" + urlencode({"q": q, "page": page, "page_size": page_size})

    return templates.TemplateResponse(
        "admin_wordlist_detail.html",
        {
            "request": request,
            "list_name": name,
            "meta": meta,
            "versions": versions,
            "q": q,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "rows": rows,
            "offset": offset,
            "current_url": current_url,
            "ok": ok,
            "error": error,
        },
    )


@router.post("/upload/{list_name}")
async def admin_wordlists_upload(
    request: Request,
    list_name: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """관리자 페이지 폼 업로드 처리(전체 덮어쓰기).

    - overview(/admin/wordlists/)에서 호출되면 overview로 돌아감
    - detail(/admin/wordlists/{list_name})에서 호출되면 next=... 쿼리로 돌아감
    """
    next_url = (request.query_params.get("next") or "").strip()
    # open redirect 방지: 내부 경로만 허용
    if next_url and not next_url.startswith("/admin/wordlists"):
        next_url = ""

    def _redir(url: str) -> RedirectResponse:
        return RedirectResponse(url=url, status_code=303)

    def _redir_error(err: str, which_name: str) -> RedirectResponse:
        if next_url:
            sep = "&" if "?" in next_url else "?"
            return _redir(url=f"{next_url}{sep}error={err}")
        return _redir(url=f"/admin/wordlists/?error={err}&which={which_name}")

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return _redir(url="/admin/wordlists/?error=unknown&which=" + str(list_name))

    raw = await file.read()
    if raw is None or len(raw) == 0:
        return _redir_error("empty", name)
    if len(raw) > 5 * 1024 * 1024:
        return _redir_error("toolarge", name)

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp949")
        except Exception:
            return _redir_error("encoding", name)

    words = _parse_txt(text)
    try:
        _upsert_wordlist_overwrite(db, name, words)
        _create_snapshot(db, name, action="upload", actor=_admin, note=f"filename:{file.filename}")
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redir_error("dup", name)

    if next_url:
        sep = "&" if "?" in next_url else "?"
        return _redir(url=f"{next_url}{sep}ok=1")
    return _redir(url=f"/admin/wordlists/?ok=1&which={name}")


@router.post("/{list_name}/add")
async def admin_word_add(
    request: Request,
    list_name: str,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """관리자 UI: 단어 1개 추가."""
    form = await request.form()
    next_url = _safe_next_url(str(form.get("next") or ""))
    if not next_url:
        next_url = f"/admin/wordlists/{list_name}"

    def _redir(err: str = "", ok: bool = False) -> RedirectResponse:
        sep = "&" if "?" in next_url else "?"
        if ok:
            return RedirectResponse(url=f"{next_url}{sep}ok=1", status_code=303)
        return RedirectResponse(url=f"{next_url}{sep}error={err}", status_code=303)

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    try:
        w = _clean_word_form(str(form.get("word") or ""))
    except ValueError as e:
        return _redir(err=str(e))

    row = models.BlueWarWord(list_name=name, word=w)
    db.add(row)
    try:
        _create_snapshot(db, name, action="add", actor=_admin, note=f"add:{w}")
        db.commit()
    except IntegrityError:
        db.rollback()
        return _redir(err="dup")
    return _redir(ok=True)


@router.post("/{list_name}/delete")
async def admin_word_delete(
    request: Request,
    list_name: str,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """관리자 UI: 단일/선택 삭제.

    - single_id가 오면 그 1개만 삭제
    - ids[]가 오면 선택 삭제
    """
    form = await request.form()
    next_url = _safe_next_url(str(form.get("next") or ""))
    if not next_url:
        next_url = f"/admin/wordlists/{list_name}"

    def _redir(err: str = "", ok: bool = False) -> RedirectResponse:
        sep = "&" if "?" in next_url else "?"
        if ok:
            return RedirectResponse(url=f"{next_url}{sep}ok=1", status_code=303)
        return RedirectResponse(url=f"{next_url}{sep}error={err}", status_code=303)

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    single_id = (form.get("single_id") or "").strip()
    ids = form.getlist("ids") if hasattr(form, "getlist") else []

    try:
        if single_id:
            target_ids = [int(single_id)]
        else:
            target_ids = [int(x) for x in ids if str(x).strip().isdigit()]
    except Exception:
        return _redir(err="invalid")

    if not target_ids:
        return _redir(err="empty")
    if len(target_ids) > 5000:
        return _redir(err="toolarge")

    deleted = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id.in_(target_ids))
        .delete(synchronize_session=False)
    )
    if int(deleted) <= 0:
        db.rollback()
        return _redir(err="notfound")

    action = "delete" if len(target_ids) == 1 else "bulk_delete"
    _create_snapshot(db, name, action=action, actor=_admin, note=f"count:{len(target_ids)}")
    db.commit()
    return _redir(ok=True)


@router.get("/{list_name}/edit/{word_id}")
def admin_word_edit_page(
    request: Request,
    list_name: str,
    word_id: int,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """관리자 UI: 단어 수정 페이지."""
    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    next_url = _safe_next_url(str(request.query_params.get("next") or ""))
    if not next_url:
        next_url = f"/admin/wordlists/{name}"

    row = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id == int(word_id))
        .first()
    )
    if not row:
        sep = "&" if "?" in next_url else "?"
        return RedirectResponse(url=f"{next_url}{sep}error=notfound", status_code=303)

    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "admin_wordlist_edit.html",
        {
            "request": request,
            "list_name": name,
            "word_id": int(word_id),
            "word": row.word,
            "next_url": next_url,
            "error": error,
        },
    )


@router.post("/{list_name}/edit/{word_id}")
async def admin_word_edit_submit(
    request: Request,
    list_name: str,
    word_id: int,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """관리자 UI: 단어 수정 처리."""
    form = await request.form()
    next_url = _safe_next_url(str(form.get("next") or ""))
    if not next_url:
        next_url = f"/admin/wordlists/{list_name}"

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    row = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id == int(word_id))
        .first()
    )
    if not row:
        sep = "&" if "?" in next_url else "?"
        return RedirectResponse(url=f"{next_url}{sep}error=notfound", status_code=303)

    try:
        w = _clean_word_form(str(form.get("word") or ""))
    except ValueError as e:
        return RedirectResponse(
            url=f"/admin/wordlists/{name}/edit/{int(word_id)}?next={quote(next_url, safe='')}&error={str(e)}",
            status_code=303,
        )

    row.word = w
    try:
        _create_snapshot(db, name, action="edit", actor=_admin, note=f"id:{int(word_id)}")
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            url=f"/admin/wordlists/{name}/edit/{int(word_id)}?next={quote(next_url, safe='')}&error=dup",
            status_code=303,
        )

    sep = "&" if "?" in next_url else "?"
    return RedirectResponse(url=f"{next_url}{sep}ok=1", status_code=303)


@router.post("/{list_name}/rollback/{version}")
async def admin_wordlist_rollback(
    request: Request,
    list_name: str,
    version: int,
    db: Session = Depends(get_db),
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """Phase 6: 관리자 UI 롤백.

    - 특정 버전의 스냅샷 content_text를 현재 리스트에 덮어쓴다.
    - 그리고 rollback 액션으로 새로운 버전을 하나 더 만든다.
    """
    form = await request.form()
    next_url = _safe_next_url(str(form.get("next") or request.query_params.get("next") or ""))
    if not next_url:
        next_url = f"/admin/wordlists/{list_name}"

    def _redir(err: str = "", ok: bool = False) -> RedirectResponse:
        sep = "&" if "?" in next_url else "?"
        if ok:
            return RedirectResponse(url=f"{next_url}{sep}ok=1", status_code=303)
        return RedirectResponse(url=f"{next_url}{sep}error={err}", status_code=303)

    try:
        name = _assert_list_name(list_name)
    except HTTPException:
        return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + str(list_name), status_code=303)

    v = int(version)
    if v <= 0:
        return _redir(err="invalid")

    snap = (
        db.query(models.BlueWarWordListSnapshot)
        .filter(models.BlueWarWordListSnapshot.list_name == name)
        .filter(models.BlueWarWordListSnapshot.version == v)
        .first()
    )
    if not snap:
        return _redir(err="notfound")

    words = _parse_txt(snap.content_text or "")
    try:
        _upsert_wordlist_overwrite(db, name, words)
        _create_snapshot(db, name, action="rollback", actor=_admin, note=f"rollback to v{v}")
        db.commit()
    except Exception:
        db.rollback()
        return _redir(err="unknown")

    return _redir(ok=True)
