import hashlib
import json
from datetime import date, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Connection, ConnectionType, DestinationRecord, Project, utcnow
from ..security import validate_external_url


class LoadingError(RuntimeError):
    pass


def normalized_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    try:
        if hasattr(value, "item"):
            return value.item()
    except (ValueError, TypeError):
        pass
    return value


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    return {str(k): normalized_value(v) for k, v in record.items()}


def record_checksum(record: dict[str, Any]) -> str:
    serialized = json.dumps(
        normalize_record(record), sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _load_database(session: Session, project: Project, records: list[dict]) -> list[dict]:
    results = []
    for raw in records:
        record = normalize_record(raw)
        key = record.get(project.business_key)
        if key is None or str(key).strip() == "":
            results.append(
                {"outcome": "rejected", "error": f"Missing business key {project.business_key}"}
            )
            continue
        key = str(key)
        checksum = record_checksum(record)
        existing = session.scalar(
            select(DestinationRecord).where(
                DestinationRecord.project_id == project.id,
                DestinationRecord.business_key == key,
            )
        )
        if existing and existing.checksum == checksum:
            results.append({"outcome": "skipped", "business_key": key, "checksum": checksum})
        elif existing:
            existing.data = record
            existing.checksum = checksum
            existing.updated_at = utcnow()
            results.append({"outcome": "updated", "business_key": key, "checksum": checksum})
        else:
            session.add(
                DestinationRecord(
                    project_id=project.id, business_key=key, data=record, checksum=checksum
                )
            )
            results.append({"outcome": "inserted", "business_key": key, "checksum": checksum})
    session.flush()
    return results


def _load_rest(connection: Connection, project: Project, records: list[dict]) -> list[dict]:
    url = validate_external_url(connection.config.get("url", ""))
    headers = connection.config.get("headers", {})
    results = []
    with httpx.Client(timeout=30) as client:
        for raw in records:
            record = normalize_record(raw)
            key = record.get(project.business_key)
            if key is None or str(key).strip() == "":
                results.append(
                    {"outcome": "rejected", "error": f"Missing business key {project.business_key}"}
                )
                continue
            try:
                response = client.post(url, headers=headers, json=record)
                if response.status_code == 409 and connection.config.get("upsert", True):
                    update_url = f"{url.rstrip('/')}/{key}"
                    response = client.put(update_url, headers=headers, json=record)
                    response.raise_for_status()
                    outcome = "updated"
                else:
                    response.raise_for_status()
                    outcome = "inserted"
                results.append(
                    {
                        "outcome": outcome,
                        "business_key": str(key),
                        "checksum": record_checksum(record),
                    }
                )
            except httpx.HTTPError as exc:
                body = (
                    getattr(exc.response, "text", "")[:500]
                    if getattr(exc, "response", None)
                    else ""
                )
                results.append(
                    {"outcome": "rejected", "business_key": str(key), "error": f"{exc}: {body}"}
                )
    return results


def load_records(
    session: Session, project: Project, connection: Connection, records: list[dict]
) -> list[dict]:
    if connection.type == ConnectionType.postgresql:
        return _load_database(session, project, records)
    if connection.type == ConnectionType.rest_api:
        return _load_rest(connection, project, records)
    raise LoadingError("Destinations must be PostgreSQL or REST API connections")
