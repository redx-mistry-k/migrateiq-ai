from sqlalchemy import func, select

from migrateiq.jobs import recover_interrupted_jobs, run_job
from migrateiq.models import (
    Connection,
    ConnectionKind,
    ConnectionType,
    DestinationRecord,
    JobRecord,
    JobStatus,
    MappingConfig,
    MigrationJob,
    Project,
)
from migrateiq.reconciliation import reconcile_job
from migrateiq.reporting import job_report, rejected_csv


def setup_workflow(session, tmp_path):
    path = tmp_path / "customers.csv"
    path.write_text(
        "cust_id,cust_name,email_id,join_date\n"
        "1,  Alice Example  ,alice@example.test,01/02/2024\n"
        "2,Bob Example,not-an-email,2024-02-03\n"
        "3,Carol Example,carol@example.test,bad-date\n",
        encoding="utf-8",
    )
    source = Connection(
        name="CSV",
        kind=ConnectionKind.source,
        type=ConnectionType.csv,
        config={"path": str(path)},
    )
    destination = Connection(
        name="PostgreSQL",
        kind=ConnectionKind.destination,
        type=ConnectionType.postgresql,
        config={},
    )
    session.add_all([source, destination])
    session.flush()
    project = Project(
        name="File workflow",
        source_connection_id=source.id,
        destination_connection_id=destination.id,
        business_key="customer_id",
    )
    session.add(project)
    session.flush()
    session.add(
        MappingConfig(
            project_id=project.id,
            mappings=[
                {"source": "cust_id", "destination": "customer_id"},
                {"source": "cust_name", "destination": "full_name"},
                {"source": "email_id", "destination": "email"},
                {"source": "join_date", "destination": "created_at"},
            ],
            transformations=[
                {"operation": "trim", "column": "full_name"},
                {"operation": "normalize_date", "column": "created_at"},
            ],
            validation_rules=[
                {"column": "customer_id", "rule": "required"},
                {"column": "email", "rule": "email"},
                {"column": "created_at", "rule": "required"},
            ],
        )
    )
    job = MigrationJob(project_id=project.id, batch_size=2, status=JobStatus.queued)
    session.add(job)
    session.commit()
    return project, job


def test_csv_to_database_reconciliation_and_idempotent_retry(session, tmp_path):
    project, job = setup_workflow(session, tmp_path)
    run_job(session, job.id)
    session.refresh(job)
    assert job.status == JobStatus.completed
    assert job.source_count == 3
    assert job.processed_count == 3
    assert job.inserted_count == 1
    assert job.rejected_count == 2
    assert session.scalar(select(func.count()).select_from(DestinationRecord)) == 1
    rejected = list(
        session.scalars(
            select(JobRecord).where(JobRecord.job_id == job.id, JobRecord.outcome == "rejected")
        )
    )
    assert {error for row in rejected for error in row.errors} == {
        "email is not a valid email address",
        "created_at is required",
    }
    reconciliation = reconcile_job(session, job.id)
    assert reconciliation.status == "matched"
    assert reconciliation.summary["verified_records"] == 1
    assert job_report(session, job.id)["job"]["inserted"] == 1
    assert b"not-an-email" in rejected_csv(session, job.id)

    second = MigrationJob(project_id=project.id, batch_size=10, status=JobStatus.queued)
    session.add(second)
    session.commit()
    run_job(session, second.id)
    session.refresh(second)
    assert second.skipped_count == 1
    assert second.inserted_count == 0
    assert session.scalar(select(func.count()).select_from(DestinationRecord)) == 1


def test_worker_restart_requeues_running_job_at_checkpoint(session, tmp_path):
    _, job = setup_workflow(session, tmp_path)
    job.status = JobStatus.running
    job.checkpoint_offset = 2
    job.processed_count = 2
    session.commit()

    assert recover_interrupted_jobs(session) == 1
    session.refresh(job)
    assert job.status == JobStatus.queued
    assert job.checkpoint_offset == 2
    assert job.processed_count == 2
