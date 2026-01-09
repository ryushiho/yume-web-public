from __future__ import annotations

"""BlueWar analysis persistent cache (Phase 2).

Why a filesystem cache when we already store results in DB?

1) Deploy pipeline safe-excludes `data/`, so cache survives code deploy.
2) After a server restart or DB reset, we can restore analysis quickly.
3) We can optionally cache the *explanation graph* to avoid rebuilding
   large adjacency structures repeatedly.

This module intentionally stores only pure-Python data structures.
"""

import gzip
import os
import pickle
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.utils import dictpacks


def cache_dir() -> Path:
    """Return cache directory (created on demand)."""

    override = (getattr(settings, "BLUEWAR_ANALYSIS_CACHE_DIR", "") or "").strip()
    if override:
        p = Path(override).expanduser()
        if not p.is_absolute():
            p = dictpacks.project_root() / p
        return p
    return dictpacks.project_root() / "data" / "bluewar_analysis_cache"


def cache_file_path(analysis_key: str) -> Path:
    return cache_dir() / f"{analysis_key}.pkl.gz"


def graph_cache_file_path(analysis_key: str) -> Path:
    return cache_dir() / f"{analysis_key}.graph.pkl.gz"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))


def save_payload(analysis_key: str, payload: Dict[str, Any]) -> None:
    """Save analysis result payload."""
    p = cache_file_path(analysis_key)
    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    buf = gzip.compress(raw, compresslevel=6)
    _atomic_write(p, buf)


def load_payload(analysis_key: str) -> Optional[Dict[str, Any]]:
    p = cache_file_path(analysis_key)
    if not p.exists() or not p.is_file():
        return None
    try:
        raw = gzip.decompress(p.read_bytes())
        obj = pickle.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def save_graph(analysis_key: str, payload: Dict[str, Any]) -> None:
    """Save explanation graph payload."""
    p = graph_cache_file_path(analysis_key)
    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    buf = gzip.compress(raw, compresslevel=6)
    _atomic_write(p, buf)


def load_graph(analysis_key: str) -> Optional[Dict[str, Any]]:
    p = graph_cache_file_path(analysis_key)
    if not p.exists() or not p.is_file():
        return None
    try:
        raw = gzip.decompress(p.read_bytes())
        obj = pickle.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None
