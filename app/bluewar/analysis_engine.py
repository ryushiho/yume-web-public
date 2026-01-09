from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app import models
from app.utils import dictpacks
from app.config import settings
from .dooum import allowed_first_chars, dooum_signature


ALGO_VERSION = "syllable-retrograde-v1"


class NodeType:
    WIN = "WIN"
    LOSE = "LOSE"
    DRAW = "DRAW"


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _normalize_word(w: str) -> str:
    return (w or "").strip()


def _first_char(w: str) -> str:
    return w[0] if w else ""


def _last_char(w: str) -> str:
    return w[-1] if w else ""


def _build_txt(words: Iterable[str]) -> str:
    # 1줄 1단어 + 마지막 개행 보장
    out = "\n".join([_normalize_word(x) for x in words if _normalize_word(x)])
    return (out + "\n") if out and not out.endswith("\n") else out


def _load_words_from_pack(*, version: str, list_name: str) -> Optional[List[str]]:
    """Load words from monthly dict pack file.

    list_name uses internal names from app/routers/api_wordlists.py:
      - "blue_archive_words" -> blue_archive_words.txt
      - "suggestion" -> suggestion.txt
      - "public_words" -> public_words.txt
    """
    filename_map = {
        "blue_archive_words": "blue_archive_words.txt",
        "suggestion": "suggestion.txt",
        "public_words": "public_words.txt",
    }
    fn = filename_map.get(list_name)
    if not fn:
        return None

    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    p = dictpacks.pack_file_path(version, fn, env_override=packs_dir)
    if not p.exists():
        return None

    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        # fallback: cp949
        try:
            text = p.read_text(encoding="cp949")
        except Exception:
            return None

    words = []
    seen = set()
    for raw in text.splitlines():
        w = _normalize_word(raw)
        if not w:
            continue
        if any(ch.isspace() for ch in w):
            continue
        if w in seen:
            continue
        seen.add(w)
        words.append(w)
    return words


def _load_words_from_db(db: Session, list_name: str) -> List[str]:
    rows = (
        db.query(models.BlueWarWord.word)
        .filter(models.BlueWarWord.list_name == list_name)
        .order_by(models.BlueWarWord.id.asc())
        .all()
    )
    out: List[str] = []
    seen = set()
    for (w,) in rows:
        ww = _normalize_word(w)
        if not ww:
            continue
        if any(ch.isspace() for ch in ww):
            continue
        if ww in seen:
            continue
        seen.add(ww)
        out.append(ww)
    return out


@dataclass(frozen=True)
class AnalysisInput:
    list_name: str
    pack_version: Optional[str]
    words_sha256: str
    word_count: int
    dooum_sha256: str
    algo_version: str
    analysis_key: str


def prepare_input(
    db: Session,
    *,
    list_name: str = "blue_archive_words",
    pack_version: Optional[str] = None,
) -> Tuple[AnalysisInput, List[str]]:
    """Resolve the word source and produce a stable analysis key."""

    # Resolve pack version (if packs exist on filesystem)
    packs_dir = (settings.WORDLIST_PACKS_DIR or "").strip()
    packs_default = (settings.WORDLIST_PACKS_DEFAULT or "").strip()

    words: Optional[List[str]] = None
    resolved_pack: Optional[str] = None

    # Try dict packs first (preferred in 운영)
    if pack_version:
        resolved_pack = pack_version.strip()
    else:
        resolved_pack = dictpacks.get_default_version(env_override=packs_dir, env_default=packs_default)

    if resolved_pack:
        words = _load_words_from_pack(version=resolved_pack, list_name=list_name)

    # Fallback to DB
    if words is None:
        words = _load_words_from_db(db, list_name)
        resolved_pack = None

    txt = _build_txt(words)
    words_sha = _sha256_text(txt)
    dooum_sha = dooum_signature()

    analysis_key = hashlib.sha256(
        json.dumps(
            {
                "list_name": list_name,
                "pack_version": resolved_pack,
                "words_sha256": words_sha,
                "dooum_sha256": dooum_sha,
                "algo_version": ALGO_VERSION,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    return (
        AnalysisInput(
            list_name=list_name,
            pack_version=resolved_pack,
            words_sha256=words_sha,
            word_count=len(words),
            dooum_sha256=dooum_sha,
            algo_version=ALGO_VERSION,
            analysis_key=analysis_key,
        ),
        words,
    )


def _retrograde_syllables(
    words: List[str],
) -> Tuple[
    Dict[str, str],
    Dict[str, int],
    Dict[str, int],
    Dict[str, List[Tuple[str, str]]],
    Dict[str, List[str]],
]:
    """Compute syllable WIN/LOSE/DRAW via retrograde algorithm.

    Returns:
      status: syllable -> NodeType
      out_moves: syllable -> number of playable words (moves)
      in_moves: syllable -> number of incoming moves
      moves: syllable -> list of (word, next_syllable)
      preds: syllable -> list of predecessor syllables (one entry per move)
    """

    words_by_first: Dict[str, List[str]] = defaultdict(list)
    firsts: set[str] = set()
    lasts: set[str] = set()

    for w in words:
        if not w:
            continue
        f = _first_char(w)
        l = _last_char(w)
        if not f or not l:
            continue
        words_by_first[f].append(w)
        firsts.add(f)
        lasts.add(l)

    # Nodes: all syllables that can appear as start condition or as next condition.
    nodes: set[str] = set(firsts) | set(lasts)
    # Also include dooum source syllables that may appear as last syllable.
    # (If they never appear, they don't matter; but this keeps stats stable.)
    from .dooum import DOOUM_MAP
    nodes |= set(DOOUM_MAP.keys())

    moves: Dict[str, List[Tuple[str, str]]] = {n: [] for n in nodes}
    preds: Dict[str, List[str]] = {n: [] for n in nodes}
    out_moves: Dict[str, int] = {n: 0 for n in nodes}
    in_moves: Dict[str, int] = {n: 0 for n in nodes}

    # Build moves and predecessor lists (counting each word as one move)
    for u in list(nodes):
        allowed = allowed_first_chars(u)
        if not allowed:
            continue
        for ch in allowed:
            for w in words_by_first.get(ch, []):
                v = _last_char(w)
                if not v:
                    continue
                if v not in nodes:
                    # extremely rare; but be safe
                    nodes.add(v)
                    moves[v] = []
                    preds[v] = []
                    out_moves[v] = 0
                    in_moves[v] = 0
                moves[u].append((w, v))
                preds[v].append(u)
                out_moves[u] += 1
                in_moves[v] += 1

    # Retrograde
    status: Dict[str, str] = {n: "" for n in nodes}
    remaining = dict(out_moves)
    q: deque[str] = deque()

    for n in nodes:
        if remaining.get(n, 0) == 0:
            status[n] = NodeType.LOSE
            q.append(n)

    while q:
        v = q.popleft()
        t = status[v]

        if t == NodeType.LOSE:
            # predecessors become WIN
            for u in preds.get(v, []):
                if status.get(u):
                    continue
                status[u] = NodeType.WIN
                q.append(u)
        elif t == NodeType.WIN:
            # decrement predecessors; if no remaining moves, they become LOSE
            for u in preds.get(v, []):
                if status.get(u):
                    continue
                remaining[u] = max(0, int(remaining.get(u, 0)) - 1)
                if remaining[u] == 0:
                    status[u] = NodeType.LOSE
                    q.append(u)

    for n in nodes:
        if not status.get(n):
            status[n] = NodeType.DRAW

    return status, out_moves, in_moves, moves, preds


def rebuild_analysis(
    db: Session,
    *,
    list_name: str = "blue_archive_words",
    pack_version: Optional[str] = None,
    sample_words: int = 8,
) -> AnalysisInput:
    """Recompute and store analysis results.

    This is safe to call multiple times; it overwrites rows for the same analysis_key.
    """

    meta, words = prepare_input(db, list_name=list_name, pack_version=pack_version)

    status, out_moves, in_moves, moves, _preds = _retrograde_syllables(words)

    # Remove existing rows for this key
    db.query(models.BlueWarSyllableStat).filter(models.BlueWarSyllableStat.analysis_key == meta.analysis_key).delete()
    db.query(models.BlueWarWordStat).filter(models.BlueWarWordStat.analysis_key == meta.analysis_key).delete()
    db.query(models.BlueWarAnalysisMeta).filter(models.BlueWarAnalysisMeta.analysis_key == meta.analysis_key).delete()

    now = datetime.utcnow()

    # Insert meta
    db.add(
        models.BlueWarAnalysisMeta(
            analysis_key=meta.analysis_key,
            list_name=meta.list_name,
            pack_version=meta.pack_version,
            words_sha256=meta.words_sha256,
            word_count=meta.word_count,
            dooum_sha256=meta.dooum_sha256,
            algo_version=meta.algo_version,
            created_at=now,
        )
    )

    # Syllable stats
    syl_rows: List[models.BlueWarSyllableStat] = []
    for syl, t in sorted(status.items(), key=lambda kv: kv[0]):
        mv = moves.get(syl, [])
        win_ws = [w for (w, v) in mv if status.get(v) == NodeType.LOSE]
        lose_ws = [w for (w, v) in mv if status.get(v) == NodeType.WIN]
        draw_ws = [w for (w, v) in mv if status.get(v) == NodeType.DRAW]

        sample = win_ws[:sample_words]

        syl_rows.append(
            models.BlueWarSyllableStat(
                analysis_key=meta.analysis_key,
                syllable=syl,
                node_type=t,
                out_moves=int(out_moves.get(syl, 0)),
                in_moves=int(in_moves.get(syl, 0)),
                win_moves=int(len(win_ws)),
                lose_moves=int(len(lose_ws)),
                draw_moves=int(len(draw_ws)),
                sample_win_words=json.dumps(sample, ensure_ascii=False),
                updated_at=now,
            )
        )

    db.bulk_save_objects(syl_rows)

    # Word stats (type derived from end syllable)
    word_rows: List[models.BlueWarWordStat] = []
    for w in words:
        s = _first_char(w)
        e = _last_char(w)
        end_t = status.get(e, NodeType.DRAW)
        if end_t == NodeType.LOSE:
            wt = NodeType.WIN
        elif end_t == NodeType.WIN:
            wt = NodeType.LOSE
        else:
            wt = NodeType.DRAW
        word_rows.append(
            models.BlueWarWordStat(
                analysis_key=meta.analysis_key,
                word=w,
                start_syllable=s,
                end_syllable=e,
                node_type=wt,
                updated_at=now,
            )
        )

    db.bulk_save_objects(word_rows)
    db.commit()

    return meta


def get_latest_meta(
    db: Session,
    *,
    list_name: str,
    pack_version: Optional[str],
) -> Optional[models.BlueWarAnalysisMeta]:
    q = db.query(models.BlueWarAnalysisMeta).filter(models.BlueWarAnalysisMeta.list_name == list_name)
    if pack_version is None:
        q = q.filter(models.BlueWarAnalysisMeta.pack_version.is_(None))
    else:
        q = q.filter(models.BlueWarAnalysisMeta.pack_version == pack_version)
    return q.order_by(models.BlueWarAnalysisMeta.created_at.desc()).first()
