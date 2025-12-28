# app/routers/records.py

from __future__ import annotations

from typing import Dict, List, Optional, Set, TypedDict

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user
from app import models
from app.utils.time import fmt_kst


router = APIRouter(
    prefix="/records",
    tags=["records"],
)

templates = Jinja2Templates(directory="app/templates")


class MatchRow(TypedDict):
    match: models.BlueWarMatch
    display_id: str
    mode_label: str
    starter_name: str
    winner_name: str
    loser_name: str
    started_at: str
    finished_at: str


REASON_KO = {
    "user_no_move": "사용자가 단어를 입력하지 않음",
    "user_timeout": "시간 초과",
    "time_out": "시간 초과",
    "timeout": "시간 초과",
    "invalid_word": "유효하지 않은 단어",
    "not_in_dict": "사전에 없는 단어",
    "already_used": "이미 사용된 단어",
    "used_word": "이미 사용된 단어",
    "wrong_start": "시작 글자 불일치",
    "rule_violation": "규칙 위반",
    "aborted": "중단",
}


def _parse_note(note: Optional[str]) -> tuple[Optional[str], str]:
    """note에서 reason을 추출하고, UI에 불필요한 토큰(practice/pvp)을 제거한다.

    Returns:
        (reason_code, cleaned_note)
    """

    if not note:
        return None, ""

    tokens = [t.strip() for t in str(note).split(",") if t.strip()]
    reason_code: Optional[str] = None
    cleaned: list[str] = []

    for t in tokens:
        tl = t.lower()
        if tl in {"practice", "pvp", "pv", "versus", "vs"}:
            continue
        if tl.startswith("reason="):
            reason_code = t.split("=", 1)[1].strip()
            continue
        cleaned.append(t)

    return reason_code, ", ".join(cleaned)


def _mode_label(mode: str, *, ai_label: Optional[str] = None) -> str:
    m = (mode or "").strip().lower()
    if m == "pvp":
        return "PVP"
    if m == "practice":
        # ai_label이 있으면 "연습 (난이도/AI)" 형태로 표시
        if ai_label:
            label = ai_label
            if label.strip().lower() == "yume":
                label = "유메"
            return f"연습 ({label})"
        return "연습"
    return mode


def _resolve_display_name(
    *,
    discord_id: Optional[str],
    users_by_discord: Dict[str, models.User],
    fallback_names_by_discord: Dict[str, str],
) -> str:
    if not discord_id:
        return "-"
    if str(discord_id).strip().lower() == "yume":
        return "유메"
    u = users_by_discord.get(discord_id)
    if u and u.nickname:
        return u.nickname
    if discord_id in fallback_names_by_discord:
        return fallback_names_by_discord[discord_id]
    return discord_id


@router.get("/", response_class=HTMLResponse)
def list_records(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
    limit: int = 200,
    mode: str = Query(default="pvp", description="pvp|practice|all"),
):
    """블루전 매치 목록 페이지(최신순)."""

    mode_norm = (mode or "pvp").strip().lower()
    if mode_norm not in {"pvp", "practice", "all"}:
        mode_norm = "pvp"

    # 탭 표시용 카운트
    pvp_count = db.query(models.BlueWarMatch).filter(models.BlueWarMatch.mode == "pvp").count()
    practice_count = db.query(models.BlueWarMatch).filter(models.BlueWarMatch.mode == "practice").count()

    q = db.query(models.BlueWarMatch)
    if mode_norm in {"pvp", "practice"}:
        q = q.filter(models.BlueWarMatch.mode == mode_norm)

    total_matches = q.count()

    matches: List[models.BlueWarMatch] = (
        q
        .order_by(models.BlueWarMatch.id.desc())
        .limit(max(1, min(int(limit), 1000)))
        .all()
    )

    # 표시용: 모드별(=PVP/연습) 시퀀스 번호를 만든다. (PVP#1, 연습#1)
    seq_by_match_id: Dict[int, int] = {}
    if matches:
        seq_subq = (
            db.query(
                models.BlueWarMatch.id.label("id"),
                models.BlueWarMatch.mode.label("mode"),
                func.row_number()
                .over(
                    partition_by=models.BlueWarMatch.mode,
                    order_by=models.BlueWarMatch.id.asc(),
                )
                .label("seq"),
            )
        ).subquery()

        rows_seq = (
            db.query(seq_subq.c.id, seq_subq.c.seq)
            .filter(seq_subq.c.id.in_([m.id for m in matches]))
            .all()
        )
        seq_by_match_id = {int(r.id): int(r.seq) for r in rows_seq}

    # 한 번에 표시 이름을 resolve 하기 위해 필요한 discord_id들을 모은다.
    discord_ids: Set[str] = set()
    match_ids: List[int] = []
    for m in matches:
        match_ids.append(m.id)
        if m.starter_discord_id:
            discord_ids.add(m.starter_discord_id)
        if m.winner_discord_id:
            discord_ids.add(m.winner_discord_id)
        if m.loser_discord_id:
            discord_ids.add(m.loser_discord_id)

    users_by_discord: Dict[str, models.User] = {}
    if discord_ids:
        users = (
            db.query(models.User)
            .filter(models.User.discord_id.in_(list(discord_ids)))
            .all()
        )
        users_by_discord = {u.discord_id: u for u in users}

    # users 테이블에 없더라도 participant.name이 있는 경우가 있으므로 fallback으로 쓴다.
    fallback_names_by_discord: Dict[str, str] = {}
    winner_guess_by_match_id: Dict[int, str] = {}
    ai_label_by_match_id: Dict[int, str] = {}
    if match_ids:
        practice_ids = {int(m.id) for m in matches if (m.mode or "").lower() == "practice"}
        parts = (
            db.query(models.BlueWarParticipant)
            .filter(models.BlueWarParticipant.match_id.in_(match_ids))
            .all()
        )
        for p in parts:
            if p.discord_id and p.name and p.discord_id not in fallback_names_by_discord:
                fallback_names_by_discord[p.discord_id] = p.name

            # winner_discord_id가 비어있는(특히 연습) 매치에서, 참가자 플래그로 승자를 추정한다.
            if p.match_id and p.is_winner and p.match_id not in winner_guess_by_match_id:
                if p.user and p.user.nickname:
                    winner_guess_by_match_id[p.match_id] = p.user.nickname
                elif p.ai_name:
                    winner_guess_by_match_id[p.match_id] = p.ai_name
                elif p.name:
                    winner_guess_by_match_id[p.match_id] = p.name
                elif p.discord_id:
                    winner_guess_by_match_id[p.match_id] = p.discord_id

            # 연습 모드에서 난이도/AI 이름(표시용)을 추정
            if p.match_id and int(p.match_id) in practice_ids and int(p.match_id) not in ai_label_by_match_id:
                label: Optional[str] = None
                if p.ai_name:
                    label = p.ai_name
                elif p.name:
                    label = p.name
                if label:
                    ai_label_by_match_id[int(p.match_id)] = label

    rows: List[MatchRow] = []
    for m in matches:
        seq = seq_by_match_id.get(int(m.id), int(m.id))
        if (m.mode or "").lower() == "pvp":
            display_id = f"#{seq}"
        elif (m.mode or "").lower() == "practice":
            display_id = f"#{10000 + int(seq)}"
        else:
            display_id = f"#{seq}"

        winner_name = _resolve_display_name(
            discord_id=m.winner_discord_id,
            users_by_discord=users_by_discord,
            fallback_names_by_discord=fallback_names_by_discord,
        )
        # 특히 연습 매치에서 winner_discord_id가 None으로 올 수 있다. -> 참가자 정보로 보정
        if winner_name == "-" and m.status == "finished":
            guessed = winner_guess_by_match_id.get(int(m.id), "-")
            if isinstance(guessed, str) and guessed.strip().lower() == "yume":
                guessed = "유메"
            if guessed != "-":
                winner_name = guessed
            elif m.mode == "practice" and m.loser_discord_id:
                # 최후의 fallback: 패자가 있으면 승자는 유메(봇)로 표시
                winner_name = "유메"

        rows.append(
            {
                "match": m,
                "display_id": display_id,
                "mode_label": _mode_label(m.mode, ai_label=ai_label_by_match_id.get(int(m.id))),
                "starter_name": _resolve_display_name(
                    discord_id=m.starter_discord_id,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "winner_name": winner_name,
                "loser_name": _resolve_display_name(
                    discord_id=m.loser_discord_id,
                    users_by_discord=users_by_discord,
                    fallback_names_by_discord=fallback_names_by_discord,
                ),
                "started_at": fmt_kst(m.started_at, "%Y-%m-%d %H:%M:%S"),
                "finished_at": fmt_kst(m.finished_at, "%Y-%m-%d %H:%M:%S"),
            }
        )

    return templates.TemplateResponse(
        "records_list.html",
        {
            "request": request,
            "total_matches": total_matches,
            "rows": rows,
            "mode": mode_norm,
            "pvp_count": pvp_count,
            "practice_count": practice_count,
        },
    )


@router.get("/{match_id}", response_class=HTMLResponse)
def record_detail(
    match_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
):
    match: Optional[models.BlueWarMatch] = (
        db.query(models.BlueWarMatch)
        .filter(models.BlueWarMatch.id == match_id)
        .first()
    )

    if not match:
        # 템플릿에서 graceful 하게 처리
        return templates.TemplateResponse(
            "record_detail.html",
            {
                "request": request,
                "match": None,
            },
            status_code=404,
        )

    participants: List[models.BlueWarParticipant] = (
        db.query(models.BlueWarParticipant)
        .filter(models.BlueWarParticipant.match_id == match_id)
        .order_by(models.BlueWarParticipant.side.asc(), models.BlueWarParticipant.id.asc())
        .all()
    )

    # 표시용: 모드별 시퀀스 번호
    seq_subq = (
        db.query(
            models.BlueWarMatch.id.label("id"),
            models.BlueWarMatch.mode.label("mode"),
            func.row_number()
            .over(
                partition_by=models.BlueWarMatch.mode,
                order_by=models.BlueWarMatch.id.asc(),
            )
            .label("seq"),
        )
    ).subquery()
    seq = (
        db.query(seq_subq.c.seq)
        .filter(seq_subq.c.id == int(match.id))
        .scalar()
    )
    seq_i = int(seq) if seq is not None else int(match.id)
    if (match.mode or "").lower() == "pvp":
        display_id = f"#{seq_i}"
    elif (match.mode or "").lower() == "practice":
        display_id = f"#{10000 + int(seq_i)}"
    else:
        display_id = f"#{seq_i}"

    # 표시용: 연습 난이도/AI 라벨 추정
    ai_label: Optional[str] = None
    if (match.mode or "").lower() == "practice":
        for p in participants:
            if p.ai_name:
                ai_label = p.ai_name
                break
            if p.name:
                ai_label = p.name
                break

    mode_label = _mode_label(match.mode, ai_label=ai_label)

    # 표시용: 디스코드 ID -> 닉네임 resolve
    ids: Set[str] = set()
    for did in [match.starter_discord_id, match.winner_discord_id, match.loser_discord_id]:
        if did:
            ids.add(did)

    users_by_discord: Dict[str, models.User] = {}
    if ids:
        users = db.query(models.User).filter(models.User.discord_id.in_(list(ids))).all()
        users_by_discord = {u.discord_id: u for u in users}

    fallback_names_by_discord: Dict[str, str] = {}
    for p in participants:
        if p.discord_id and p.name and p.discord_id not in fallback_names_by_discord:
            fallback_names_by_discord[p.discord_id] = p.name

    starter_name = _resolve_display_name(
        discord_id=match.starter_discord_id,
        users_by_discord=users_by_discord,
        fallback_names_by_discord=fallback_names_by_discord,
    )
    winner_name = _resolve_display_name(
        discord_id=match.winner_discord_id,
        users_by_discord=users_by_discord,
        fallback_names_by_discord=fallback_names_by_discord,
    )
    loser_name = _resolve_display_name(
        discord_id=match.loser_discord_id,
        users_by_discord=users_by_discord,
        fallback_names_by_discord=fallback_names_by_discord,
    )

    # 연습은 winner_discord_id가 비어있을 수 있다 -> 참가자 플래그로 보정
    if winner_name == "-" and (match.mode or "").lower() == "practice" and match.status == "finished":
        guessed: Optional[str] = None
        for p in participants:
            if p.is_winner:
                if p.user and p.user.nickname:
                    guessed = p.user.nickname
                elif p.ai_name:
                    guessed = p.ai_name
                elif p.name:
                    guessed = p.name
                elif p.discord_id:
                    guessed = p.discord_id
                break
        if guessed:
            winner_name = "유메" if guessed.strip().lower() == "yume" else guessed
        elif loser_name != "-":
            winner_name = "유메"

    reason_code, note_clean = _parse_note(match.note)
    if reason_code:
        rc_norm = (reason_code or "").strip().lower()
        reason_ko = REASON_KO.get(rc_norm) or f"기타 ({reason_code})"
    else:
        reason_ko = None

    return templates.TemplateResponse(
        "record_detail.html",
        {
            "request": request,
            "match": match,
            "participants": participants,
            "display_id": display_id,
            "mode_label": mode_label,
            "starter_name": starter_name,
            "winner_name": winner_name,
            "loser_name": loser_name,
            "reason_ko": reason_ko,
            "note_clean": note_clean,
            "started_at": fmt_kst(match.started_at, "%Y-%m-%d %H:%M:%S"),
            "finished_at": fmt_kst(match.finished_at, "%Y-%m-%d %H:%M:%S"),
        },
    )
