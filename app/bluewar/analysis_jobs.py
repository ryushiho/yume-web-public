from __future__ import annotations

import traceback
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app import models
from app.database import SessionLocal
from .analysis_engine import prepare_input, rebuild_analysis


JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_DONE = "DONE"
JOB_ERROR = "ERROR"


def enqueue_rebuild_job(
    db: Session,
    *,
    list_name: str,
    pack_version: Optional[str],
) -> models.BlueWarAnalysisJob:
    """Create (or reuse) a background rebuild job and start it.

    This function is intentionally synchronous and fast.
    The heavy computation runs in a daemon thread.
    """

    meta, _words = prepare_input(db, list_name=list_name, pack_version=pack_version)

    # If an in-flight job exists for the same analysis_key, reuse it.
    existing = (
        db.query(models.BlueWarAnalysisJob)
        .filter(models.BlueWarAnalysisJob.analysis_key == meta.analysis_key)
        .filter(models.BlueWarAnalysisJob.status.in_([JOB_PENDING, JOB_RUNNING]))
        .order_by(models.BlueWarAnalysisJob.id.desc())
        .first()
    )
    if existing:
        # If server restarted mid-job, status may remain RUNNING forever.
        # Treat very old RUNNING jobs as stale and allow re-queue.
        try:
            if (
                existing.status == JOB_RUNNING
                and existing.started_at
                and (datetime.utcnow() - existing.started_at).total_seconds() > 6 * 3600
            ):
                existing.status = JOB_ERROR
                existing.finished_at = datetime.utcnow()
                existing.message = "stale"
                db.commit()
            else:
                return existing
        except Exception:
            db.rollback()
            return existing

    # If DB already has the computed rows for this analysis_key, we don't need to spawn a thread.
    # We still record a job row for auditability in the admin UI.
    try:
        m = (
            db.query(models.BlueWarAnalysisMeta)
            .filter(models.BlueWarAnalysisMeta.analysis_key == meta.analysis_key)
            .first()
        )
        if m and (m.words_sha256 == meta.words_sha256) and (m.dooum_sha256 == meta.dooum_sha256):
            wcnt = (
                db.query(models.BlueWarWordStat)
                .filter(models.BlueWarWordStat.analysis_key == meta.analysis_key)
                .count()
            )
            scnt = (
                db.query(models.BlueWarSyllableStat)
                .filter(models.BlueWarSyllableStat.analysis_key == meta.analysis_key)
                .count()
            )
            if int(wcnt) == int(meta.word_count) and int(scnt) > 0:
                now = datetime.utcnow()
                job = models.BlueWarAnalysisJob(
                    analysis_key=meta.analysis_key,
                    list_name=meta.list_name,
                    pack_version=meta.pack_version,
                    status=JOB_DONE,
                    progress_current=1,
                    progress_total=1,
                    message="cache hit (db)",
                    created_at=now,
                    started_at=now,
                    finished_at=now,
                )
                db.add(job)
                db.commit()
                db.refresh(job)
                return job
    except Exception:
        db.rollback()

    job = models.BlueWarAnalysisJob(
        analysis_key=meta.analysis_key,
        list_name=meta.list_name,
        pack_version=meta.pack_version,
        status=JOB_PENDING,
        progress_current=0,
        progress_total=5,
        message="queued",
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    t = threading.Thread(
        target=_run_rebuild_job,
        args=(job.id, list_name, pack_version),
        daemon=True,
    )
    t.start()

    return job


def _run_rebuild_job(job_id: int, list_name: str, pack_version: Optional[str]) -> None:
    # Use two separate DB sessions:
    # - analysis_db: long-running analysis/insert transaction
    # - job_db: small status/progress updates (committed independently)
    analysis_db = SessionLocal()
    job_db = SessionLocal()
    try:
        job = job_db.query(models.BlueWarAnalysisJob).filter(models.BlueWarAnalysisJob.id == int(job_id)).first()
        if not job:
            return

        job.status = JOB_RUNNING
        job.started_at = datetime.utcnow()
        job.message = "running"
        job_db.commit()

        def _progress(cur: int, total: int, msg: str) -> None:
            try:
                j = job_db.query(models.BlueWarAnalysisJob).filter(models.BlueWarAnalysisJob.id == int(job_id)).first()
                if not j:
                    return
                j.progress_current = int(cur)
                j.progress_total = int(total)
                j.message = (msg or "")[:500]
                job_db.commit()
            except Exception:
                # Best-effort progress updates; never crash the job.
                job_db.rollback()

        rebuild_analysis(
            analysis_db,
            list_name=list_name,
            pack_version=pack_version,
            progress_cb=_progress,
        )

        job = job_db.query(models.BlueWarAnalysisJob).filter(models.BlueWarAnalysisJob.id == int(job_id)).first()
        if not job:
            return
        job.status = JOB_DONE
        job.finished_at = datetime.utcnow()
        job.progress_current = int(job.progress_total or 5)
        job.message = "done"
        job_db.commit()

    except Exception:
        tb = traceback.format_exc()
        try:
            job = job_db.query(models.BlueWarAnalysisJob).filter(models.BlueWarAnalysisJob.id == int(job_id)).first()
            if job:
                job.status = JOB_ERROR
                job.finished_at = datetime.utcnow()
                job.error_text = tb[-8000:]
                job.message = "error"
                job_db.commit()
        except Exception:
            job_db.rollback()
    finally:
        analysis_db.close()
        job_db.close()
