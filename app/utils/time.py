"""시간/타임존 유틸.

서버(DB)에는 기본적으로 UTC 기준 datetime(대부분 tzinfo 없는 naive)이 들어온다.
웹 UI에는 한국(Asia/Seoul) 기준으로 보기 좋게 표시한다.

NOTE:
  - dt.tzinfo 가 없으면 UTC로 간주한다.
  - dt.tzinfo 가 있으면 그 값을 존중한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


def to_kst(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)


def fmt_kst(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> str:
    k = to_kst(dt)
    if k is None:
        return "-"
    return k.strftime(fmt)
