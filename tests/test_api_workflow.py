import json

import respx
from httpx import Response

from migrateiq.jobs import run_job
from migrateiq.models import (
    Connection,
    ConnectionKind,
    ConnectionType,
    JobStatus,
    MappingConfig,
    MigrationJob,
    Project,
)
from migrateiq.reconciliation import reconcile_job


@respx.mock
def test_api_to_api_workflow_with_pagination_and_reconciliation(session, monkeypatch):
    for module in (
        "migrateiq.connectors.extractors.validate_external_url",
        "migrateiq.connectors.loaders.validate_external_url",
        "migrateiq.reconciliation.validate_external_url",
    ):
        monkeypatch.setattr(module, lambda value: value)

    legacy = [
        {
            "cust_id": f"LEG-{index}",
            "cust_name": name,
            "contact_number": phone,
            "email_id": f"customer{index}@example.com",
            "signup_date": "2024-01-02",
        }
        for index, name, phone in [
            (1, " Alice ", "555-1001"),
            (2, "Bob", "555-1002"),
            (3, "Carol", "555-1003"),
        ]
    ]
    modern = {}

    def source_callback(request):
        page = int(request.url.params.get("page", "1"))
        return Response(200, json={"items": legacy[(page - 1) * 2 : page * 2]})

    def create_callback(request):
        payload = json.loads(request.content)
        key = payload["customer_id"]
        if key in modern:
            return Response(409, json={"detail": "exists"})
        modern[key] = payload
        return Response(201, json=payload)

    def update_callback(request):
        payload = json.loads(request.content)
        modern[payload["customer_id"]] = payload
        return Response(200, json=payload)

    respx.get("https://legacy.example.com/customers").mock(side_effect=source_callback)
    respx.post("https://modern.example.com/customers").mock(side_effect=create_callback)
    respx.put(url__regex=r"https://modern\.example\.com/customers/.+").mock(
        side_effect=update_callback
    )
    respx.get("https://modern.example.com/customers").mock(
        side_effect=lambda _: Response(200, json={"items": list(modern.values())})
    )

    source = Connection(
        name="Legacy CRM",
        kind=ConnectionKind.source,
        type=ConnectionType.rest_api,
        config={
            "url": "https://legacy.example.com/customers",
            "items_path": "items",
            "pagination": "page",
            "page_size": 2,
        },
    )
    destination = Connection(
        name="Modern CRM",
        kind=ConnectionKind.destination,
        type=ConnectionType.rest_api,
        config={
            "url": "https://modern.example.com/customers",
            "list_url": "https://modern.example.com/customers",
            "items_path": "items",
            "upsert": True,
        },
    )
    session.add_all([source, destination])
    session.flush()
    project = Project(
        name="API workflow",
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
                {"source": "contact_number", "destination": "phone"},
                {"source": "email_id", "destination": "email"},
                {"source": "signup_date", "destination": "created_at"},
            ],
            transformations=[
                {"operation": "trim", "column": "full_name"},
                {"operation": "normalize_phone", "column": "phone"},
                {"operation": "normalize_date", "column": "created_at"},
            ],
            validation_rules=[
                {"column": "customer_id", "rule": "required"},
                {"column": "email", "rule": "email"},
            ],
        )
    )
    job = MigrationJob(project_id=project.id, batch_size=2)
    session.add(job)
    session.commit()

    run_job(session, job.id)
    session.refresh(job)
    assert job.status == JobStatus.completed
    assert job.inserted_count == 3
    assert modern["LEG-1"]["full_name"] == "Alice"
    assert modern["LEG-1"]["phone"] == "+5551001"
    result = reconcile_job(session, job.id)
    assert result.status == "matched"
    assert result.summary["verified_records"] == 3

    repeat = MigrationJob(project_id=project.id, batch_size=3)
    session.add(repeat)
    session.commit()
    run_job(session, repeat.id)
    session.refresh(repeat)
    assert repeat.updated_count == 3
    assert len(modern) == 3
