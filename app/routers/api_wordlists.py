# app/routers/api_wordlists.py

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status, Body, Header, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user_api
from app import models
from app.config import settings
from app.utils import dictpacks
from app.utils.multipart_guard import HAS_MULTIPART


MAX_PAGE_SIZE = 1000


def _read_text_robust(path) -> str:
    """UTF-8 우선, 실패하면 CP949로 읽는다."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949")


def _require_wordlist_token(
    request: Request,
    x_yume_wordlist_token: str | None = Header(default=None, alias="X-Yume-Wordlist-Token"),
    token: str | None = Query(default=None),
) -> None:
    """원본 TXT(.txt / download) 접근 보호.

    공개 단어 보기(/words)는 누구나 가능하지만,
    원본 TXT는 '다운로드'가 너무 쉬워서 외부 공개를 막는다.

    - 헤더: X-Yume-Wordlist-Token
    - 또는 쿼리: ?token=
    """
    # 1) 관리자(세션)면 허용
    try:
        if request.session.get("user"):
            return
        member = request.session.get("member")
        if member and bool(member.get("is_admin")):
            return
    except Exception:
        # 세션 미들웨어가 없거나, request.session이 없더라도 토큰 검증으로 계속 진행
        pass

    # 2) 토큰 검증
    expected = (settings.WORDLIST_TOKEN or "").strip()
    provided = ((x_yume_wordlist_token or "").strip() or (token or "").strip())

    # 운영에서 토큰이 비어있으면 보호 의미가 없다. -> 안전하게 막는다.
    if not expected:
        raise HTTPException(status_code=404, detail="not found")

    if not provided or provided != expected:
        # 존재 자체를 숨기기 위해 403이 아니라 404로 처리
        raise HTTPException(status_code=404, detail="not found")


router = APIRouter(
    prefix="/api/bluewar/wordlists",
    tags=["api-bluewar-wordlists"],
)


# Phase 1 기준: 웹에서 관리할 리스트.
# - suggestion / blue_archive_words: 봇에서 사용하는 핵심 리스트
# - public_words: 공개 "단어 보기"(/words) 전용 리스트 (관리자 TXT 업로드 가능)
ALLOWED_LISTS = {
    "suggestion": "suggestion.txt",
    "blue_archive_words": "blue_archive_words.txt",
    "public_words": "public_words.txt",
}


# 월별(버전) 단어 DB 팩이 활성화되어 있으면,
# suggestion / blue_archive_words 의 .txt 응답은 "default_version" 파일을 우선 제공한다.
PACK_BACKED_LISTS = {"suggestion", "blue_archive_words", "public_words"}


def _packs_env() -> tuple[str, str, int]:
    """(packs_dir, packs_default, max_keep)"""
    return (
        (settings.WORDLIST_PACKS_DIR or "").strip(),
        (settings.WORDLIST_PACKS_DEFAULT or "").strip(),
        int(getattr(settings, "WORDLIST_PACKS_MAX_KEEP", 3) or 3),
    )


def _packs_default_version() -> str | None:
    packs_dir, packs_default, _max_keep = _packs_env()
    return dictpacks.get_default_version(env_override=packs_dir, env_default=packs_default)


def _packs_has_file(list_name: str) -> bool:
    if list_name not in PACK_BACKED_LISTS:
        return False
    packs_dir, packs_default, _max_keep = _packs_env()
    dv = dictpacks.get_default_version(env_override=packs_dir, env_default=packs_default)
    if not dv:
        return False
    fn = ALLOWED_LISTS[list_name]
    try:
        p = dictpacks.pack_file_path(dv, fn, env_override=packs_dir)
        return p.exists() and p.is_file()
    except Exception:
        return False


def _packs_read_default_txt(list_name: str) -> tuple[str, str] | None:
    """Return (dict_version, content_txt) for the default pack file if available."""
    if list_name not in PACK_BACKED_LISTS:
        return None
    packs_dir, packs_default, _max_keep = _packs_env()
    dv = dictpacks.get_default_version(env_override=packs_dir, env_default=packs_default)
    if not dv:
        return None
    fn = ALLOWED_LISTS[list_name]
    try:
        p = dictpacks.pack_file_path(dv, fn, env_override=packs_dir)
        if not p.exists() or not p.is_file():
            return None
        return (dv, _read_text_robust(p))
    except Exception:
        return None


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


def _parse_txt(text: str) -> List[str]:
    """txt(1줄=1단어) -> 단어 배열.

    - 공백/탭/개행 제거
    - 빈 줄 제거
    - 중복 제거(원본 순서 유지)
    """
    seen = set()
    out: List[str] = []
    for line in text.splitlines():
        w = (line or "").strip()
        if not w:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _clean_word(word: Optional[str]) -> str:
    """단어 입력값 검증/정규화.

    - 앞뒤 공백 제거
    - 빈 문자열 금지
    - 길이 제한(모델 컬럼 길이와 맞춤)
    - 내부 공백/탭 금지(끝말잇기 단어 호환)
    """
    w = (word or "").strip()
    if not w:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty word")
    if len(w) > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="word too long")
    if any(ch.isspace() for ch in w):
        # strip 이후에도 남은 공백/탭/개행은 허용하지 않는다.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="whitespace not allowed")
    return w


def _upsert_wordlist_overwrite(db: Session, list_name: str, words: List[str]) -> None:
    """해당 리스트를 완전히 교체한다(덮어쓰기).

    NOTE:
      - Phase 6(버전/롤백)부터는 "스냅샷"과 같은 트랜잭션에 묶기 위해 여기서 commit하지 않는다.
      - 호출자가 db.commit()을 수행해야 한다.
    """
    db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == list_name).delete()
    now = datetime.utcnow()
    rows = [models.BlueWarWord(list_name=list_name, word=w, created_at=now, updated_at=now) for w in words]
    if rows:
        db.bulk_save_objects(rows)


def _actor_fields(actor: Optional[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    """스냅샷/감사로그에 남길 '누가' 정보를 문자열로 정규화."""
    if not actor:
        return ("system", None)

    nickname = actor.get("nickname")
    # member login
    if actor.get("member_id") is not None:
        return (f"member:{actor.get('member_id')}", nickname)
    # legacy admin login (/auth/login)
    if actor.get("id") is not None:
        return (f"admin:{actor.get('id')}", nickname)

    return ("unknown", nickname)


def _current_version(db: Session, list_name: str) -> int:
    v = (
        db.query(func.max(models.BlueWarWordListSnapshot.version))
        .filter(models.BlueWarWordListSnapshot.list_name == list_name)
        .scalar()
    )
    return int(v or 0)


def _create_snapshot(
    db: Session,
    list_name: str,
    action: str,
    actor: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
) -> models.BlueWarWordListSnapshot:
    """현재 DB 상태를 기준으로 스냅샷을 1개 추가한다.

    - 호출 시점의 "현재" 단어 목록을 그대로 저장한다.
    - commit은 호출자가 수행한다.
    """
    words = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == list_name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    )
    txt = _build_txt([w.word for w in words])
    sha = _sha256(txt)
    ver = _current_version(db, list_name) + 1
    created_by, created_by_name = _actor_fields(actor)

    snap = models.BlueWarWordListSnapshot(
        list_name=list_name,
        version=int(ver),
        sha256=sha,
        count=int(len(words)),
        content_text=txt,
        action=(action or "unknown")[:30],
        created_by=created_by,
        created_by_name=created_by_name,
        note=note,
    )
    db.add(snap)
    return snap


@router.post("/{list_name}/words")
def word_add(
    list_name: str,
    data: Dict[str, str] = Body(...),
    db: Session = Depends(get_db),
    actor: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 단어 1개 추가."""
    name = _assert_list_name(list_name)
    w = _clean_word((data or {}).get("word", ""))
    row = models.BlueWarWord(list_name=name, word=w)
    db.add(row)
    try:
        _create_snapshot(db, name, action="add", actor=actor, note=f"add:{w}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="duplicate")
    db.refresh(row)
    return {"id": int(row.id), "list_name": name, "word": row.word}


@router.patch("/{list_name}/words/{word_id}")
def word_update(
    list_name: str,
    word_id: int,
    data: Dict[str, str] = Body(...),
    db: Session = Depends(get_db),
    actor: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 단어 1개 수정."""
    name = _assert_list_name(list_name)
    row = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id == int(word_id))
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    w = _clean_word((data or {}).get("word", ""))
    row.word = w
    try:
        _create_snapshot(db, name, action="edit", actor=actor, note=f"id:{int(word_id)}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="duplicate")
    db.refresh(row)
    return {"id": int(row.id), "list_name": name, "word": row.word}


@router.delete("/{list_name}/words/{word_id}")
def word_delete(
    list_name: str,
    word_id: int,
    db: Session = Depends(get_db),
    actor: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 단어 1개 삭제."""
    name = _assert_list_name(list_name)
    row = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id == int(word_id))
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(row)
    _create_snapshot(db, name, action="delete", actor=actor, note=f"id:{int(word_id)}")
    db.commit()
    return {"deleted": 1, "id": int(word_id), "list_name": name}


@router.post("/{list_name}/words/bulk_delete")
def word_bulk_delete(
    list_name: str,
    data: Dict[str, List[int]] = Body(...),
    db: Session = Depends(get_db),
    actor: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 여러 단어 삭제.

    body 예)
      {"ids": [1,2,3]}
    """
    name = _assert_list_name(list_name)
    ids = (data or {}).get("ids") or []
    ids = [int(x) for x in ids if str(x).strip().isdigit()]
    # 안전장치: 한 번에 너무 많이 지우는 실수 방지
    if len(ids) > 5000:
        raise HTTPException(status_code=400, detail="too many ids")
    if not ids:
        return {"deleted": 0, "list_name": name}

    deleted = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .filter(models.BlueWarWord.id.in_(ids))
        .delete(synchronize_session=False)
    )
    _create_snapshot(db, name, action="bulk_delete", actor=actor, note=f"count:{len(ids)}")
    db.commit()
    return {"deleted": int(deleted), "list_name": name}


@router.get("/meta")
def wordlists_meta(db: Session = Depends(get_db)) -> Dict[str, Dict[str, Optional[str]]]:
    """봇/클라이언트가 캐시 갱신 판단에 쓰는 메타."""
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for name in ALLOWED_LISTS.keys():
        # ✅ 월별(버전) 단어 DB 팩이 존재하면, suggestion/blue_archive_words는
        #    DB가 아니라 "default_version" 파일 메타를 반환한다.
        if name in PACK_BACKED_LISTS:
            packs_dir, packs_default, _max_keep = _packs_env()
            dv = dictpacks.get_default_version(env_override=packs_dir, env_default=packs_default)
            if dv:
                try:
                    p = dictpacks.pack_file_path(dv, ALLOWED_LISTS[name], env_override=packs_dir)
                    if p.exists() and p.is_file():
                        txt = _read_text_robust(p)
                        words = _parse_txt(txt)
                        last_updated = datetime.fromtimestamp(p.stat().st_mtime)
                        out[name] = {
                            "filename": ALLOWED_LISTS[name],
                            "count": str(len(words)),
                            "updated_at": last_updated.isoformat(),
                            "version": f"dict:{dv}",
                            "dict_version": dv,
                            "sha256": _sha256(_build_txt(words)),
                        }
                        continue
                except Exception:
                    # 파일 기반 메타가 실패하면 DB 기반 메타로 폴백
                    pass

        q = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
        count = q.count()
        last_updated: Optional[datetime] = (
            db.query(func.max(models.BlueWarWord.updated_at))
            .filter(models.BlueWarWord.list_name == name)
            .scalar()
        )

        words = [w.word for w in q.order_by(models.BlueWarWord.id.asc()).all()]
        txt = _build_txt(words)
        version = _current_version(db, name)
        out[name] = {
            "filename": ALLOWED_LISTS[name],
            "count": str(count),
            "updated_at": last_updated.isoformat() if last_updated else None,
            "version": str(version),
            "sha256": _sha256(txt),
        }
    return out


@router.get("/manifest.json")
def dictpacks_manifest(
    _: None = Depends(_require_wordlist_token),
) -> Dict[str, object]:
    """월별(버전) 단어 DB 팩 목록/메타.

    - shiho(봇) autosync에서 이걸 읽어서 최신 3개를 맞춘다.
    - 토큰이 없으면 404로 숨긴다.
    """
    packs_dir, packs_default, max_keep = _packs_env()
    return dictpacks.build_manifest(
        env_override=packs_dir,
        env_default=packs_default,
        max_keep=max_keep,
    )


@router.get("/{dict_version}/{filename}", response_class=PlainTextResponse)
def dictpacks_file_txt(
    dict_version: str,
    filename: str,
    _: None = Depends(_require_wordlist_token),
) -> PlainTextResponse:
    """특정 버전(YYYY-MM)의 원본 TXT를 제공한다.

    예)
      - /api/bluewar/wordlists/2025-12/blue_archive_words.txt
      - /api/bluewar/wordlists/2025-12/suggestion.txt
      - /api/bluewar/wordlists/2025-12/public_words.txt
    """
    packs_dir, _packs_default, _max_keep = _packs_env()
    v = (dict_version or "").strip()
    fn = (filename or "").strip()
    if not dictpacks.is_valid_version(v):
        raise HTTPException(status_code=404, detail="not found")
    if fn not in dictpacks.PACK_FILES:
        raise HTTPException(status_code=404, detail="not found")
    try:
        p = dictpacks.pack_file_path(v, fn, env_override=packs_dir)
        if not p.exists() or not p.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return PlainTextResponse(content=_read_text_robust(p))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="not found")


@router.get("/{list_name}.txt", response_class=PlainTextResponse)
def wordlist_txt(
    list_name: str,
    db: Session = Depends(get_db),
    _: None = Depends(_require_wordlist_token),
) -> PlainTextResponse:
    """단어 리스트를 txt 형태로 제공한다.

    예)
      - /api/bluewar/wordlists/suggestion.txt
      - /api/bluewar/wordlists/blue_archive_words.txt
    """
    name = _assert_list_name(list_name)

    # ✅ 월별(버전) 단어 DB 팩이 있으면 default 버전 파일을 우선 제공
    pack = _packs_read_default_txt(name)
    if pack is not None and name in PACK_BACKED_LISTS:
        _dv, txt = pack
        return PlainTextResponse(content=txt)

    words = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    )
    txt = _build_txt([w.word for w in words])

    # content-type은 PlainTextResponse가 알아서 text/plain
    return PlainTextResponse(content=txt)


@router.get("/{list_name}/download")
def wordlist_download(
    list_name: str,
    db: Session = Depends(get_db),
    _: None = Depends(_require_wordlist_token),
) -> Response:
    """브라우저에서 바로 다운로드되도록 Content-Disposition을 포함한다."""
    name = _assert_list_name(list_name)

    # ✅ 월별(버전) 단어 DB 팩이 있으면 default 버전 파일을 우선 제공
    pack = _packs_read_default_txt(name)
    if pack is not None and name in PACK_BACKED_LISTS:
        _dv, txt = pack
        filename = ALLOWED_LISTS[name]
        return Response(
            content=txt,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    words = (
        db.query(models.BlueWarWord)
        .filter(models.BlueWarWord.list_name == name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    )
    txt = _build_txt([w.word for w in words])
    filename = ALLOWED_LISTS[name]
    return Response(
        content=txt,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{list_name}/words")
def wordlist_words(
    list_name: str,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
) -> Dict[str, object]:
    """단어 리스트를 페이지 단위(JSON)로 제공한다.

    Phase 3(관리자 UI 검색/페이지네이션)에서 사용.

    NOTE:
      - 공개 "단어 보기"(/words) 페이지에서도 이 쿼리를 직접 사용하므로,
        이 엔드포인트 자체는 공개(읽기)로 유지한다.
      - 원본 TXT 다운로드는 토큰으로 보호한다.
    """
    name = _assert_list_name(list_name)
    query = (q or "").strip()

    base = db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == name)
    if query:
        # SQLite에서 ILIKE가 애매할 수 있어 contains로 간단히 처리한다(한글 단어 위주).
        base = base.filter(models.BlueWarWord.word.contains(query))

    total = int(base.count())
    offset = int((page - 1) * page_size)
    rows = (
        base.order_by(models.BlueWarWord.id.asc())
        .offset(offset)
        .limit(int(page_size))
        .all()
    )
    items = [{"id": int(w.id), "word": w.word} for w in rows]

    return {
        "list_name": name,
        "q": query,
        "total": total,
        "page": int(page),
        "page_size": int(page_size),
        "items": items,
    }


if HAS_MULTIPART:
    @router.post("/{list_name}/upload")
    async def wordlist_upload(
        list_name: str,
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        actor: dict = Depends(get_current_admin_user_api),
    ) -> Dict[str, str]:
        """관리자 전용: txt 업로드로 전체 덮어쓰기.

        - multipart/form-data
        - file: UploadFile (.txt)
        """
        name = _assert_list_name(list_name)

        raw = await file.read()
        if raw is None or len(raw) == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file")
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file too large")

        # 원칙: UTF-8 권장. 다만 업로드 UX를 위해 CP949 한 번 더 시도한다.
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("cp949")
            except Exception:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported encoding (use UTF-8)")

        words = _parse_txt(text)
        _upsert_wordlist_overwrite(db, name, words)

        # 업로드 변경을 버전으로 남긴다.
        _create_snapshot(db, name, action="upload", actor=actor, note=f"filename:{file.filename}")
        db.commit()

        txt = _build_txt(words)
        return {
            "list_name": name,
            "filename": ALLOWED_LISTS[name],
            "count": str(len(words)),
            "version": str(_current_version(db, name)),
            "sha256": _sha256(txt),
        }
else:
    @router.post("/{list_name}/upload")
    async def wordlist_upload(
        list_name: str,
        payload: Dict[str, str] = Body(...),
        db: Session = Depends(get_db),
        actor: dict = Depends(get_current_admin_user_api),
    ) -> Dict[str, str]:
        """관리자 전용: txt 업로드(멀티파트 미지원 환경).

        Body(JSON): {"text": "...", "filename": "optional.txt"}
        """
        name = _assert_list_name(list_name)

        text = (payload.get("text") or "")
        if not text.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty text")
        if len(text.encode("utf-8")) > 5 * 1024 * 1024:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="text too large")

        words = _parse_txt(text)
        _upsert_wordlist_overwrite(db, name, words)

        fn = (payload.get("filename") or "").strip() or "(json)"
        _create_snapshot(db, name, action="upload", actor=actor, note=f"filename:{fn}")
        db.commit()

        txt = _build_txt(words)
        return {
            "list_name": name,
            "filename": ALLOWED_LISTS[name],
            "count": str(len(words)),
            "version": str(_current_version(db, name)),
            "sha256": _sha256(txt),
        }


@router.get("/{list_name}/versions")
def wordlist_versions(
    list_name: str,
    limit: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_admin_user_api),
) -> Dict[str, object]:
    """관리자 전용: 버전(스냅샷) 목록."""
    name = _assert_list_name(list_name)

    rows = (
        db.query(models.BlueWarWordListSnapshot)
        .filter(models.BlueWarWordListSnapshot.list_name == name)
        .order_by(models.BlueWarWordListSnapshot.version.desc())
        .limit(int(limit))
        .all()
    )

    items = []
    for r in rows:
        items.append(
            {
                "id": int(r.id),
                "version": int(r.version),
                "action": r.action,
                "count": int(r.count),
                "sha256": r.sha256,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "created_by": r.created_by,
                "created_by_name": r.created_by_name,
                "note": r.note,
            }
        )

    return {"list_name": name, "items": items}


@router.post("/{list_name}/rollback/{version}")
def wordlist_rollback(
    list_name: str,
    version: int,
    db: Session = Depends(get_db),
    actor: dict = Depends(get_current_admin_user_api),
) -> Dict[str, str]:
    """관리자 전용: 특정 버전으로 롤백.

    - 요청이 오면 해당 버전의 content_text를 현재 word 테이블에 덮어쓴다.
    - 그리고 "rollback" 액션으로 새로운 버전을 하나 더 만든다(감사로그/복구용).
    """
    name = _assert_list_name(list_name)
    v = int(version)
    if v <= 0:
        raise HTTPException(status_code=400, detail="invalid version")

    snap = (
        db.query(models.BlueWarWordListSnapshot)
        .filter(models.BlueWarWordListSnapshot.list_name == name)
        .filter(models.BlueWarWordListSnapshot.version == v)
        .first()
    )
    if not snap:
        raise HTTPException(status_code=404, detail="version not found")

    words = _parse_txt(snap.content_text or "")
    _upsert_wordlist_overwrite(db, name, words)
    _create_snapshot(db, name, action="rollback", actor=actor, note=f"rollback to v{v}")
    db.commit()

    txt = _build_txt(words)
    return {
        "list_name": name,
        "filename": ALLOWED_LISTS[name],
        "count": str(len(words)),
        "version": str(_current_version(db, name)),
        "sha256": _sha256(txt),
    }
