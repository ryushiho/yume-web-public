from __future__ import annotations

import ast
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, Set, List


# NOTE: This map is copied from Shiho's BlueWar implementation.
#       The file (dooum_rules.txt) may contain *partial* rules.
#       We always keep DEFAULT_DOOUM_MAP and merge extra rules on top.
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


# ------------------------------------------------------------
# Auto 두음 확장(완전형)
# ------------------------------------------------------------
# dooum_rules.txt(및 DEFAULT_DOOUM_MAP)는 "자주 쓰는" 규칙만 포함할 수 있어
# 희귀 음절(예: '르' -> '느')이 빠지는 경우가 있습니다.
#
# 실제 두음법칙의 핵심 변환은 다음 두 가지입니다.
#   1) 초성 ㄹ -> ㄴ (모든 모음에서)
#   2) 초성 ㄴ -> ㅇ (모음이 'ㅣ' 또는 'ㅑ/ㅕ/ㅛ/ㅠ/ㅖ/ㅒ/ㅟ' 계열일 때)
#
# Shiho 쪽은 파일 기반 규칙을 우선하지만, web 분석 기능에서는 "누락 없이" 적용되는 게
# 더 유리해서 자동 확장을 추가합니다. (단방향만)

_AUTO_CACHE: Dict[str, Set[str]] = {}


def _is_hangul_syllable(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3


# Hangul syllable decomposition constants
_S_BASE = 0xAC00
_L_BASE = 0x1100
_V_BASE = 0x1161
_T_BASE = 0x11A7
_L_COUNT = 19
_V_COUNT = 21
_T_COUNT = 28
_N_COUNT = _V_COUNT * _T_COUNT


def _decompose_syllable(ch: str) -> tuple[int, int, int] | None:
    if not _is_hangul_syllable(ch):
        return None
    s_index = ord(ch) - _S_BASE
    l_index = s_index // _N_COUNT
    v_index = (s_index % _N_COUNT) // _T_COUNT
    t_index = s_index % _T_COUNT
    return l_index, v_index, t_index


def _compose_syllable(l_index: int, v_index: int, t_index: int) -> str | None:
    if not (0 <= l_index < _L_COUNT and 0 <= v_index < _V_COUNT and 0 <= t_index < _T_COUNT):
        return None
    code = _S_BASE + (l_index * _N_COUNT) + (v_index * _T_COUNT) + t_index
    return chr(code)


# 초성 인덱스: ㄴ, ㄹ (Hangul choseong order)
_L_NIEUN = 2
_L_RIEUL = 5
_L_IEUNG = 11


# 'ㅣ' 및 반모음(ya/yeo/yo/yu) + ye/yae + wi 계열에서 ㄴ -> ㅇ 적용
# Hangul jungseong index (0-based):
#  ㅣ=20, ㅑ=2, ㅕ=6, ㅛ=12, ㅠ=17, ㅖ=7, ㅒ=3, ㅟ=16
_V_N_TO_IEUNG = {20, 2, 6, 12, 17, 7, 3, 16}


def _auto_dooum_targets(ch: str) -> Set[str]:
    """Compute auto two-initial targets for a single syllable (one-way)."""
    c = (ch or "").strip()
    if not c or not _is_hangul_syllable(c):
        return set()
    cached = _AUTO_CACHE.get(c)
    if cached is not None:
        return set(cached)

    dec = _decompose_syllable(c)
    if dec is None:
        _AUTO_CACHE[c] = set()
        return set()
    l, v, t = dec

    # closure over the two transformations
    out: Set[str] = set()
    frontier: Set[tuple[int, int, int]] = {(l, v, t)}
    visited: Set[tuple[int, int, int]] = set(frontier)

    while frontier:
        nl, nv, nt = frontier.pop()

        # 1) ㄹ -> ㄴ
        if nl == _L_RIEUL:
            nxt = (_L_NIEUN, nv, nt)
            if nxt not in visited:
                visited.add(nxt)
                frontier.add(nxt)

        # 2) ㄴ -> ㅇ (조건 모음)
        if nl == _L_NIEUN and nv in _V_N_TO_IEUNG:
            nxt = (_L_IEUNG, nv, nt)
            if nxt not in visited:
                visited.add(nxt)
                frontier.add(nxt)

    for xl, xv, xt in visited:
        if (xl, xv, xt) == (l, v, t):
            continue
        composed = _compose_syllable(xl, xv, xt)
        if composed:
            out.add(composed)

    _AUTO_CACHE[c] = set(out)
    return set(out)


def allowed_first_chars(last_char: str) -> Set[str]:
    """Return allowed starting syllables for the next word.

    IMPORTANT: Two-initial (두음) rules are applied in a *one-way* manner.
      - If last_char == '리' then '이' is allowed (리 -> 이).
      - But last_char == '이' does NOT allow '리'.
    """
    c = (last_char or "").strip()
    if not c:
        return set()
    s = {c}
    # 1) file/DEFAULT 기반 (Shiho 호환)
    s |= DOOUM_MAP.get(c, set())

    # 2) 자동 확장 (누락 보완)
    s |= _auto_dooum_targets(c)
    return s


def dooum_signature() -> str:
    """Stable signature for the effective dooum behavior.

    분석 캐시 키(analysis_key)에 들어가므로, "규칙이 바뀌면" 반드시 바뀌어야 한다.
    - 파일/기본 규칙(DOOUM_MAP)
    - 자동 확장 규칙(closure) 버전/파라미터
    """
    items = []
    for k in sorted(DOOUM_MAP.keys()):
        items.append((k, sorted(DOOUM_MAP[k])))

    meta = {
        "file_map": items,
        "auto_version": "auto-closure-v1",
        "auto_vowels": sorted(list(_V_N_TO_IEUNG)),
    }
    raw = repr(meta).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
