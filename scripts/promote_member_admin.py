"""promote_member_admin.py

사용법:
    cd /opt/yume-web
    source venv/bin/activate
    python scripts/promote_member_admin.py <회원아이디>

지정한 '회원 로그인 아이디'(member register/login에서 쓰는 아이디)의 MemberUser를
관리자(is_admin=True)로 승급한다.
"""

from __future__ import annotations

import sys

from app.database import SessionLocal
from app import models

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/promote_member_admin.py <login_id>")
        return 2
    login_id = sys.argv[1].strip()
    db = SessionLocal()
    try:
        m = db.query(models.MemberUser).filter(models.MemberUser.discord_id == login_id).first()
        if not m:
            print(f"[!] Member not found: {login_id}")
            return 1
        m.is_admin = True
        db.commit()
        print(f"[*] OK: promoted {login_id} ({m.nickname}) to admin")
        return 0
    finally:
        db.close()

if __name__ == "__main__":
    raise SystemExit(main())
