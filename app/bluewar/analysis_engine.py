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

    def _iter_tokens(line: str) -> List[str]:
        """Return word tokens from a single line.

        Supports both:
          - one word per line: "갑니다토라마루"
          - grouped by syllable: "갑 : 갑니다토라마루, 갑작스러운제안"
          - comma separated: "a, b, c"
        """
        s = (line or "").strip()
        if not s:
            return []
        if s.startswith("#"):
            return []

        if ":" in s:
            # "갑 : ..." 형태면 ':' 오른쪽만 파싱
            _, right = s.split(":", 1)
            s = right.strip()
            if not s:
                return []

        # commas first
        if "," in s:
            parts = [x.strip() for x in s.split(",")]
        else:
            # fallback: whitespace separated tokens
            parts = s.split()

        return [p for p in parts if p]

    words: List[str] = []
    seen = set()
    for raw in text.splitlines():
        for tok in _iter_tokens(raw):
            w = _normalize_word(tok)
            if not w:
                continue
            # Don't allow internal whitespace in a word token.
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


@dataclass
class ComputedGraph:
    """In-memory computed graph (cached per analysis_key).

    This is used for explanation pages so we can show a concrete word route
    (principal variation) without storing the entire graph in DB.
    """

    analysis_key: str
    status: Dict[str, str]  # syllable -> NodeType
    out_moves: Dict[str, int]
    in_moves: Dict[str, int]
    moves: Dict[str, List[Tuple[str, str]]]  # syllable -> [(word, next_syllable)]
    pred_edges: Dict[str, List[Tuple[str, str]]]  # next_syllable -> [(prev_syllable, word)]
    mate_dist: Dict[str, int]  # only for WIN/LOSE nodes (0 means immediate loss for player to move)
    win_witness: Dict[str, Tuple[str, str]]  # syllable -> (word, next_syllable) where next is LOSE
    lose_best: Dict[str, Tuple[str, str]]  # syllable -> (word, next_syllable) that prolongs the loss


_GRAPH_CACHE: Dict[str, ComputedGraph] = {}
_GRAPH_CACHE_ORDER: List[str] = []
_GRAPH_CACHE_MAX = 8


def _cache_get(key: str) -> Optional[ComputedGraph]:
    g = _GRAPH_CACHE.get(key)
    if not g:
        return None
    # refresh LRU
    try:
        _GRAPH_CACHE_ORDER.remove(key)
    except ValueError:
        pass
    _GRAPH_CACHE_ORDER.append(key)
    return g


def _cache_put(key: str, graph: ComputedGraph) -> None:
    _GRAPH_CACHE[key] = graph
    try:
        _GRAPH_CACHE_ORDER.remove(key)
    except ValueError:
        pass
    _GRAPH_CACHE_ORDER.append(key)
    while len(_GRAPH_CACHE_ORDER) > _GRAPH_CACHE_MAX:
        old = _GRAPH_CACHE_ORDER.pop(0)
        _GRAPH_CACHE.pop(old, None)


def get_computed_graph(*, analysis_key: str, words: List[str]) -> ComputedGraph:
    """Get (or compute) a cached graph for explanation."""
    g = _cache_get(analysis_key)
    if g:
        return g

    status, out_moves, in_moves, moves, preds, pred_edges, win_witness, lose_best, dist = _retrograde_proof(words)
    g = ComputedGraph(
        analysis_key=analysis_key,
        status=status,
        out_moves=out_moves,
        in_moves=in_moves,
        moves=moves,
        pred_edges=pred_edges,
        mate_dist=dist,
        win_witness=win_witness,
        lose_best=lose_best,
    )
    _cache_put(analysis_key, g)
    return g


def _retrograde_proof(
    words: List[str],
) -> Tuple[
    Dict[str, str],
    Dict[str, int],
    Dict[str, int],
    Dict[str, List[Tuple[str, str]]],
    Dict[str, List[str]],
    Dict[str, List[Tuple[str, str]]],
    Dict[str, Tuple[str, str]],
    Dict[str, Tuple[str, str]],
    Dict[str, int],
]:
    """Compute syllable WIN/LOSE/DRAW + proof helpers.

    Returns:
      status, out_moves, in_moves, moves, preds,
      pred_edges, win_witness, lose_best, mate_dist
    """

    # Index words by first syllable
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

    nodes: set[str] = set(firsts) | set(lasts)
    moves: Dict[str, List[Tuple[str, str]]] = {n: [] for n in nodes}
    preds: Dict[str, List[str]] = {n: [] for n in nodes}
    pred_edges: Dict[str, List[Tuple[str, str]]] = {n: [] for n in nodes}
    out_moves: Dict[str, int] = {n: 0 for n in nodes}
    in_moves: Dict[str, int] = {n: 0 for n in nodes}

    # Build moves and predecessor lists (counting each word as one move)
    # NOTE: dooum is applied as "allowed first syllables" on the current syllable.
    #       That is consistent with Shiho's rule: 받침음절 X -> 두음 적용 가능한 시작음절(단방향)로도 시작 가능.
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
                    pred_edges[v] = []
                    out_moves[v] = 0
                    in_moves[v] = 0
                moves[u].append((w, v))
                preds[v].append(u)
                pred_edges[v].append((u, w))
                out_moves[u] += 1
                in_moves[v] += 1

    # Retrograde
    status: Dict[str, str] = {}
    remaining: Dict[str, int] = {n: int(out_moves.get(n, 0)) for n in nodes}

    # mate_dist: number of plies to reach a terminal loss for the player who is to move,
    # under optimal play. For LOSE nodes, this is "time until you lose" (you want it large).
    # For WIN nodes, this is "time until you force the opponent to lose" (you want it small).
    mate_dist: Dict[str, int] = {}
    win_witness: Dict[str, Tuple[str, str]] = {}
    lose_best: Dict[str, Tuple[str, str]] = {}

    q: deque[str] = deque()

    # Terminal losing nodes: no outgoing moves
    for n in nodes:
        if int(out_moves.get(n, 0)) == 0:
            status[n] = NodeType.LOSE
            mate_dist[n] = 0
            q.append(n)

    def _pick_word_for_edge(u: str, v: str) -> str:
        # deterministic: shortest, then lexicographic
        cands = [w for (w, to) in moves.get(u, []) if to == v]
        if cands:
            return min(cands, key=lambda x: (len(x), x))
        # fallback: scan pred_edges[v]
        for pu, pw in pred_edges.get(v, []):
            if pu == u:
                return pw
        return ""

    while q:
        v = q.popleft()
        t = status.get(v)
        if not t:
            continue

        if t == NodeType.LOSE:
            # predecessors can become WIN (they can move to a LOSE node)
            for u in preds.get(v, []):
                if status.get(u) is None:
                    status[u] = NodeType.WIN
                    w = _pick_word_for_edge(u, v)
                    win_witness[u] = (w, v)
                    mate_dist[u] = int(mate_dist.get(v, 0)) + 1
                    q.append(u)
                elif status.get(u) == NodeType.WIN:
                    # better (shorter) witness possible
                    new_d = int(mate_dist.get(v, 0)) + 1
                    old_d = mate_dist.get(u)
                    if old_d is None or new_d < int(old_d):
                        w = _pick_word_for_edge(u, v)
                        win_witness[u] = (w, v)
                        mate_dist[u] = new_d

        elif t == NodeType.WIN:
            # decrement predecessors; if no remaining moves, they become LOSE
            for u in preds.get(v, []):
                if status.get(u):
                    continue
                remaining[u] = max(0, int(remaining.get(u, 0)) - 1)
                if remaining[u] == 0:
                    status[u] = NodeType.LOSE

                    # all successors are WIN (by definition here), pick the one that maximizes mate_dist
                    best_move: Optional[Tuple[str, str]] = None
                    best_succ_d = -1
                    for w2, to2 in moves.get(u, []):
                        if status.get(to2) != NodeType.WIN:
                            continue
                        d2 = int(mate_dist.get(to2, 0))
                        if d2 > best_succ_d:
                            best_succ_d = d2
                            best_move = (w2, to2)
                        elif d2 == best_succ_d and best_move is not None:
                            # deterministic tie-breaker for display (shorter word first)
                            if (len(w2), w2) < (len(best_move[0]), best_move[0]):
                                best_move = (w2, to2)

                    if best_move is None:
                        # fallback: choose any (shortest) move
                        mv = moves.get(u, [])
                        if mv:
                            best_move = min(mv, key=lambda x: (len(x[0]), x[0]))
                            best_succ_d = int(mate_dist.get(best_move[1], 0))
                        else:
                            best_move = ("", "")
                            best_succ_d = 0

                    lose_best[u] = best_move
                    mate_dist[u] = best_succ_d + 1
                    q.append(u)

    for n in nodes:
        if not status.get(n):
            status[n] = NodeType.DRAW

    return status, out_moves, in_moves, moves, preds, pred_edges, win_witness, lose_best, mate_dist


def _retrograde_syllables(
    words: List[str],
) -> Tuple[
    Dict[str, str],
    Dict[str, int],
    Dict[str, int],
    Dict[str, List[Tuple[str, str]]],
    Dict[str, List[str]],
]:
    """Compatibility wrapper used by rebuild_analysis()."""
    status, out_moves, in_moves, moves, preds, _pred_edges, _ww, _lb, _dist = _retrograde_proof(words)
    return status, out_moves, in_moves, moves, preds


def _find_draw_cycle(
    start: str,
    moves: Dict[str, List[Tuple[str, str]]],
    status: Dict[str, str],
    max_depth: int = 8,
) -> Optional[List[Tuple[str, str, str]]]:
    """Try to find a short cycle within DRAW subgraph.

    Returns list of (from, word, to) if found.
    """
    if status.get(start) != NodeType.DRAW:
        return None

    from collections import deque as _dq

    # node -> (prev_node, word)
    prev: Dict[str, Tuple[str, str]] = {}
    q = _dq([(start, 0)])
    visited = {start}

    while q:
        u, d = q.popleft()
        if d >= max_depth:
            continue
        for w, v in moves.get(u, []):
            if status.get(v) != NodeType.DRAW:
                continue
            if v == start and u != start:
                # reconstruct: start -> ... -> u -> start
                path_nodes = [u]
                while path_nodes[-1] != start:
                    pn, _pw = prev[path_nodes[-1]]
                    path_nodes.append(pn)
                path_nodes.reverse()  # [start, ..., u]
                # build edges
                edges: List[Tuple[str, str, str]] = []
                cur = start
                for nxt in path_nodes[1:]:
                    pn, pw = prev[nxt]
                    edges.append((pn, pw, nxt))
                    cur = nxt
                edges.append((u, w, start))
                return edges

            if v not in visited:
                visited.add(v)
                prev[v] = (u, w)
                q.append((v, d + 1))

    return None


def explain_syllable(
    *,
    analysis_key: str,
    words: List[str],
    syllable: str,
    max_steps: int = 10,
) -> Dict[str, object]:
    """Explain why a syllable is WIN/LOSE/DRAW, including a sample word route."""

    g = get_computed_graph(analysis_key=analysis_key, words=words)

    syl = (syllable or "").strip()
    t = g.status.get(syl)
    if not syl or not t:
        return {
            "syllable": syl,
            "node_type": None,
            "reason": "unknown syllable",
            "allowed_first": [],
            "line": [],
        }

    allowed = sorted(list(allowed_first_chars(syl)))
    out_n = int(g.out_moves.get(syl, 0))

    reason = ""
    if t == NodeType.WIN:
        w, v = g.win_witness.get(syl, ("", ""))
        md = g.mate_dist.get(syl)
        if w and v:
            reason = f"WIN: '{w}'(으)로 '{v}'에 보내면 상대는 LOSE 상태가 된다."
        else:
            reason = "WIN: 상대를 LOSE로 보내는 수가 존재한다."
        if md is not None:
            reason += f" (최단 {md}수 내 승리)"
    elif t == NodeType.LOSE:
        if out_n == 0:
            reason = "LOSE: 낼 수 있는 단어가 0개라서 즉시 패배."
        else:
            md = g.mate_dist.get(syl)
            reason = "LOSE: 어떤 단어를 내더라도 상대가 WIN 전략을 가진다."
            if md is not None:
                reason += f" (최선으로 버텨도 약 {md}수 내 패배)"
    else:
        reason = "DRAW: 승/패가 결정되지 않는 순환(무한 루프 가능) 영역."

    # Build a principal variation line (sample route).
    line: List[Dict[str, object]] = []

    cur = syl
    player = 0  # 0: 나(현재 플레이어), 1: 상대
    for step in range(max_steps):
        ct = g.status.get(cur)
        if ct == NodeType.LOSE and int(g.out_moves.get(cur, 0)) == 0:
            # terminal loss for player to move
            line.append(
                {
                    "turn": step + 1,
                    "player": "상대" if player == 1 else "나",
                    "from": cur,
                    "word": None,
                    "to": None,
                    "to_type": None,
                    "note": "낼 수 있는 단어가 없어 패배",
                }
            )
            break

        if ct == NodeType.DRAW:
            cyc = _find_draw_cycle(cur, g.moves, g.status, max_depth=min(8, max_steps))
            if cyc:
                for (u, w, v) in cyc[: max_steps - step]:
                    line.append(
                        {
                            "turn": len(line) + 1,
                            "player": "상대" if player == 1 else "나",
                            "from": u,
                            "word": w,
                            "to": v,
                            "to_type": g.status.get(v),
                            "note": "DRAW 순환 예시",
                        }
                    )
                    player = 1 - player
                break
            line.append(
                {
                    "turn": step + 1,
                    "player": "상대" if player == 1 else "나",
                    "from": cur,
                    "word": None,
                    "to": None,
                    "to_type": None,
                    "note": "DRAW: 순환 영역(예시 경로를 찾지 못함)",
                }
            )
            break

        move: Optional[Tuple[str, str]] = None
        note = ""
        if ct == NodeType.WIN:
            move = g.win_witness.get(cur)
            if not move:
                # fallback: pick any move to LOSE
                cands = [(w, v) for (w, v) in g.moves.get(cur, []) if g.status.get(v) == NodeType.LOSE]
                if cands:
                    move = min(cands, key=lambda x: (len(x[0]), x[0]))
            note = "상대를 LOSE로 보내는 수" if move else "WIN이지만 증거 수를 찾지 못함"
        elif ct == NodeType.LOSE:
            move = g.lose_best.get(cur)
            if not move:
                mv = g.moves.get(cur, [])
                if mv:
                    move = min(mv, key=lambda x: (len(x[0]), x[0]))
            note = "최선의 방어(버티기)" if move else "LOSE이지만 이동이 없음"

        if not move or not move[0] or not move[1]:
            line.append(
                {
                    "turn": step + 1,
                    "player": "상대" if player == 1 else "나",
                    "from": cur,
                    "word": None,
                    "to": None,
                    "to_type": None,
                    "note": "경로 생성 실패",
                }
            )
            break

        w, nxt = move
        line.append(
            {
                "turn": step + 1,
                "player": "상대" if player == 1 else "나",
                "from": cur,
                "word": w,
                "to": nxt,
                "to_type": g.status.get(nxt),
                "note": note,
            }
        )

        cur = nxt
        player = 1 - player

    return {
        "syllable": syl,
        "node_type": t,
        "reason": reason,
        "allowed_first": allowed,
        "out_moves": out_n,
        "in_moves": int(g.in_moves.get(syl, 0)),
        "mate_dist": g.mate_dist.get(syl),
        "line": line,
    }

def rebuild_analysis(
    db: Session,
    *,
    list_name: str = "blue_archive_words",
    pack_version: Optional[str] = None,
    sample_words: int = 8,
    progress_cb=None,
) -> AnalysisInput:
    """Recompute and store analysis results.

    This is safe to call multiple times; it overwrites rows for the same analysis_key.
    """

    meta, words = prepare_input(db, list_name=list_name, pack_version=pack_version)

    def _progress(cur: int, total: int, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(int(cur), int(total), str(msg))
            except Exception:
                # Progress is best-effort; never break analysis.
                pass

    total_steps = 5
    _progress(1, total_steps, "compute graph")

    status, out_moves, in_moves, moves, _preds = _retrograde_syllables(words)

    _progress(2, total_steps, "delete old rows")

    # Remove existing rows for this key
    db.query(models.BlueWarSyllableStat).filter(models.BlueWarSyllableStat.analysis_key == meta.analysis_key).delete()
    db.query(models.BlueWarWordStat).filter(models.BlueWarWordStat.analysis_key == meta.analysis_key).delete()
    db.query(models.BlueWarAnalysisMeta).filter(models.BlueWarAnalysisMeta.analysis_key == meta.analysis_key).delete()

    now = datetime.utcnow()

    _progress(3, total_steps, "insert meta")

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

    _progress(4, total_steps, "insert syllables")

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

    _progress(5, total_steps, "insert words & commit")

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
