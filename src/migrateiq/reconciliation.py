from collections import Counter
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors.loaders import normalize_record, record_checksum
from .models import (
    Connection,
    ConnectionType,
    DestinationRecord,
    JobRecord,
    MigrationJob,
    Project,
    ReconciliationResult,
)
from .security import validate_external_url


def _destination_records(session: Session, project: Project, connection: Connection) -> list[dict]:
    if connection.type == ConnectionType.postgresql:
        return list(
            session.scalars(
                select(DestinationRecord.data).where(DestinationRecord.project_id == project.id)
            )
        )
    if connection.type == ConnectionType.rest_api:
        list_url = validate_external_url(
            connection.config.get("list_url") or connection.config.get("url", "")
        )
        response = httpx.get(list_url, headers=connection.config.get("headers", {}), timeout=30)
        response.raise_for_status()
        payload: Any = response.json()
        for part in connection.config.get("items_path", "items").split("."):
            payload = payload[part]
        if not isinstance(payload, list):
            raise ValueError("Destination list endpoint did not return a list")
        return [normalize_record(item) for item in payload]
    raise ValueError("Unsupported destination type")


def reconcile_job(session: Session, job_id: str) -> ReconciliationResult:
    job = session.get(MigrationJob, job_id)
    if not job:
        raise ValueError("Job not found")
    project = session.get(Project, job.project_id)
    destination = session.get(Connection, project.destination_connection_id)
    expected_rows = list(
        session.scalars(
            select(JobRecord).where(
                JobRecord.job_id == job.id,
                JobRecord.outcome.in_(["inserted", "updated", "skipped"]),
            )
        )
    )
    actual_rows = _destination_records(session, project, destination)
    expected = {
        str(row.business_key): normalize_record(row.data)
        for row in expected_rows
        if row.business_key
    }
    actual_keys = [
        str(row.get(project.business_key))
        for row in actual_rows
        if row.get(project.business_key) is not None
    ]
    actual = {
        str(row.get(project.business_key)): normalize_record(row)
        for row in actual_rows
        if row.get(project.business_key) is not None
    }
    duplicates = sorted(key for key, count in Counter(actual_keys).items() if count > 1)
    missing = sorted(set(expected) - set(actual))
    mismatches = []
    for key in sorted(set(expected) & set(actual)):
        if record_checksum(expected[key]) == record_checksum(actual[key]):
            continue
        fields = []
        for field in sorted(set(expected[key]) | set(actual[key])):
            if expected[key].get(field) != actual[key].get(field):
                fields.append(
                    {
                        "field": field,
                        "expected": expected[key].get(field),
                        "actual": actual[key].get(field),
                    }
                )
        mismatches.append({"business_key": key, "fields": fields})
    verified = len(expected) - len(missing) - len(mismatches)
    status = "matched" if not missing and not mismatches and not duplicates else "mismatched"
    summary = {
        "source_records": job.source_count,
        "processed_records": job.processed_count,
        "inserted": job.inserted_count,
        "updated": job.updated_count,
        "skipped": job.skipped_count,
        "rejected": job.rejected_count,
        "expected_destination_records": len(expected),
        "actual_destination_records": len(actual_rows),
        "verified_records": verified,
        "missing_records": len(missing),
        "duplicate_destination_keys": len(duplicates),
        "field_mismatches": len(mismatches),
    }
    details = [
        *({"type": "missing", "business_key": key} for key in missing),
        *({"type": "duplicate", "business_key": key} for key in duplicates),
        *({"type": "field_mismatch", **item} for item in mismatches),
    ]
    result = ReconciliationResult(job_id=job.id, status=status, summary=summary, details=details)
    session.add(result)
    session.commit()
    session.refresh(result)
    return result
