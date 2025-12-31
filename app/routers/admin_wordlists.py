# app/routers/admin_wordlists.py

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from urllib.parse import urlencode, quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
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

from app.utils.time import fmt_kst
from app.utils.wordlists import WORDLIST_LABELS
from app.utils import dictpacks
from app.utils.multipart_guard import HAS_MULTIPART
from app.config import settings


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
        "updated_at": fmt_kst(last_updated, "%Y-%m-%d %H:%M:%S") if last_updated else None,
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

    # 월별(버전) 단어 DB 팩 상태
    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    packs_default = (settings.WORDLIST_PACKS_DEFAULT or "").strip()
    packs_max_keep = int(getattr(settings, "WORDLIST_PACKS_MAX_KEEP", 3) or 3)

    pack_versions = dictpacks.list_versions(packs_dir)
    pack_default = dictpacks.get_default_version(env_override=packs_dir, env_default=packs_default)
    pack_manifest = dictpacks.build_manifest(env_override=packs_dir, env_default=packs_default, max_keep=packs_max_keep)


    # 월별(버전) 팩 업로드 '고정 탭' (예: 10월/12월/1월)
    tabs_raw = (getattr(settings, "WORDLIST_PACKS_TABS", "") or "").strip()
    if tabs_raw:
        _tab_versions = [v.strip() for v in tabs_raw.split(",") if v.strip()]
    else:
        # 기본값: 운영에서 가장 자주 쓰는 3개(예: 2025-10 / 2025-12 / 2026-01)
        _tab_versions = ["2025-10", "2025-12", "2026-01"]

    _versions_index = {
        str(it.get("version")): it
        for it in (pack_manifest.get("versions") or [])
        if isinstance(it, dict) and it.get("version") is not None
    }

    packs_tabs = []
    for v in _tab_versions:
        vv = str(v or "").strip()
        if not dictpacks.is_valid_version(vv):
            continue
        try:
            mm = int(vv.split("-")[1])
            label = f"{mm}월"
        except Exception:
            label = vv
        files = (_versions_index.get(vv) or {}).get("files") or {}
        packs_tabs.append({"version": vv, "label": label, "files": files, "is_default": (vv == pack_default)})


    return templates.TemplateResponse(
        "admin_wordlists.html",
        {
            "request": request,
            "metas": metas,
            "list_labels": WORDLIST_LABELS,
            "ok": ok,
            "error": error,
            "which": which,

            # packs
            "packs_manifest": pack_manifest,
            "packs_tabs": packs_tabs,
            "packs_versions": pack_versions,
            "packs_default": pack_default,
            "packs_max_keep": packs_max_keep,
        },
    )


def _decode_upload_txt(raw: bytes) -> str:
    if raw is None or len(raw) == 0:
        raise ValueError("empty")
    if len(raw) > 10 * 1024 * 1024:
        # 안전 상한(10MB)
        raise ValueError("toolarge")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp949")
        except Exception:
            raise ValueError("encoding")


if HAS_MULTIPART:
    @router.post("/packs/upload")
    async def admin_packs_upload(
        request: Request,
        dict_version: str = Form(...),
        blue_archive_words: UploadFile = File(...),
        suggestion: UploadFile = File(...),
        public_words: UploadFile = File(...),
        _admin: Dict[str, Any] = Depends(get_current_admin_user),
    ):
        """관리자 전용: 월별(버전) 단어 DB 팩 업로드 (multipart/form-data)."""
        v = (dict_version or "").strip()
        if not dictpacks.is_valid_version(v):
            return RedirectResponse(url="/admin/wordlists/?error=invalid_pack&which=" + quote(v), status_code=303)

        packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
        packs_default = (settings.WORDLIST_PACKS_DEFAULT or "").strip()
        packs_max_keep = int(getattr(settings, "WORDLIST_PACKS_MAX_KEEP", 3) or 3)

        try:
            raw1 = await blue_archive_words.read()
            raw2 = await suggestion.read()
            raw3 = await public_words.read()
            text1 = _decode_upload_txt(raw1)
            text2 = _decode_upload_txt(raw2)
            text3 = _decode_upload_txt(raw3)

            # 정규화(중복 제거 + 마지막 개행 보장)
            words1 = _parse_txt(text1)
            words2 = _parse_txt(text2)
            words3 = _parse_txt(text3)
            norm1 = _build_txt(words1)
            norm2 = _build_txt(words2)
            norm3 = _build_txt(words3)

            dictpacks.write_pack_files(
                v,
                blue_archive_words_txt=norm1,
                suggestion_txt=norm2,
                public_words_txt=norm3,
                env_override=packs_dir,
            )

            dictpacks.prune_versions(env_override=packs_dir, max_keep=packs_max_keep)

            # default_version.txt가 없고 env로 강제하지 않는다면, 기본값을 최신으로 고정
            if not packs_default:
                df = dictpacks.default_file(packs_dir)
                if not df.exists():
                    latest = dictpacks.get_default_version(env_override=packs_dir, env_default="")
                    if latest:
                        dictpacks.set_default_version(latest, env_override=packs_dir)
        except ValueError as e:
            code = str(e)
            return RedirectResponse(url=f"/admin/wordlists/?error={code}&which=pack", status_code=303)
        except Exception:
            return RedirectResponse(url="/admin/wordlists/?error=pack_upload&which=pack", status_code=303)

        return RedirectResponse(url=f"/admin/wordlists/?ok=1&which=pack:{quote(v)}", status_code=303)
else:
    @router.post("/packs/upload")
    async def admin_packs_upload(
        _request: Request,
        _admin: Dict[str, Any] = Depends(get_current_admin_user),
    ):
        """multipart 미설치 환경: 업로드 엔드포인트를 비활성화한다(서비스 기동은 유지)."""
        return RedirectResponse(url="/admin/wordlists/?error=multipart_missing&which=pack", status_code=303)


@router.post("/packs/delete/{dict_version}")
async def admin_packs_delete(
    dict_version: str,
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    v = (dict_version or "").strip()
    if not dictpacks.is_valid_version(v):
        return RedirectResponse(url="/admin/wordlists/?error=invalid_pack&which=" + quote(v), status_code=303)

    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    packs_default = (settings.WORDLIST_PACKS_DEFAULT or "").strip()

    try:
        dictpacks.delete_version(v, env_override=packs_dir)

        # default_version.txt가 없고 env로 강제하지 않는다면, 최신으로 재고정
        if not packs_default:
            df = dictpacks.default_file(packs_dir)
            if not df.exists():
                latest = dictpacks.get_default_version(env_override=packs_dir, env_default="")
                if latest:
                    dictpacks.set_default_version(latest, env_override=packs_dir)
    except Exception:
        return RedirectResponse(url="/admin/wordlists/?error=pack_delete&which=" + quote(v), status_code=303)

    return RedirectResponse(url=f"/admin/wordlists/?ok=1&which=pack_deleted:{quote(v)}", status_code=303)


@router.post("/packs/set_default/{dict_version}")
async def admin_packs_set_default(
    dict_version: str,
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    v = (dict_version or "").strip()
    if not dictpacks.is_valid_version(v):
        return RedirectResponse(url="/admin/wordlists/?error=invalid_pack&which=" + quote(v), status_code=303)

    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    try:
        dictpacks.set_default_version(v, env_override=packs_dir)
    except Exception:
        return RedirectResponse(url="/admin/wordlists/?error=pack_default&which=" + quote(v), status_code=303)

    return RedirectResponse(url=f"/admin/wordlists/?ok=1&which=pack_default:{quote(v)}", status_code=303)


def _pack_list_name(list_name: str) -> str:
    """월별(버전) 팩에서 관리할 3개 키만 허용."""
    try:
        return _assert_list_name(list_name)
    except HTTPException:
        raise


@router.get("/packs/{dict_version}/{list_name}")
def admin_pack_file_page(
    request: Request,
    dict_version: str,
    list_name: str,
    _admin: Dict[str, Any] = Depends(get_current_admin_user),
):
    """월별(버전) 단어 DB 팩의 개별 파일 관리(간단 뷰/다운로드/업로드)."""
    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()

    v = (dict_version or "").strip()
    if not dictpacks.is_valid_version(v):
        return RedirectResponse(url="/admin/wordlists/?error=invalid_pack&which=" + quote(v), status_code=303)

    name = _pack_list_name(list_name)
    fn = ALLOWED_LISTS[name]

    meta = None
    text_preview = ""
    try:
        p = dictpacks.pack_file_path(v, fn, env_override=packs_dir)
        if p.exists() and p.is_file():
            m = dictpacks.file_meta(p)
            meta = {
                "filename": fn,
                "size": m.size,
                "sha256": m.sha256,
                "updated_at": m.updated_at,
            }
            # 프리뷰는 최대 400줄만
            raw = _decode_upload_txt(p.read_bytes())
            lines = raw.splitlines()
            text_preview = "\n".join(lines[:400])
    except Exception:
        meta = None
        text_preview = ""

    ok = request.query_params.get("ok")
    error = request.query_params.get("error")

    return templates.TemplateResponse(
        "admin_pack_file.html",
        {
            "request": request,
            "dict_version": v,
            "list_name": name,
            "list_label": WORDLIST_LABELS.get(name, fn),
            "filename": fn,
            "meta": meta,
            "preview": text_preview,
            "ok": ok,
            "error": error,
        },
    )


if HAS_MULTIPART:
    @router.post("/packs/upload_one")
    async def admin_packs_upload_one(
        request: Request,
        dict_version: str = Form(...),
        list_name: str = Form(...),
        file: UploadFile = File(...),
        _admin: Dict[str, Any] = Depends(get_current_admin_user),
    ):
        """월별(버전) 팩에 파일 1개만 업로드(덮어쓰기).

        UI에서 10월/12월/1월 탭을 눌러 3개 파일을 각각 업로드할 수 있도록 한다.
        """
        v = (dict_version or "").strip()
        if not dictpacks.is_valid_version(v):
            return RedirectResponse(url="/admin/wordlists/?error=invalid_pack&which=" + quote(v), status_code=303)

        try:
            name = _pack_list_name(list_name)
        except HTTPException:
            return RedirectResponse(url="/admin/wordlists/?error=unknown&which=" + quote(list_name), status_code=303)

        packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
        packs_default = (settings.WORDLIST_PACKS_DEFAULT or "").strip()
        packs_max_keep = int(getattr(settings, "WORDLIST_PACKS_MAX_KEEP", 3) or 3)

        try:
            raw = await file.read()
            text = _decode_upload_txt(raw)
            words = _parse_txt(text)
            norm = _build_txt(words)

            fn = ALLOWED_LISTS[name]
            p = dictpacks.pack_file_path(v, fn, env_override=packs_dir)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(norm, encoding="utf-8")
            tmp.replace(p)

            dictpacks.prune_versions(env_override=packs_dir, max_keep=packs_max_keep)

            # default_version.txt가 없고 env로 강제하지 않는다면, 기본값을 최신으로 고정
            if not packs_default:
                df = dictpacks.default_file(packs_dir)
                if not df.exists():
                    latest = dictpacks.get_default_version(env_override=packs_dir, env_default="")
                    if latest:
                        dictpacks.set_default_version(latest, env_override=packs_dir)
        except ValueError as e:
            code = str(e)
            return RedirectResponse(
                url=f"/admin/wordlists/packs/{quote(v)}/{quote(name)}?error={quote(code)}",
                status_code=303,
            )
        except Exception:
            return RedirectResponse(
                url=f"/admin/wordlists/packs/{quote(v)}/{quote(name)}?error=pack_upload",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/admin/wordlists/?ok=1&which=pack_one:{quote(v)}:{quote(name)}&packtab={quote(v)}",
            status_code=303,
        )
else:
    @router.post("/packs/upload_one")
    async def admin_packs_upload_one(
        _request: Request,
        _admin: Dict[str, Any] = Depends(get_current_admin_user),
    ):
        return RedirectResponse(url="/admin/wordlists/?error=multipart_missing&which=pack", status_code=303)


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
            "list_labels": WORDLIST_LABELS,
            "list_label": WORDLIST_LABELS.get(name, ALLOWED_LISTS.get(name, name)),
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


if HAS_MULTIPART:
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
else:
    @router.post("/upload/{list_name}")
    async def admin_wordlists_upload(
        list_name: str,
        _admin: Dict[str, Any] = Depends(get_current_admin_user),
    ):
        return RedirectResponse(url=f"/admin/wordlists/?error=multipart_missing&which={quote(list_name)}", status_code=303)


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
