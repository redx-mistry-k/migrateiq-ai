import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from .ai_assistance import cleaning_suggestions, explain_validation
from .config import get_settings
from .connectors import inspect_source
from .db import get_db, init_db
from .mapping import suggest_mapping
from .models import (
    AuditLog,
    Connection,
    ConnectionKind,
    ConnectionType,
    JobRecord,
    JobStatus,
    MappingConfig,
    MigrationJob,
    Project,
    ReconciliationResult,
    utcnow,
)
from .reconciliation import reconcile_job
from .reporting import rejected_csv, report_json
from .schemas import (
    ConnectionCreate,
    ConnectionRead,
    JobCreate,
    JobRead,
    MappingConfigRead,
    MappingConfigWrite,
    MappingSuggestionRequest,
    PreviewRequest,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    ValidationExplainRequest,
)
from .security import redact_config
from .transformations import apply_mapping, apply_transformations
from .validation import validate_record


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title="MigrateIQ AI API",
    version="0.1.0",
    description="Auditable ETL, migration, and reconciliation API",
    lifespan=lifespan,
)


def require(model, session: Session, object_id: str, label: str):
    value = session.get(model, object_id)
    if not value:
        raise HTTPException(404, f"{label} not found")
    return value


@app.get("/health")
def health(session: Session = Depends(get_db)):
    session.execute(select(1))
    return {"status": "healthy"}


@app.get("/api/dashboard")
def dashboard(session: Session = Depends(get_db)):
    jobs = list(session.scalars(select(MigrationJob).order_by(MigrationJob.created_at.desc())))
    return {
        "total_projects": session.scalar(select(func.count()).select_from(Project)) or 0,
        "completed_migrations": sum(job.status == JobStatus.completed for job in jobs),
        "failed_migrations": sum(job.status == JobStatus.failed for job in jobs),
        "records_processed": sum(job.processed_count for job in jobs),
        "records_succeeded": sum(
            job.inserted_count + job.updated_count + job.skipped_count for job in jobs
        ),
        "records_rejected": sum(job.rejected_count for job in jobs),
        "recent_activity": [
            JobRead.model_validate(job).model_dump(mode="json") for job in jobs[:10]
        ],
    }


@app.post("/api/connections", response_model=ConnectionRead, status_code=201)
def create_connection(payload: ConnectionCreate, session: Session = Depends(get_db)):
    connection = Connection(**payload.model_dump())
    session.add(connection)
    session.commit()
    session.refresh(connection)
    result = ConnectionRead.model_validate(connection).model_dump()
    result["config"] = redact_config(result["config"])
    return result


@app.get("/api/connections", response_model=list[ConnectionRead])
def list_connections(kind: ConnectionKind | None = None, session: Session = Depends(get_db)):
    statement = select(Connection).order_by(Connection.created_at.desc())
    if kind:
        statement = statement.where(Connection.kind == kind)
    results = []
    for connection in session.scalars(statement):
        item = ConnectionRead.model_validate(connection).model_dump()
        item["config"] = redact_config(item["config"])
        results.append(item)
    return results


@app.post("/api/connections/{connection_id}/test")
def test_connection(connection_id: str, session: Session = Depends(get_db)):
    connection = require(Connection, session, connection_id, "Connection")
    try:
        if connection.kind == ConnectionKind.source:
            preview = inspect_source(connection, limit=3)
            return {
                "ok": True,
                "message": f"Read {preview['row_count']} records",
                "preview": preview,
            }
        if connection.type == ConnectionType.postgresql:
            database_url = connection.config.get("database_url")
            if not database_url:
                raise ValueError("PostgreSQL destination requires database_url")
            destination_engine = create_engine(database_url, pool_pre_ping=True)
            with destination_engine.connect() as target:
                target.execute(text("SELECT 1"))
            return {"ok": True, "message": "PostgreSQL destination connection succeeded"}
        if connection.type != ConnectionType.rest_api:
            raise ValueError("Unsupported destination type")
        return {
            "ok": True,
            "message": (
                "REST destination configuration is valid; writes are tested during migration"
            ),
        }
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/sources/upload", response_model=ConnectionRead, status_code=201)
def upload_source(
    file: UploadFile = File(...),
    name: str | None = None,
    session: Session = Depends(get_db),
):
    suffix = Path(file.filename or "").suffix.lower()
    type_map = {".csv": "csv", ".xlsx": "excel", ".json": "json", ".xml": "xml"}
    if suffix not in type_map:
        raise HTTPException(415, "Only CSV, XLSX, JSON, and XML files are supported")
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    destination = (settings.upload_dir / f"{uuid.uuid4()}{suffix}").resolve()
    with destination.open("wb") as target:
        shutil.copyfileobj(file.file, target, length=1024 * 1024)
    if destination.stat().st_size > settings.max_upload_bytes:
        destination.unlink(missing_ok=True)
        raise HTTPException(413, f"Upload exceeds the {settings.max_upload_mb} MB limit")
    connection = Connection(
        name=name or file.filename or "Uploaded source",
        kind=ConnectionKind.source,
        type=ConnectionType(type_map[suffix]),
        config={"path": str(destination)},
    )
    session.add(connection)
    session.commit()
    session.refresh(connection)
    return connection


@app.get("/api/sources/{connection_id}/preview")
def preview_source(
    connection_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_db),
):
    connection = require(Connection, session, connection_id, "Connection")
    if connection.kind != ConnectionKind.source:
        raise HTTPException(422, "Connection is not a source")
    try:
        return inspect_source(connection, limit)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/projects", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, session: Session = Depends(get_db)):
    project = Project(**payload.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@app.get("/api/projects", response_model=list[ProjectRead])
def list_projects(session: Session = Depends(get_db)):
    return list(session.scalars(select(Project).order_by(Project.updated_at.desc())))


@app.get("/api/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, session: Session = Depends(get_db)):
    return require(Project, session, project_id, "Project")


@app.patch("/api/projects/{project_id}", response_model=ProjectRead)
def update_project(project_id: str, payload: ProjectUpdate, session: Session = Depends(get_db)):
    project = require(Project, session, project_id, "Project")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    project.updated_at = utcnow()
    session.commit()
    session.refresh(project)
    return project


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str, session: Session = Depends(get_db)):
    project = require(Project, session, project_id, "Project")
    session.delete(project)
    session.commit()
    return Response(status_code=204)


@app.post("/api/mappings/suggest")
def suggest(payload: MappingSuggestionRequest):
    mappings, provider, warning = suggest_mapping(
        payload.source_columns, payload.destination_columns, payload.use_ai
    )
    return {
        "provider": provider,
        "warning": warning,
        "mappings": [item.model_dump() for item in mappings],
    }


@app.put("/api/projects/{project_id}/mapping", response_model=MappingConfigRead)
def save_mapping(project_id: str, payload: MappingConfigWrite, session: Session = Depends(get_db)):
    require(Project, session, project_id, "Project")
    config = session.scalar(select(MappingConfig).where(MappingConfig.project_id == project_id))
    values = payload.model_dump(mode="json")
    if config:
        config.mappings = values["mappings"]
        config.transformations = values["transformations"]
        config.validation_rules = values["validation_rules"]
        config.updated_at = utcnow()
    else:
        config = MappingConfig(project_id=project_id, **values)
        session.add(config)
    session.commit()
    session.refresh(config)
    return config


@app.get("/api/projects/{project_id}/mapping", response_model=MappingConfigRead)
def get_mapping(project_id: str, session: Session = Depends(get_db)):
    config = session.scalar(select(MappingConfig).where(MappingConfig.project_id == project_id))
    if not config:
        raise HTTPException(404, "Mapping configuration not found")
    return config


def _project_preview(session: Session, request: PreviewRequest):
    project = require(Project, session, request.project_id, "Project")
    source = require(Connection, session, project.source_connection_id, "Source connection")
    config = session.scalar(select(MappingConfig).where(MappingConfig.project_id == project.id))
    if not config:
        raise HTTPException(422, "Save a mapping configuration first")
    from .connectors import extract_records

    raw = extract_records(source).head(request.limit)
    before = raw.astype(object).where(pd.notna(raw), None).to_dict(orient="records")
    mapped = apply_mapping(raw, config.mappings)
    rules = (
        request.transformations if request.transformations is not None else config.transformations
    )
    after_frame = apply_transformations(mapped, rules)
    after = after_frame.astype(object).where(pd.notna(after_frame), None).to_dict(orient="records")
    return project, config, before, after


@app.post("/api/transformations/preview")
def preview_transformations(request: PreviewRequest, session: Session = Depends(get_db)):
    try:
        _, _, before, after = _project_preview(session, request)
        return {"before": before, "after": after}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/validations/preview")
def preview_validation(request: PreviewRequest, session: Session = Depends(get_db)):
    try:
        _, config, _, after = _project_preview(session, request)
        results = [
            {"record": row, "errors": validate_record(row, config.validation_rules)}
            for row in after
        ]
        return {
            "valid": sum(not row["errors"] for row in results),
            "invalid": sum(bool(row["errors"]) for row in results),
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/projects/{project_id}/cleaning-suggestions")
def get_cleaning_suggestions(
    project_id: str, use_ai: bool = True, session: Session = Depends(get_db)
):
    project = require(Project, session, project_id, "Project")
    source = require(Connection, session, project.source_connection_id, "Source connection")
    try:
        metadata = inspect_source(source, limit=1)
        suggestions, provider, warning = cleaning_suggestions(metadata["columns"], use_ai)
        return {"provider": provider, "warning": warning, "suggestions": suggestions}
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/ai/explain-validation")
def explain_validation_failures(payload: ValidationExplainRequest, use_ai: bool = True):
    result, provider, warning = explain_validation(payload.errors, use_ai)
    return {"provider": provider, "warning": warning, **result.model_dump()}


@app.post("/api/jobs", response_model=JobRead, status_code=202)
def start_job(payload: JobCreate, session: Session = Depends(get_db)):
    require(Project, session, payload.project_id, "Project")
    active = session.scalar(
        select(MigrationJob).where(
            MigrationJob.project_id == payload.project_id,
            MigrationJob.status.in_([JobStatus.queued, JobStatus.running]),
        )
    )
    if active:
        raise HTTPException(409, f"Project already has active job {active.id}")
    job = MigrationJob(**payload.model_dump())
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


@app.get("/api/jobs", response_model=list[JobRead])
def list_jobs(project_id: str | None = None, session: Session = Depends(get_db)):
    statement = select(MigrationJob).order_by(MigrationJob.created_at.desc())
    if project_id:
        statement = statement.where(MigrationJob.project_id == project_id)
    return list(session.scalars(statement))


@app.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, session: Session = Depends(get_db)):
    return require(MigrationJob, session, job_id, "Job")


@app.post("/api/jobs/{job_id}/retry", response_model=JobRead, status_code=202)
def retry_job(job_id: str, session: Session = Depends(get_db)):
    job = require(MigrationJob, session, job_id, "Job")
    if job.status not in {JobStatus.failed, JobStatus.paused}:
        raise HTTPException(409, "Only failed or paused jobs can be retried")
    job.status = JobStatus.queued
    job.error_message = None
    job.finished_at = None
    session.add(
        AuditLog(
            job_id=job.id,
            level="INFO",
            message="Job queued for resume",
            context={"offset": job.checkpoint_offset},
        )
    )
    session.commit()
    session.refresh(job)
    return job


@app.get("/api/jobs/{job_id}/records")
def job_records(
    job_id: str,
    outcome: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    session: Session = Depends(get_db),
):
    require(MigrationJob, session, job_id, "Job")
    statement = (
        select(JobRecord)
        .where(JobRecord.job_id == job_id)
        .order_by(JobRecord.source_index)
        .limit(limit)
    )
    if outcome:
        statement = statement.where(JobRecord.outcome == outcome)
    return [
        {
            "source_index": item.source_index,
            "business_key": item.business_key,
            "outcome": item.outcome,
            "errors": item.errors,
            "data": item.data,
        }
        for item in session.scalars(statement)
    ]


@app.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str, level: str | None = None, session: Session = Depends(get_db)):
    statement = select(AuditLog).where(AuditLog.job_id == job_id).order_by(AuditLog.created_at)
    if level:
        statement = statement.where(AuditLog.level == level.upper())
    return [
        {
            "timestamp": item.created_at,
            "level": item.level,
            "message": item.message,
            "context": item.context,
        }
        for item in session.scalars(statement)
    ]


@app.post("/api/jobs/{job_id}/reconcile")
def reconcile(job_id: str, session: Session = Depends(get_db)):
    job = require(MigrationJob, session, job_id, "Job")
    if job.status != JobStatus.completed:
        raise HTTPException(409, "Only completed jobs can be reconciled")
    try:
        result = reconcile_job(session, job_id)
        return {
            "id": result.id,
            "status": result.status,
            "summary": result.summary,
            "details": result.details,
        }
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/jobs/{job_id}/reconciliation")
def latest_reconciliation(job_id: str, session: Session = Depends(get_db)):
    result = session.scalar(
        select(ReconciliationResult)
        .where(ReconciliationResult.job_id == job_id)
        .order_by(ReconciliationResult.created_at.desc())
    )
    if not result:
        raise HTTPException(404, "No reconciliation has been run")
    return {
        "id": result.id,
        "status": result.status,
        "summary": result.summary,
        "details": result.details,
    }


@app.get("/api/jobs/{job_id}/report.json")
def download_report(job_id: str, session: Session = Depends(get_db)):
    require(MigrationJob, session, job_id, "Job")
    return Response(
        report_json(session, job_id),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="migration-{job_id}.json"'},
    )


@app.get("/api/jobs/{job_id}/rejected.csv")
def download_rejected(job_id: str, session: Session = Depends(get_db)):
    require(MigrationJob, session, job_id, "Job")
    return Response(
        rejected_csv(session, job_id),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="rejected-{job_id}.csv"'},
    )
