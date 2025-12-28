"""import_wordlists_from_txt.py

Phase 1 용 임시 이식 스크립트.

사용법(서버):
    cd /opt/yume-web
    source venv/bin/activate
    python scripts/import_wordlists_from_txt.py \
        --suggestion /path/to/suggestion.txt \
        --blue-archive /path/to/blue_archive_words.txt

동작:
    - bluewar_words 테이블에 단어를 저장한다.
    - 각 리스트는 '전체 덮어쓰기' 방식(기존 삭제 후 insert)으로 처리한다.

주의:
    - Phase 2에서 웹 업로드 기능이 들어가면 이 스크립트는 거의 필요 없어진다.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List

from app.database import SessionLocal
from app import models


def _read_words(p: Path) -> List[str]:
    text = p.read_text(encoding="utf-8", errors="ignore")
    out: List[str] = []
    seen = set()
    for raw in text.splitlines():
        w = raw.strip()
        if not w:
            continue
        if w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _replace_list(db, list_name: str, words: Iterable[str]) -> int:
    db.query(models.BlueWarWord).filter(models.BlueWarWord.list_name == list_name).delete()
    db.commit()
    n = 0
    for w in words:
        db.add(models.BlueWarWord(list_name=list_name, word=w))
        n += 1
    db.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suggestion", required=True, help="suggestion.txt 경로")
    ap.add_argument("--blue-archive", required=True, help="blue_archive_words.txt 경로")
    args = ap.parse_args()

    sug_path = Path(args.suggestion)
    ba_path = Path(args.blue_archive)
    if not sug_path.exists():
        print(f"[!] not found: {sug_path}")
        return 2
    if not ba_path.exists():
        print(f"[!] not found: {ba_path}")
        return 2

    sug_words = _read_words(sug_path)
    ba_words = _read_words(ba_path)

    db = SessionLocal()
    try:
        n1 = _replace_list(db, "suggestion", sug_words)
        n2 = _replace_list(db, "blue_archive_words", ba_words)
        print(f"[*] OK: suggestion={n1}, blue_archive_words={n2}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
