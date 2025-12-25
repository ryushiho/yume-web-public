# app/routers/admin_members.py

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import models
from app.dependencies import get_current_admin_user, get_db

router = APIRouter(prefix="/admin/members", tags=["admin-members"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/")
def members_page(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin_user),
):
    members = db.query(models.MemberUser).order_by(models.MemberUser.created_at.desc()).all()
    return templates.TemplateResponse(
        "admin_members.html",
        {"request": request, "members": members},
    )


@router.post("/set-admin")
def set_admin(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(get_current_admin_user),
    discord_id: str = Form(...),
    make_admin: str = Form("1"),
):
    target = db.query(models.MemberUser).filter(models.MemberUser.discord_id == discord_id).first()
    if target:
        target.is_admin = (make_admin == "1")
        db.commit()
    return RedirectResponse(url="/admin/members/", status_code=303)
