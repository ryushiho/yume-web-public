"""promote_member_admin.py

사용법:
    cd /opt/yume-web
    source venv/bin/activate
    python scripts/promote_member_admin.py 1433962010785349634

지정한 discord_id의 MemberUser를 관리자(is_admin=True)로 승급한다.
"""

from __future__ import annotations

import sys

from app.database import SessionLocal
from app import models

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/promote_member_admin.py <discord_id>")
        return 2
    discord_id = sys.argv[1].strip()
    db = SessionLocal()
    try:
        m = db.query(models.MemberUser).filter(models.MemberUser.discord_id == discord_id).first()
        if not m:
            print(f"[!] Member not found: {discord_id}")
            return 1
        m.is_admin = True
        db.commit()
        print(f"[*] OK: promoted {discord_id} ({m.nickname}) to admin")
        return 0
    finally:
        db.close()

if __name__ == "__main__":
    raise SystemExit(main())
