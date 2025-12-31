"""Guard for optional `python-multipart`.

If `python-multipart` is missing, endpoints using FastAPI `File(...)`/`Form(...)`
may raise at route registration time and crash startup.
"""

from __future__ import annotations


def has_multipart() -> bool:
    try:
        import multipart  # type: ignore
    except Exception:
        return False
    return True


HAS_MULTIPART: bool = has_multipart()
