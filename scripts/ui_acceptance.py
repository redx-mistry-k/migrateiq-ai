"""Headless acceptance checks for the live Streamlit application.

Run with API_BASE_URL pointed at the live backend.  This uses Streamlit's
official AppTest harness, so it executes widget callbacks and reruns without
requiring a browser driver.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "frontend" / "app.py"
API = "http://localhost:8000"
PROJECT_NAME = os.getenv("MIGRATEIQ_UI_PROJECT")
EXPECTED_MAPPING = {
    "cust_id": "customer_id",
    "cust_name": "full_name",
    "mobile_no": "phone",
    "email_id": "email",
    "join_date": "created_at",
}


def errors(app: AppTest) -> list[str]:
    return [str(item.value) for item in app.exception]


def by_label(elements, label: str):
    return next(item for item in elements if item.label == label)


def open_page(app: AppTest, page: str) -> AppTest:
    app.sidebar.radio[0].set_value(page)
    app.run(timeout=60)
    assert not errors(app), (page, errors(app))
    return app


def choose_project(app: AppTest, key: str) -> dict:
    widget = app.selectbox(key=key)
    if PROJECT_NAME:
        label = next(option for option in widget.options if option.startswith(f"{PROJECT_NAME} ·"))
    else:
        label = next(
            option for option in widget.options if option.startswith("Live 10K acceptance ")
        )
    widget.set_value(label)
    app.run(timeout=60)
    assert not errors(app), errors(app)
    projects = httpx.get(f"{API}/api/projects", timeout=60).json()
    project_prefix = label.rsplit(" · ", 1)[1]
    return next(item for item in projects if item["id"].startswith(project_prefix))


def main() -> None:
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    page_results: dict[str, dict[str, object]] = {}
    for page in [
        "Dashboard",
        "Projects",
        "Data Sources",
        "Schema Mapping",
        "Transform & Validate",
        "Execution",
        "Reports & Reconciliation",
    ]:
        open_page(app, page)
        page_results[page] = {
            "exceptions": errors(app),
            "buttons": [button.label for button in app.button],
            "selectboxes": [selectbox.label for selectbox in app.selectbox],
        }

    open_page(app, "Schema Mapping")
    project = choose_project(app, "mapping_project")
    by_label(app.checkbox, "Use Gemini when configured").set_value(False)
    by_label(app.button, "Generate suggestions").click()
    app.run(timeout=60)
    assert not errors(app), errors(app)
    suggested_rows = app.session_state[f"mapping-{project['id']}"]
    mapping = {row["source"]: row["destination"] for row in suggested_rows}
    assert {key: mapping[key] for key in EXPECTED_MAPPING} == EXPECTED_MAPPING, mapping
    assert mapping["legacy_code"] is None and mapping["unexpected_notes"] is None, mapping
    by_label(app.button, "Approve and save mapping").click()
    app.run(timeout=60)
    assert any("Approved configuration saved" in item.value for item in app.success)

    open_page(app, "Transform & Validate")
    choose_project(app, "transform_project")
    by_label(app.button, "Suggest cleaning rules").click()
    app.run(timeout=60)
    assert not errors(app), errors(app)
    by_label(app.button, "Preview before and after").click()
    app.run(timeout=60)
    assert not errors(app), errors(app)
    assert len(app.dataframe) >= 2
    by_label(app.button, "Run validation preview").click()
    app.run(timeout=60)
    assert not errors(app), errors(app)
    metric_values = {metric.label: metric.value for metric in app.metric}
    assert int(metric_values["Valid"]) > 0, metric_values

    open_page(app, "Execution")
    choose_project(app, "execution_project")
    by_label(app.number_input, "Batch size").set_value(1000)
    by_label(app.button, "Start migration").click()
    app.run(timeout=60)
    assert not errors(app), errors(app)
    success_message = next(item.value for item in app.success if "queued" in item.value)
    short_id = success_message.split()[1]
    jobs = httpx.get(f"{API}/api/jobs", params={"project_id": project["id"]}, timeout=60).json()
    job = next(item for item in jobs if item["id"].startswith(short_id))
    deadline = time.monotonic() + 120
    while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.5)
        job = httpx.get(f"{API}/api/jobs/{job['id']}", timeout=60).json()
    assert job["status"] == "completed", job
    assert job["inserted_count"] + job["updated_count"] + job["skipped_count"] > 0, job
    by_label(app.button, "Refresh status").click()
    app.run(timeout=60)
    assert not errors(app), errors(app)

    open_page(app, "Reports & Reconciliation")
    job_widget = by_label(app.selectbox, "Migration job")
    selected_label = next(
        option for option in job_widget.options if option.startswith(job["id"][:8])
    )
    job_widget.set_value(selected_label)
    app.run(timeout=60)
    by_label(app.button, "Run reconciliation").click()
    app.run(timeout=60)
    assert not errors(app), errors(app)
    reconciliation_message = next(
        item.value for item in app.success if item.value.startswith("Reconciliation:")
    )
    assert len(app.get("download_button")) == 2

    print(
        json.dumps(
            {
                "pages": page_results,
                "deterministic_mapping": mapping,
                "validation_metrics": metric_values,
                "ui_job": {
                    key: job[key]
                    for key in [
                        "id",
                        "status",
                        "processed_count",
                        "inserted_count",
                        "updated_count",
                        "skipped_count",
                        "rejected_count",
                    ]
                },
                "reconciliation": reconciliation_message,
                "download_buttons": 2,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
