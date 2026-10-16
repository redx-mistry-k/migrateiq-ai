import traceback
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors import extract_records, load_records
from .connectors.loaders import normalize_record
from .models import (
    AuditLog,
    Connection,
    JobRecord,
    JobStatus,
    MappingConfig,
    MigrationJob,
    Project,
)
from .transformations import apply_mapping, apply_transformations
from .validation import validate_record


def now():
    return datetime.now(UTC)


def add_log(session: Session, job_id: str | None, level: str, message: str, **context) -> None:
    session.add(AuditLog(job_id=job_id, level=level, message=message, context=context))


def claim_next_job(session: Session) -> MigrationJob | None:
    statement = (
        select(MigrationJob)
        .where(MigrationJob.status == JobStatus.queued)
        .order_by(MigrationJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = session.scalar(statement)
    if job:
        job.status = JobStatus.running
        job.started_at = job.started_at or now()
        job.error_message = None
        add_log(session, job.id, "INFO", "Worker claimed migration job")
        session.commit()
    return job


def recover_interrupted_jobs(session: Session) -> int:
    """Requeue jobs left running when the single worker process terminated."""
    jobs = list(
        session.scalars(select(MigrationJob).where(MigrationJob.status == JobStatus.running))
    )
    for job in jobs:
        job.status = JobStatus.queued
        add_log(
            session,
            job.id,
            "WARNING",
            "Recovered interrupted job from its last committed checkpoint",
            checkpoint_offset=job.checkpoint_offset,
        )
    session.commit()
    return len(jobs)


def prepare_job_frame(session: Session, job: MigrationJob) -> pd.DataFrame:
    project = session.get(Project, job.project_id)
    if not project or not project.source_connection_id or not project.destination_connection_id:
        raise ValueError("Project must have source and destination connections")
    mapping_config = session.scalar(
        select(MappingConfig).where(MappingConfig.project_id == project.id)
    )
    if not mapping_config or not mapping_config.mappings:
        raise ValueError("Project must have an approved mapping configuration")
    source = session.get(Connection, project.source_connection_id)
    if not source:
        raise ValueError("Source connection not found")
    raw = extract_records(source)
    job.source_count = len(raw)
    mapped = apply_mapping(raw, mapping_config.mappings)
    transformed = apply_transformations(mapped, mapping_config.transformations)
    dropped = len(raw) - len(transformed)
    if dropped > 0 and job.checkpoint_offset == 0:
        job.skipped_count += dropped
        add_log(session, job.id, "INFO", "Duplicate transformation skipped records", count=dropped)
    return transformed.reset_index(drop=True)


def run_job(session: Session, job_id: str) -> None:
    job = session.get(MigrationJob, job_id)
    if not job:
        raise ValueError("Job not found")
    if job.status not in {JobStatus.running, JobStatus.queued}:
        return
    job.status = JobStatus.running
    job.started_at = job.started_at or now()
    session.commit()
    try:
        frame = prepare_job_frame(session, job)
        project = session.get(Project, job.project_id)
        destination = session.get(Connection, project.destination_connection_id)
        mapping_config = session.scalar(
            select(MappingConfig).where(MappingConfig.project_id == project.id)
        )
        session.commit()
        start = job.checkpoint_offset
        total = len(frame)
        for offset in range(start, total, job.batch_size):
            end = min(offset + job.batch_size, total)
            batch_frame = frame.iloc[offset:end]
            valid_records: list[dict] = []
            valid_positions: list[int] = []
            for relative, raw in enumerate(batch_frame.to_dict(orient="records")):
                source_index = offset + relative
                record = normalize_record(raw)
                errors = validate_record(record, mapping_config.validation_rules)
                if errors:
                    session.add(
                        JobRecord(
                            job_id=job.id,
                            source_index=source_index,
                            business_key=str(record.get(project.business_key) or "") or None,
                            outcome="rejected",
                            data=record,
                            errors=errors,
                        )
                    )
                    job.rejected_count += 1
                else:
                    valid_records.append(record)
                    valid_positions.append(source_index)
            results = load_records(session, project, destination, valid_records)
            for source_index, record, result in zip(
                valid_positions, valid_records, results, strict=True
            ):
                outcome = result["outcome"]
                session.add(
                    JobRecord(
                        job_id=job.id,
                        source_index=source_index,
                        business_key=result.get("business_key"),
                        outcome=outcome,
                        data=record,
                        errors=[result["error"]] if result.get("error") else [],
                        checksum=result.get("checksum"),
                    )
                )
                if outcome == "inserted":
                    job.inserted_count += 1
                elif outcome == "updated":
                    job.updated_count += 1
                elif outcome == "skipped":
                    job.skipped_count += 1
                else:
                    job.rejected_count += 1
            job.processed_count = end
            job.checkpoint_offset = end
            job.current_batch += 1
            add_log(session, job.id, "INFO", "Batch committed", start=offset, end=end)
            session.commit()
        # Deduplication can make the transformed frame shorter than the source.
        # At completion every source row has still been accounted for.
        job.processed_count = job.source_count
        job.status = JobStatus.completed
        job.finished_at = now()
        add_log(session, job.id, "INFO", "Migration completed")
        session.commit()
    except Exception as exc:
        session.rollback()
        job = session.get(MigrationJob, job_id)
        job.status = JobStatus.failed
        job.error_message = str(exc)[:2000]
        job.finished_at = now()
        add_log(
            session,
            job.id,
            "ERROR",
            "Migration failed",
            error=str(exc)[:1000],
            error_type=type(exc).__name__,
            traceback=traceback.format_exc(limit=3),
        )
        session.commit()
