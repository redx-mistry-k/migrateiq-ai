import csv
import io
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, JobRecord, MigrationJob, Project, ReconciliationResult


def job_report(session: Session, job_id: str) -> dict:
    job = session.get(MigrationJob, job_id)
    if not job:
        raise ValueError("Job not found")
    project = session.get(Project, job.project_id)
    reconciliation = session.scalar(
        select(ReconciliationResult)
        .where(ReconciliationResult.job_id == job.id)
        .order_by(ReconciliationResult.created_at.desc())
    )
    records = list(
        session.scalars(
            select(JobRecord).where(JobRecord.job_id == job.id).order_by(JobRecord.source_index)
        )
    )
    logs = list(
        session.scalars(
            select(AuditLog).where(AuditLog.job_id == job.id).order_by(AuditLog.created_at)
        )
    )
    return {
        "project": {"id": project.id, "name": project.name},
        "job": {
            "id": job.id,
            "status": job.status.value,
            "source_count": job.source_count,
            "processed": job.processed_count,
            "inserted": job.inserted_count,
            "updated": job.updated_count,
            "skipped": job.skipped_count,
            "rejected": job.rejected_count,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        },
        "reconciliation": (
            {
                "status": reconciliation.status,
                "summary": reconciliation.summary,
                "details": reconciliation.details,
            }
            if reconciliation
            else None
        ),
        "records": [
            {
                "source_index": row.source_index,
                "business_key": row.business_key,
                "outcome": row.outcome,
                "errors": row.errors,
                "data": row.data,
            }
            for row in records
        ],
        "logs": [
            {
                "timestamp": row.created_at.isoformat(),
                "level": row.level,
                "message": row.message,
                "context": row.context,
            }
            for row in logs
        ],
    }


def report_json(session: Session, job_id: str) -> bytes:
    return json.dumps(job_report(session, job_id), indent=2, default=str).encode()


def rejected_csv(session: Session, job_id: str) -> bytes:
    rows = list(
        session.scalars(
            select(JobRecord).where(JobRecord.job_id == job_id, JobRecord.outcome == "rejected")
        )
    )
    buffer = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row.data})
    writer = csv.DictWriter(buffer, fieldnames=["source_index", "errors", *fieldnames])
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {"source_index": row.source_index, "errors": "; ".join(row.errors), **row.data}
        )
    return buffer.getvalue().encode("utf-8-sig")
