# app/routers/users.py

from typing import List, Optional

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_current_admin_user
from app import models

router = APIRouter(
    prefix="/users",
    tags=["users"],
)

templates = Jinja2Templates(directory="app/templates")


def get_user_or_404(db: Session, user_id: int) -> models.User:
    user = (
        db.query(models.User)
        .filter(models.User.id == user_id)
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/", name="users_list")
async def users_list(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
):
    users: List[models.User] = db.query(models.User).order_by(models.User.id.asc()).all()
    return templates.TemplateResponse(
        "users_list.html",
        {
            "request": request,
            "users": users,
        },
    )


@router.get("/create")
async def user_create_form(
    request: Request,
    admin=Depends(get_current_admin_user),
):
    return templates.TemplateResponse(
        "user_create.html",
        {"request": request},
    )


@router.post("/create")
async def user_create(
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
    discord_id: str = Form(...),
    nickname: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
):
    user = models.User(
        discord_id=discord_id,
        nickname=nickname or "",
        note=note or "",
        base_wins=0,
        base_losses=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return RedirectResponse(url="/users/", status_code=303)


@router.get("/{user_id}")
async def user_detail(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
):
    user = get_user_or_404(db, user_id)
    return templates.TemplateResponse(
        "user_detail.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.get("/{user_id}/edit")
async def user_edit_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
):
    user = get_user_or_404(db, user_id)
    return templates.TemplateResponse(
        "user_edit.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.post("/{user_id}/edit")
async def user_edit(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
    discord_id: str = Form(...),
    nickname: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
):
    user = get_user_or_404(db, user_id)

    user.discord_id = discord_id
    user.nickname = nickname or ""
    user.note = note or ""

    db.commit()
    db.refresh(user)

    return RedirectResponse(url=f"/users/{user.id}", status_code=303)


@router.get("/{user_id}/stats/edit")
async def user_stats_edit_form(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
):
    """
    기본 승/패 전적 수정 폼
    """
    user = get_user_or_404(db, user_id)
    return templates.TemplateResponse(
        "user_stats_edit.html",
        {
            "request": request,
            "user": user,
        },
    )


@router.post("/{user_id}/stats/edit")
async def user_stats_edit(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin=Depends(get_current_admin_user),
    base_wins: int = Form(...),
    base_losses: int = Form(...),
):
    user = get_user_or_404(db, user_id)

    # 음수 방지 정도만 간단히
    user.base_wins = max(0, int(base_wins))
    user.base_losses = max(0, int(base_losses))

    db.commit()
    db.refresh(user)

    return RedirectResponse(url=f"/users/{user.id}", status_code=303)
