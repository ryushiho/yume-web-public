from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, Set, List

# NOTE: This map is copied from Shiho's BlueWar implementation.
#       The file (dooum_rules.txt) may contain *partial* rules.
#       We always keep DEFAULT_DOOUM_MAP and merge extra rules on top.
#
# IMPORTANT(호환성):
#   Web 분석(승/패 분류)에서도 **Shiho 봇과 동일한 두음 적용 방식**을 사용한다.
#   - 단방향 규칙만 허용
#     예) '려' -> '여' 는 허용하지만, '여' -> '려' 역방향은 허용하지 않음.
#   - 자동(완전형) 두음 확장 같은 추가 추론은 하지 않음
#     (추가 추론을 넣으면 그래프가 과하게 연결되어 WIN/LOSE가 전부 DRAW로
#      무너질 수 있음)

DEFAULT_DOOUM_MAP: Dict[str, Set[str]] = {
    "녀": {"여"},
    "녁": {"역"},
    "년": {"연"},
    "녈": {"열"},
    "념": {"염"},
    "녑": {"엽"},
    "녓": {"엿"},
    "녕": {"영"},
    "뇨": {"요"},
    "뇰": {"욜"},
    "뇽": {"용"},
    "뉴": {"유"},
    "뉵": {"육"},
    "늄": {"윰"},
    "늉": {"융"},
    "니": {"이"},
    "닉": {"익"},
    "닌": {"인"},
    "닐": {"일"},
    "님": {"임"},
    "닙": {"입"},
    "닛": {"잇"},
    "닝": {"잉"},
    "닢": {"잎"},
    "라": {"나"},
    "락": {"낙"},
    "란": {"난"},
    "랄": {"날"},
    "람": {"남"},
    "랍": {"납"},
    "랫": {"낫"},
    "량": {"양"},
    "략": {"약"},
    "려": {"여"},
    "력": {"역"},
    "련": {"연"},
    "렬": {"열"},
    "렴": {"염"},
    "렵": {"엽"},
    "렷": {"엿"},
    "령": {"영"},
    "로": {"노"},
    "록": {"녹"},
    "론": {"논"},
    "롤": {"놀"},
    "롬": {"놈"},
    "롭": {"놉"},
    "롯": {"놋"},
    "료": {"요"},
    "룡": {"용"},
    "루": {"누"},
    "륙": {"육"},
    "륜": {"윤"},
    "률": {"율"},
    "륭": {"융"},
    "를": {"늘"},
    "리": {"이"},
    "린": {"인"},
    "림": {"임"},
    "립": {"입"},
    "릿": {"잇"},
    "링": {"잉"},
}

_THIS_DIR = Path(__file__).resolve().parent
DOOUM_RULES_FILE = _THIS_DIR / "dooum_rules.txt"


def _parse_dooum_text_as_lines(text: str) -> Dict[str, Set[str]]:
    """Parse dooum_rules.txt (line based).

    Accepted examples:
      녀: 여
      리: 이, 니
    """
    m: Dict[str, Set[str]] = defaultdict(set)
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().strip('"').strip("'")
        v = (v or "").strip()
        if not k or not v:
            continue
        parts: List[str] = []
        for token in v.replace(",", " ").split():
            t = token.strip().strip('"').strip("'")
            if t:
                parts.append(t)
        if parts:
            m[k].update(parts)
    return {k: set(v) for k, v in m.items()}


def _parse_dooum_text_as_literal(text: str) -> Dict[str, Set[str]]:
    """Fallback parser: allow dict literal format."""
    s = (text or "").strip()
    if not s:
        return {}
    try:
        data = ast.literal_eval(s)
        if not isinstance(data, dict):
            return {}
        out: Dict[str, Set[str]] = {}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            if isinstance(v, (set, list, tuple)):
                vals = {str(x) for x in v if str(x)}
            elif isinstance(v, str):
                vals = {v} if v else set()
            else:
                vals = set()
            if vals:
                out[k] = vals
        return out
    except Exception:
        return {}


def load_dooum_map(path: Path | None = None) -> Dict[str, Set[str]]:
    """Load and merge dooum rules.

    Safety rule:
      - Even if dooum_rules.txt exists but is partial,
        DEFAULT_DOOUM_MAP must remain active.
    """
    base: Dict[str, Set[str]] = {k: set(v) for k, v in DEFAULT_DOOUM_MAP.items()}
    p = path or DOOUM_RULES_FILE
    if not p.exists():
        return base
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return base

    extra = _parse_dooum_text_as_lines(text)
    if not extra:
        extra = _parse_dooum_text_as_literal(text)

    for k, vs in (extra or {}).items():
        if not k:
            continue
        base.setdefault(k, set()).update(set(vs or []))

    return base


DOOUM_MAP: Dict[str, Set[str]] = load_dooum_map()


def allowed_first_chars(last_char: str) -> Set[str]:
    """Return allowed starting syllables for the next word (Shiho-compatible).

    IMPORTANT:
      - Two-initial (두음) rules are applied in a *one-way* manner.
        예) last_char == '려' 이면 다음 단어 시작은 '려' 또는 '여' 가능
        하지만 last_char == '여' 에서 '려' 는 불가
    """
    c = (last_char or "").strip()
    if not c:
        return set()
    s = {c}
    s |= DOOUM_MAP.get(c, set())
    return s


def dooum_signature() -> str:
    """Stable signature for the effective dooum behavior.

    분석 캐시 키(analysis_key)에 들어가므로, "규칙이 바뀌면" 반드시 바뀌어야 한다.
    - 파일/기본 규칙(DOOUM_MAP)
    - 적용 모드(Shiho one-way only)
    """
    items = []
    for k in sorted(DOOUM_MAP.keys()):
        items.append((k, sorted(DOOUM_MAP[k])))

    meta = {
        "mode": "shiho-oneway-only",
        "file_map": items,
    }
    raw = repr(meta).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
