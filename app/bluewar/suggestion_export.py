from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Literal, Tuple

from sqlalchemy.orm import Session

from app import models
from .analysis_engine import NodeType


SuggestionFormat = Literal["plain", "grouped"]


def _norm_word(w: str) -> str:
    return (w or "").strip()


def _sort_words(words: Iterable[str]) -> List[str]:
    # Deterministic: short -> lexicographic (UTF-8 codepoint order)
    out = [_norm_word(w) for w in words if _norm_word(w)]
    out = sorted(set(out), key=lambda x: (len(x), x))
    return out


def build_suggestion_text(words: Iterable[str], fmt: SuggestionFormat = "grouped") -> str:
    """Build suggestion.txt content.

    - plain: one word per line
    - grouped: '<첫음절> : w1, w2, ...' per line
    """
    fmt = (fmt or "grouped").strip().lower()  # type: ignore[assignment]
    if fmt not in ("plain", "grouped"):
        fmt = "grouped"

    sorted_words = _sort_words(words)

    if fmt == "plain":
        return "\n".join(sorted_words) + ("\n" if sorted_words else "")

    groups: Dict[str, List[str]] = defaultdict(list)
    for w in sorted_words:
        s = w[0]
        groups[s].append(w)

    # Deterministic syllable order
    lines: List[str] = []
    for syl in sorted(groups.keys()):
        ws = groups[syl]
        lines.append(f"{syl} : {', '.join(ws)}")
    return "\n".join(lines) + ("\n" if lines else "")


def fetch_neutral_words(db: Session, analysis_key: str) -> List[str]:
    """Fetch DRAW(중립) words from DB for a given analysis_key."""
    rows = (
        db.query(models.BlueWarWordStat.word)
        .filter(models.BlueWarWordStat.analysis_key == analysis_key)
        .filter(models.BlueWarWordStat.node_type == NodeType.DRAW)
        .order_by(models.BlueWarWordStat.word.asc())
        .all()
    )
    return [r[0] for r in rows if r and r[0]]
