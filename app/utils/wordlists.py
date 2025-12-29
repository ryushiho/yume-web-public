"""Wordlist UI labels.

키(list_name)는 DB/URL에서 쓰는 내부 식별자이고,
값은 웹 UI에 노출할 한국어 표시명이다.

NOTE: 파일명(ALLOWED_LISTS)은 /api/bluewar/wordlists 쪽에서 관리한다.
"""

from __future__ import annotations


WORDLIST_LABELS = {
    "suggestion": "시작 단어",
    "blue_archive_words": "루트전 단어",
    "public_words": "전체 단어 목록",
}
