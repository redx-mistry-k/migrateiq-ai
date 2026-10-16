"""Run destructive-to-test-data-only acceptance checks against the local Docker stack."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import psycopg

API = "http://localhost:8000"
CRM = "http://localhost:8001"
DB_URL = "postgresql://migrateiq:migrateiq@localhost:55432/migrateiq"
ROOT = Path(__file__).resolve().parents[1]


class AcceptanceRunner:
    def __init__(self):
        self.client = httpx.Client(base_url=API, timeout=120)
        self.results: dict[str, Any] = {}

    def request(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(
                f"{method} {path} failed ({response.status_code}): {response.text[:1000]}"
            )
        return response.json() if response.content else None

    def connection(self, name: str, kind: str, connection_type: str, config: dict):
        return self.request(
            "POST",
            "/api/connections",
            json={"name": name, "kind": kind, "type": connection_type, "config": config},
        )

    def upload(self, path: Path):
        with path.open("rb") as stream:
            return self.request(
                "POST",
                f"/api/sources/upload?name={path.stem}",
                files={"file": (path.name, stream, "text/csv")},
            )

    def project(self, name: str, source_id: str, destination_id: str):
        return self.request(
            "POST",
            "/api/projects",
            json={
                "name": name,
                "description": "Live acceptance test using synthetic data only",
                "source_connection_id": source_id,
                "destination_connection_id": destination_id,
                "business_key": "customer_id",
            },
        )

    def save_customer_mapping(self, project_id: str, api_source: bool = False):
        source_names = (
            ["cust_id", "cust_name", "contact_number", "email_id", "signup_date"]
            if api_source
            else ["cust_id", "cust_name", "mobile_no", "email_id", "join_date"]
        )
        destination_names = ["customer_id", "full_name", "phone", "email", "created_at"]
        mappings = [
            {"source": source, "destination": destination, "included": True}
            for source, destination in zip(source_names, destination_names, strict=True)
        ]
        transformations = [
            {"operation": "trim", "column": "full_name", "params": {}},
            {"operation": "normalize_phone", "column": "phone", "params": {}},
            {"operation": "normalize_date", "column": "created_at", "params": {}},
            {
                "operation": "deduplicate",
                "column": "customer_id",
                "params": {"keep": "first"},
            },
        ]
        validations = [
            {"column": "customer_id", "rule": "required", "value": None},
            {"column": "full_name", "rule": "required", "value": None},
            {"column": "email", "rule": "email", "value": None},
            {"column": "created_at", "rule": "required", "value": None},
        ]
        return self.request(
            "PUT",
            f"/api/projects/{project_id}/mapping",
            json={
                "mappings": mappings,
                "transformations": transformations,
                "validation_rules": validations,
            },
        )

    def run_job(self, project_id: str, batch_size: int, timeout: float = 300):
        started = time.perf_counter()
        job = self.request(
            "POST", "/api/jobs", json={"project_id": project_id, "batch_size": batch_size}
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.request("GET", f"/api/jobs/{job['id']}")
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError(f"Job {job['id']} did not finish within {timeout} seconds")
        job["wall_seconds"] = round(time.perf_counter() - started, 3)
        if job["status"] != "completed":
            raise RuntimeError(f"Job {job['id']} failed: {job['error_message']}")
        return job

    def reconcile(self, job_id: str):
        return self.request("POST", f"/api/jobs/{job_id}/reconcile")

    def database_count(self, project_id: str) -> int:
        with psycopg.connect(DB_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM destination_records WHERE project_id = %s", (project_id,)
            )
            return cursor.fetchone()[0]

    def file_workflow(self):
        stamp = str(int(time.time()))
        source = self.upload(ROOT / "demo_data" / "sample_customers.csv")
        preview = self.request("GET", f"/api/sources/{source['id']}/preview?limit=20")
        destination = self.connection(
            f"Live PostgreSQL {stamp}",
            "destination",
            "postgresql",
            {"database_url": "postgresql+psycopg://migrateiq:migrateiq@postgres:5432/migrateiq"},
        )
        project = self.project(f"Live CSV acceptance {stamp}", source["id"], destination["id"])
        self.save_customer_mapping(project["id"])
        transform_preview = self.request(
            "POST", "/api/transformations/preview", json={"project_id": project["id"], "limit": 20}
        )
        validation_preview = self.request(
            "POST", "/api/validations/preview", json={"project_id": project["id"], "limit": 20}
        )
        job = self.run_job(project["id"], 2)
        reconciliation = self.reconcile(job["id"])
        self.results["csv_to_postgresql"] = {
            "project_id": project["id"],
            "job": job,
            "source_preview_rows": preview["row_count"],
            "transformed_preview_rows": len(transform_preview["after"]),
            "validation_preview": {
                "valid": validation_preview["valid"],
                "invalid": validation_preview["invalid"],
            },
            "database_rows": self.database_count(project["id"]),
            "reconciliation": reconciliation,
        }

    def api_workflow(self):
        stamp = str(int(time.time()))
        httpx.delete(
            f"{CRM}/modern/customers", headers={"X-API-Key": "demo-secret"}, timeout=30
        ).raise_for_status()
        source = self.connection(
            f"Legacy CRM {stamp}",
            "source",
            "rest_api",
            {
                "url": "http://demo-crm:8001/legacy/customers",
                "headers": {"X-API-Key": "demo-secret"},
                "items_path": "items",
                "pagination": "page",
                "page_size": 100,
            },
        )
        destination = self.connection(
            f"Modern CRM {stamp}",
            "destination",
            "rest_api",
            {
                "url": "http://demo-crm:8001/modern/customers",
                "list_url": "http://demo-crm:8001/modern/customers",
                "headers": {"X-API-Key": "demo-secret"},
                "items_path": "items",
                "upsert": True,
            },
        )
        project = self.project(f"Live API acceptance {stamp}", source["id"], destination["id"])
        self.save_customer_mapping(project["id"], api_source=True)
        job = self.run_job(project["id"], 50)
        reconciliation = self.reconcile(job["id"])
        modern = httpx.get(
            f"{CRM}/modern/customers?page_size=1000",
            headers={"X-API-Key": "demo-secret"},
            timeout=30,
        )
        modern.raise_for_status()
        self.results["rest_to_rest"] = {
            "project_id": project["id"],
            "job": job,
            "modern_crm_rows": modern.json()["total"],
            "reconciliation": reconciliation,
        }

    def large_workflow(self):
        stamp = str(int(time.time()))
        source = self.upload(ROOT / "demo_data" / "customers_10000.csv")
        destination = self.connection(
            f"Large PostgreSQL {stamp}",
            "destination",
            "postgresql",
            {"database_url": "postgresql+psycopg://migrateiq:migrateiq@postgres:5432/migrateiq"},
        )
        project = self.project(f"Live 10K acceptance {stamp}", source["id"], destination["id"])
        self.save_customer_mapping(project["id"])
        first = self.run_job(project["id"], 500, timeout=600)
        first_reconciliation = self.reconcile(first["id"])
        first_count = self.database_count(project["id"])
        second = self.run_job(project["id"], 500, timeout=600)
        second_reconciliation = self.reconcile(second["id"])
        second_count = self.database_count(project["id"])
        self.results["large_10000"] = {
            "project_id": project["id"],
            "first_job": first,
            "first_database_rows": first_count,
            "first_reconciliation": first_reconciliation,
            "repeat_job": second,
            "repeat_database_rows": second_count,
            "repeat_reconciliation": second_reconciliation,
            "duplicate_rows_created": second_count - first_count,
        }
        self.checkpoint_recovery(project["id"])
        self.mismatch_detection(project["id"], second["id"])

    def checkpoint_recovery(self, project_id: str):
        job = self.request("POST", "/api/jobs", json={"project_id": project_id, "batch_size": 50})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            running = self.request("GET", f"/api/jobs/{job['id']}")
            if running["status"] == "running" and running["checkpoint_offset"] >= 50:
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("Recovery test job never reached a committed checkpoint")
        subprocess.run(["docker", "compose", "stop", "worker"], cwd=ROOT, check=True)
        interrupted = self.request("GET", f"/api/jobs/{job['id']}")
        if interrupted["status"] != "running" or interrupted["checkpoint_offset"] < 50:
            raise AssertionError(f"Unexpected interrupted state: {interrupted}")
        subprocess.run(["docker", "compose", "up", "-d", "worker"], cwd=ROOT, check=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            resumed = self.request("GET", f"/api/jobs/{job['id']}")
            if resumed["status"] in {"completed", "failed"}:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("Recovered job did not finish")
        if resumed["status"] != "completed":
            raise RuntimeError(f"Recovered job failed: {resumed['error_message']}")
        logs = self.request("GET", f"/api/jobs/{job['id']}/logs")
        self.results["checkpoint_recovery"] = {
            "job_id": job["id"],
            "interrupted_checkpoint": interrupted["checkpoint_offset"],
            "final_checkpoint": resumed["checkpoint_offset"],
            "final_status": resumed["status"],
            "recovery_log_present": any(
                "Recovered interrupted job" in row["message"] for row in logs
            ),
        }

    def mismatch_detection(self, project_id: str, job_id: str):
        with psycopg.connect(DB_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, data, checksum FROM destination_records "
                "WHERE project_id=%s ORDER BY business_key LIMIT 1",
                (project_id,),
            )
            record_id, original_data, original_checksum = cursor.fetchone()
            altered = dict(original_data)
            altered["full_name"] = "INTENTIONAL QA MISMATCH"
            cursor.execute(
                "UPDATE destination_records SET data=%s::json, checksum=%s WHERE id=%s",
                (json.dumps(altered), "intentional-mismatch", record_id),
            )
            connection.commit()
        mismatch = self.reconcile(job_id)
        with psycopg.connect(DB_URL) as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE destination_records SET data=%s::json, checksum=%s WHERE id=%s",
                (json.dumps(original_data), original_checksum, record_id),
            )
            connection.commit()
        self.results["intentional_mismatch"] = {
            "status": mismatch["status"],
            "summary": mismatch["summary"],
            "field_mismatch_detail_present": any(
                row["type"] == "field_mismatch" for row in mismatch["details"]
            ),
            "restored_after_test": True,
        }

    def gemini_mapping(self):
        response = self.request(
            "POST",
            "/api/mappings/suggest",
            json={
                "source_columns": ["cust_id", "cust_name", "mobile_no", "join_date"],
                "destination_columns": ["customer_id", "full_name", "phone", "created_at"],
                "use_ai": True,
            },
        )
        self.results["gemini_mapping"] = {
            "provider": response["provider"],
            "suggestion_count": len(response["mappings"]),
            "fallback_warning_present": bool(response.get("warning")),
        }

    def run(self):
        self.file_workflow()
        self.api_workflow()
        self.large_workflow()
        self.gemini_mapping()
        print(json.dumps(self.results, indent=2, default=str))


if __name__ == "__main__":
    AcceptanceRunner().run()
