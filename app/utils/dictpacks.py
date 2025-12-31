"""Monthly/Versioned BlueWar dictionary packs.

This module introduces a filesystem-based "dictionary pack" layer.

Why filesystem?
- The bot needs to download raw TXT quickly.
- We want multiple packs (e.g., 2025-10 / 2025-12 / 2026-01) to co-exist.
- We keep at most N packs (default 3), pruning the oldest.

Directory layout (default):
  <project_root>/data/wordlists/bluewar/
    default_version.txt
    versions/
      2025-10/
        blue_archive_words.txt
        suggestion.txt
        public_words.txt
      2025-12/
        ...

The API layer can serve:
- /api/bluewar/wordlists/manifest.json
- /api/bluewar/wordlists/<YYYY-MM>/blue_archive_words.txt
- /api/bluewar/wordlists/<YYYY-MM>/suggestion.txt
- /api/bluewar/wordlists/<YYYY-MM>/public_words.txt

And legacy endpoints can serve the "default" version.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


VERSION_RE = re.compile(r"^\d{4}-\d{2}$")

# Game-required files per pack.
PACK_FILES = ("blue_archive_words.txt", "suggestion.txt", "public_words.txt")


@dataclass(frozen=True)
class PackFileMeta:
    filename: str
    size: int
    sha256: str
    updated_at: str


def project_root() -> Path:
    # .../app/utils/dictpacks.py -> parents: utils(0) app(1) project_root(2)
    return Path(__file__).resolve().parents[2]


def base_dir(env_override: str = "") -> Path:
    """Return base dir for wordlists.

    If env_override is a non-empty absolute path, it is used.
    Otherwise, we use <project_root>/data/wordlists/bluewar.
    """
    if env_override:
        p = Path(env_override).expanduser()
        # Allow relative overrides too, but keep them relative to project root.
        if not p.is_absolute():
            p = project_root() / p
        return p
    return project_root() / "data" / "wordlists" / "bluewar"


def versions_dir(env_override: str = "") -> Path:
    return base_dir(env_override) / "versions"


def default_file(env_override: str = "") -> Path:
    return base_dir(env_override) / "default_version.txt"


def is_valid_version(v: str) -> bool:
    return bool(VERSION_RE.match((v or "").strip()))


def list_versions(env_override: str = "") -> List[str]:
    d = versions_dir(env_override)
    if not d.exists() or not d.is_dir():
        return []
    out: List[str] = []
    for p in d.iterdir():
        if p.is_dir() and is_valid_version(p.name):
            out.append(p.name)
    out.sort()  # YYYY-MM lexicographic == chronological
    return out


def get_default_version(
    *,
    env_override: str = "",
    env_default: str = "",
    file_default: Optional[str] = None,
) -> Optional[str]:
    """Resolve default pack version.

    Priority:
    1) env_default if valid and exists
    2) default_version.txt if valid and exists
    3) latest available
    """
    versions = list_versions(env_override)
    if not versions:
        return None

    env_default = (env_default or "").strip()
    if env_default and is_valid_version(env_default) and env_default in versions:
        return env_default

    if file_default is None:
        try:
            file_default = default_file(env_override).read_text(encoding="utf-8").strip()
        except Exception:
            file_default = ""
    file_default = (file_default or "").strip()
    if file_default and is_valid_version(file_default) and file_default in versions:
        return file_default

    return versions[-1]


def set_default_version(version: str, *, env_override: str = "") -> None:
    v = (version or "").strip()
    if not is_valid_version(v):
        raise ValueError("invalid version")
    versions = list_versions(env_override)
    if v not in versions:
        raise FileNotFoundError("version not found")
    df = default_file(env_override)
    df.parent.mkdir(parents=True, exist_ok=True)
    df.write_text(v + "\n", encoding="utf-8")


def pack_path(version: str, *, env_override: str = "") -> Path:
    v = (version or "").strip()
    if not is_valid_version(v):
        raise ValueError("invalid version")
    return versions_dir(env_override) / v


def pack_file_path(version: str, filename: str, *, env_override: str = "") -> Path:
    fn = (filename or "").strip()
    if fn not in PACK_FILES:
        raise ValueError("invalid filename")
    return pack_path(version, env_override=env_override) / fn


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_meta(path: Path) -> PackFileMeta:
    raw = path.read_bytes()
    st = path.stat()
    # ISO string in UTC-like; we don't need strict tz semantics for cache.
    updated = datetime.fromtimestamp(st.st_mtime).isoformat()
    return PackFileMeta(
        filename=path.name,
        size=int(len(raw)),
        sha256=_sha256_bytes(raw),
        updated_at=updated,
    )


def build_manifest(*, env_override: str = "", env_default: str = "", max_keep: int = 3) -> Dict[str, object]:
    versions = list_versions(env_override)
    default_v = get_default_version(env_override=env_override, env_default=env_default)

    items = []
    for v in versions:
        files = {}
        for fn in PACK_FILES:
            p = pack_file_path(v, fn, env_override=env_override)
            if p.exists() and p.is_file():
                m = file_meta(p)
                files[fn] = {
                    "size": m.size,
                    "sha256": m.sha256,
                    "updated_at": m.updated_at,
                }
        items.append({"version": v, "files": files})

    return {
        "default_version": default_v,
        "max_keep": int(max_keep),
        "versions": items,
    }


def prune_versions(*, env_override: str = "", max_keep: int = 3) -> List[str]:
    """Keep only newest max_keep versions. Return deleted versions."""
    max_keep = int(max_keep or 0)
    if max_keep <= 0:
        return []
    versions = list_versions(env_override)
    if len(versions) <= max_keep:
        return []

    to_delete = versions[: max(0, len(versions) - max_keep)]
    deleted: List[str] = []
    for v in to_delete:
        p = pack_path(v, env_override=env_override)
        try:
            shutil.rmtree(p)
            deleted.append(v)
        except Exception:
            # Best-effort; do not crash admin flow.
            continue

    # If default was deleted, clear it; caller may set a new default.
    try:
        df = default_file(env_override)
        if df.exists():
            cur = df.read_text(encoding="utf-8").strip()
            if cur in deleted:
                df.unlink(missing_ok=True)
    except Exception:
        pass

    return deleted


def delete_version(version: str, *, env_override: str = "") -> None:
    p = pack_path(version, env_override=env_override)
    if not p.exists() or not p.is_dir():
        raise FileNotFoundError("version not found")
    shutil.rmtree(p)
    # If default points to it, clear.
    try:
        df = default_file(env_override)
        if df.exists() and df.read_text(encoding="utf-8").strip() == version:
            df.unlink(missing_ok=True)
    except Exception:
        pass


def write_pack_files(
    version: str,
    *,
    blue_archive_words_txt: str,
    suggestion_txt: str,
    public_words_txt: str,
    env_override: str = "",
) -> None:
    """Write normalized TXT files for the given version."""
    vdir = pack_path(version, env_override=env_override)
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "blue_archive_words.txt").write_text(blue_archive_words_txt, encoding="utf-8")
    (vdir / "suggestion.txt").write_text(suggestion_txt, encoding="utf-8")
    (vdir / "public_words.txt").write_text(public_words_txt, encoding="utf-8")
