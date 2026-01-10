from __future__ import annotations

"""Upload-backed word sources for BlueWar analysis (Phase 7).

Admin can upload a .txt and run analysis *only* on that uploaded file.
This is intentionally isolated from monthly packs and DB-backed lists.

Storage
  - <project_root>/data/bluewar_uploads/<upload_id>/{<listfile>.txt, meta.json}

Notes
  - `data/` is safe-excluded by the deploy pipeline, so uploads persist across deploys.
  - This module never touches the existing dictpacks directory.
"""

import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from app.utils import dictpacks


LIST_FILES: Dict[str, str] = {
    "suggestion": "suggestion.txt",
    "blue_archive_words": "blue_archive_words.txt",
    "public_words": "public_words.txt",
}


def uploads_dir() -> Path:
    return dictpacks.project_root() / "data" / "bluewar_uploads"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))


def _read_text_robust(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def new_upload_id() -> str:
    # Timestamp + random suffix (sortable-ish, collision resistant enough)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    rnd = secrets.token_hex(3)
    return f"u{ts}_{rnd}"


@dataclass(frozen=True)
class UploadInfo:
    upload_id: str
    list_name: str
    filename: str
    original_filename: str
    size_bytes: int
    sha256: str
    word_count: int
    created_at: str


def save_upload(*, list_name: str, original_filename: str, raw: bytes) -> UploadInfo:
    ln = (list_name or "").strip() or "blue_archive_words"
    if ln not in LIST_FILES:
        ln = "blue_archive_words"

    upload_id = new_upload_id()
    fn = LIST_FILES[ln]

    base = uploads_dir() / upload_id
    base.mkdir(parents=True, exist_ok=True)

    path = base / fn
    _atomic_write(path, raw)

    # word_count is best-effort; we store it for UI convenience
    try:
        text = _read_text_robust(path)
        from app.routers.api_wordlists import _parse_txt as parse_wordlist_txt  # local import

        words = parse_wordlist_txt(text)
        word_count = len(words)
    except Exception:
        word_count = 0

    info = UploadInfo(
        upload_id=upload_id,
        list_name=ln,
        filename=fn,
        original_filename=(original_filename or "").strip() or fn,
        size_bytes=int(len(raw)),
        sha256=_sha256_bytes(raw),
        word_count=int(word_count),
        created_at=datetime.utcnow().isoformat(timespec="seconds"),
    )

    meta = {
        "upload_id": info.upload_id,
        "list_name": info.list_name,
        "filename": info.filename,
        "original_filename": info.original_filename,
        "size_bytes": info.size_bytes,
        "sha256": info.sha256,
        "word_count": info.word_count,
        "created_at": info.created_at,
    }
    _atomic_write(base / "meta.json", json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    return info


def get_upload_file(*, upload_id: str, list_name: str) -> Optional[Path]:
    uid = (upload_id or "").strip()
    ln = (list_name or "").strip() or "blue_archive_words"
    if not uid:
        return None
    if ln not in LIST_FILES:
        ln = "blue_archive_words"

    p = uploads_dir() / uid / LIST_FILES[ln]
    if p.exists() and p.is_file():
        return p
    return None


def load_upload_words(*, upload_id: str, list_name: str) -> Optional[List[str]]:
    p = get_upload_file(upload_id=upload_id, list_name=list_name)
    if not p:
        return None

    try:
        text = _read_text_robust(p)
        from app.routers.api_wordlists import _parse_txt as parse_wordlist_txt  # local import

        return parse_wordlist_txt(text)
    except Exception:
        return None


def list_uploads(limit: int = 30) -> List[UploadInfo]:
    base = uploads_dir()
    if not base.exists() or not base.is_dir():
        return []

    out: List[UploadInfo] = []
    for d in sorted(base.iterdir(), key=lambda p: p.name, reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists() or not meta_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            out.append(
                UploadInfo(
                    upload_id=str(meta.get("upload_id") or d.name),
                    list_name=str(meta.get("list_name") or "blue_archive_words"),
                    filename=str(meta.get("filename") or ""),
                    original_filename=str(meta.get("original_filename") or ""),
                    size_bytes=int(meta.get("size_bytes") or 0),
                    sha256=str(meta.get("sha256") or ""),
                    word_count=int(meta.get("word_count") or 0),
                    created_at=str(meta.get("created_at") or ""),
                )
            )
        except Exception:
            continue

        if len(out) >= int(limit):
            break

    return out
