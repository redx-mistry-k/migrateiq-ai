import json
import os
from typing import Any

import httpx
import pandas as pd
import plotly.express as px
import streamlit as st

API = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
st.set_page_config(page_title="MigrateIQ AI", page_icon="⇄", layout="wide")
st.markdown(
    """
<style>
  [data-testid="stSidebar"] {background: #111827;}
  [data-testid="stSidebar"] * {color: #f3f4f6;}
  .block-container {padding-top: 1.5rem; max-width: 1500px;}
  div[data-testid="stMetric"] {
    background:#f8fafc; border:1px solid #e2e8f0; padding:1rem; border-radius:12px;
  }
  .status {
    display:inline-block;padding:.25rem .65rem;border-radius:999px;
    background:#e0f2fe;color:#075985;font-weight:600;
  }
  h1,h2,h3 {letter-spacing:-0.025em;}
</style>
""",
    unsafe_allow_html=True,
)


def api(method: str, path: str, **kwargs) -> Any:
    try:
        response = httpx.request(method, f"{API}{path}", timeout=60, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(str(detail))
        if response.status_code == 204:
            return None
        return response.json()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Backend unavailable: {exc}") from exc


def safe_api(method: str, path: str, **kwargs):
    try:
        return api(method, path, **kwargs)
    except RuntimeError as exc:
        st.error(str(exc))
        return None


def choose_project(key: str = "project"):
    projects = safe_api("GET", "/api/projects") or []
    if not projects:
        st.info("Create a migration project first.")
        return None
    labels = {f"{item['name']} · {item['id'][:8]}": item for item in projects}
    return labels[st.selectbox("Project", labels, key=key)]


def page_dashboard():
    st.title("Migration command center")
    data = safe_api("GET", "/api/dashboard")
    if not data:
        return
    if data["total_projects"] == 0:
        st.info("Welcome to MigrateIQ. Upload a source and create your first project to begin.")
    cols = st.columns(6)
    metrics = [
        ("Projects", "total_projects"),
        ("Completed", "completed_migrations"),
        ("Failed", "failed_migrations"),
        ("Processed", "records_processed"),
        ("Succeeded", "records_succeeded"),
        ("Rejected", "records_rejected"),
    ]
    for col, (label, key) in zip(cols, metrics, strict=True):
        col.metric(label, f"{data[key]:,}")
    activity = data["recent_activity"]
    if activity:
        st.subheader("Recent activity")
        frame = pd.DataFrame(activity)
        st.dataframe(
            frame[
                [
                    "id",
                    "status",
                    "processed_count",
                    "inserted_count",
                    "rejected_count",
                    "created_at",
                ]
            ],
            width="stretch",
        )
        chart = frame.groupby("status", as_index=False)["processed_count"].sum()
        st.plotly_chart(
            px.bar(
                chart,
                x="status",
                y="processed_count",
                color="status",
                title="Actual records processed by status",
            ),
            width="stretch",
        )


def page_sources():
    st.title("Data sources & destinations")
    upload_tab, connect_tab, list_tab = st.tabs(
        ["Upload file", "Add connection", "Saved connections"]
    )
    with upload_tab:
        uploaded = st.file_uploader("CSV, Excel, JSON, or XML", type=["csv", "xlsx", "json", "xml"])
        name = st.text_input("Connection name", key="upload_name")
        if st.button("Upload and inspect", disabled=uploaded is None):
            result = safe_api(
                "POST",
                f"/api/sources/upload?name={name or uploaded.name}",
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
            )
            if result:
                st.success("Source uploaded")
                preview = safe_api("GET", f"/api/sources/{result['id']}/preview")
                if preview:
                    st.metric("Rows", preview["row_count"])
                    st.dataframe(preview["records"], width="stretch")
                    for issue in preview["issues"]:
                        st.warning(issue)
    with connect_tab:
        kind = st.selectbox("Role", ["source", "destination"])
        supported = (
            ["postgresql", "rest_api"] if kind == "destination" else ["postgresql", "rest_api"]
        )
        connection_type = st.selectbox("Type", supported)
        connection_name = st.text_input("Name", key="connection_name")
        if connection_type == "postgresql":
            database_url = st.text_input("PostgreSQL URL", type="password")
            query = st.text_area("Read-only SELECT query", disabled=kind == "destination")
            config = {"database_url": database_url, "query": query}
        else:
            url = st.text_input("Endpoint URL")
            list_url = st.text_input(
                "List URL (destination reconciliation)", disabled=kind == "source"
            )
            headers = st.text_area("Headers (JSON)", value='{"X-API-Key":"demo-secret"}')
            pagination = st.selectbox(
                "Pagination",
                ["none", "page", "offset", "cursor", "next_link"],
                disabled=kind == "destination",
            )
            config = {
                "url": url,
                "headers": json.loads(headers or "{}"),
                "items_path": "items",
                "pagination": pagination,
                "page_size": 100,
            }
            if list_url:
                config["list_url"] = list_url
        if st.button("Save connection"):
            result = safe_api(
                "POST",
                "/api/connections",
                json={
                    "name": connection_name,
                    "kind": kind,
                    "type": connection_type,
                    "config": config,
                },
            )
            if result:
                st.success("Connection saved")
    with list_tab:
        connections = safe_api("GET", "/api/connections") or []
        st.dataframe(connections, width="stretch")
        if connections:
            selected = st.selectbox(
                "Test connection", connections, format_func=lambda x: f"{x['name']} ({x['type']})"
            )
            if st.button("Run connection test"):
                result = safe_api("POST", f"/api/connections/{selected['id']}/test")
                if result:
                    st.success(result["message"])


def page_projects():
    st.title("Migration projects")
    connections = safe_api("GET", "/api/connections") or []
    sources = [item for item in connections if item["kind"] == "source"]
    destinations = [item for item in connections if item["kind"] == "destination"]
    with (
        st.expander("Create project", expanded=not bool(safe_api("GET", "/api/projects") or [])),
        st.form("create_project"),
    ):
        name = st.text_input("Project name")
        description = st.text_area("Description")
        source = st.selectbox(
            "Source", sources, format_func=lambda x: x["name"] if x else "", index=None
        )
        destination = st.selectbox(
            "Destination",
            destinations,
            format_func=lambda x: x["name"] if x else "",
            index=None,
        )
        business_key = st.text_input("Destination business key", value="customer_id")
        if st.form_submit_button("Create project"):
            result = safe_api(
                "POST",
                "/api/projects",
                json={
                    "name": name,
                    "description": description,
                    "source_connection_id": source["id"] if source else None,
                    "destination_connection_id": destination["id"] if destination else None,
                    "business_key": business_key,
                },
            )
            if result:
                st.success("Project created")
                st.rerun()
    projects = safe_api("GET", "/api/projects") or []
    for project in projects:
        with st.expander(f"{project['name']} · {project['id'][:8]}"):
            st.write(project["description"] or "No description")
            st.caption(f"Business key: {project['business_key']}")
            confirm = st.checkbox("Confirm deletion", key=f"delete-confirm-{project['id']}")
            if st.button("Delete project", key=f"delete-{project['id']}", disabled=not confirm):
                safe_api("DELETE", f"/api/projects/{project['id']}")
                st.rerun()


def page_mapping():
    st.title("Schema mapping")
    project = choose_project("mapping_project")
    if not project or not project.get("source_connection_id"):
        return
    preview = safe_api("GET", f"/api/sources/{project['source_connection_id']}/preview?limit=10")
    if not preview:
        return
    source_columns = [item["name"] for item in preview["columns"]]
    destination_text = st.text_input(
        "Destination columns", value="customer_id, full_name, phone, email, created_at"
    )
    destination_columns = [item.strip() for item in destination_text.split(",") if item.strip()]
    use_ai = st.checkbox("Use Gemini when configured", value=True)
    state_key = f"mapping-{project['id']}"
    if st.button("Generate suggestions"):
        result = safe_api(
            "POST",
            "/api/mappings/suggest",
            json={
                "source_columns": source_columns,
                "destination_columns": destination_columns,
                "use_ai": use_ai,
            },
        )
        if result:
            st.session_state[state_key] = result["mappings"]
            st.info(f"Suggestions from {result['provider']}")
            if result.get("warning"):
                st.warning(result["warning"])
    if state_key in st.session_state:
        edited = st.data_editor(
            pd.DataFrame(st.session_state[state_key]),
            column_config={
                "destination": st.column_config.SelectboxColumn(options=destination_columns),
                "included": st.column_config.CheckboxColumn(),
            },
            disabled=["source", "confidence", "explanation"],
            width="stretch",
            num_rows="fixed",
        )
        transformations = st.text_area(
            "Transform rules (JSON)",
            value='[{"operation":"trim","column":"full_name"}]',
            height=120,
        )
        validations = st.text_area(
            "Validation rules (JSON)",
            value='[{"column":"customer_id","rule":"required"},{"column":"email","rule":"email"}]',
            height=120,
        )
        if st.button("Approve and save mapping"):
            result = safe_api(
                "PUT",
                f"/api/projects/{project['id']}/mapping",
                json={
                    "mappings": edited.to_dict(orient="records"),
                    "transformations": json.loads(transformations or "[]"),
                    "validation_rules": json.loads(validations or "[]"),
                },
            )
            if result:
                st.success("Approved configuration saved")
    st.subheader("Source sample")
    st.dataframe(preview["records"], width="stretch")


def page_transform():
    st.title("Transform & validate")
    project = choose_project("transform_project")
    if not project:
        return
    config = safe_api("GET", f"/api/projects/{project['id']}/mapping")
    if not config:
        return
    rules_text = st.text_area(
        "Safe transformation rules", json.dumps(config["transformations"], indent=2), height=220
    )
    if st.button("Suggest cleaning rules"):
        suggestions = safe_api(
            "GET", f"/api/projects/{project['id']}/cleaning-suggestions?use_ai=true"
        )
        if suggestions:
            st.info(f"Suggestions from {suggestions['provider']}")
            if suggestions.get("warning"):
                st.warning(suggestions["warning"])
            st.dataframe(suggestions["suggestions"], width="stretch")
    if st.button("Preview before and after"):
        result = safe_api(
            "POST",
            "/api/transformations/preview",
            json={
                "project_id": project["id"],
                "limit": 25,
                "transformations": json.loads(rules_text),
            },
        )
        if result:
            left, right = st.columns(2)
            left.subheader("Before")
            left.dataframe(result["before"], width="stretch")
            right.subheader("After")
            right.dataframe(result["after"], width="stretch")
    if st.button("Run validation preview"):
        result = safe_api(
            "POST", "/api/validations/preview", json={"project_id": project["id"], "limit": 100}
        )
        if result:
            a, b = st.columns(2)
            a.metric("Valid", result["valid"])
            b.metric("Invalid", result["invalid"])
            st.dataframe(result["results"], width="stretch")
            errors = [error for row in result["results"] for error in row["errors"]]
            explanation = safe_api(
                "POST", "/api/ai/explain-validation?use_ai=true", json={"errors": errors}
            )
            if explanation and errors:
                st.info(explanation["summary"])
                for action in explanation["recommended_actions"]:
                    st.write(f"- {action}")


def page_execution():
    st.title("Migration execution")
    project = choose_project("execution_project")
    if not project:
        return
    batch_size = st.number_input("Batch size", 1, 10000, 500)
    if st.button("Start migration", type="primary"):
        result = safe_api(
            "POST", "/api/jobs", json={"project_id": project["id"], "batch_size": batch_size}
        )
        if result:
            st.success(f"Job {result['id'][:8]} queued. The worker will process it.")
    jobs = safe_api("GET", f"/api/jobs?project_id={project['id']}") or []
    for job in jobs:
        progress = 0 if not job["source_count"] else job["processed_count"] / job["source_count"]
        with st.expander(
            f"{job['status'].upper()} · {job['id'][:8]}",
            expanded=job["status"] in ["queued", "running", "failed"],
        ):
            st.progress(
                min(progress, 1.0),
                text=f"{job['processed_count']:,} / {job['source_count']:,} records",
            )
            cols = st.columns(5)
            for col, label, field in zip(
                cols,
                ["Inserted", "Updated", "Skipped", "Rejected", "Batch"],
                [
                    "inserted_count",
                    "updated_count",
                    "skipped_count",
                    "rejected_count",
                    "current_batch",
                ],
                strict=True,
            ):
                col.metric(label, job[field])
            if job.get("error_message"):
                st.error(job["error_message"])
            if job["status"] in ["failed", "paused"] and st.button(
                "Retry from checkpoint", key=f"retry-{job['id']}"
            ):
                safe_api("POST", f"/api/jobs/{job['id']}/retry")
                st.rerun()
    if st.button("Refresh status"):
        st.rerun()


def page_reports():
    st.title("Validation, reconciliation & reports")
    jobs = safe_api("GET", "/api/jobs") or []
    if not jobs:
        st.info("Run a migration to generate reports.")
        return
    job = st.selectbox(
        "Migration job",
        jobs,
        format_func=lambda x: f"{x['id'][:8]} · {x['status']} · {x['processed_count']} records",
    )
    if job["status"] == "completed" and st.button("Run reconciliation"):
        result = safe_api("POST", f"/api/jobs/{job['id']}/reconcile")
        if result:
            st.success(f"Reconciliation: {result['status']}")
    reconciliation = (
        safe_api("GET", f"/api/jobs/{job['id']}/reconciliation")
        if job["status"] == "completed"
        else None
    )
    if reconciliation:
        cols = st.columns(4)
        for col, key in zip(
            cols,
            [
                "verified_records",
                "missing_records",
                "field_mismatches",
                "duplicate_destination_keys",
            ],
            strict=True,
        ):
            col.metric(key.replace("_", " ").title(), reconciliation["summary"].get(key, 0))
        if reconciliation["details"]:
            st.dataframe(reconciliation["details"], width="stretch")
    records = safe_api("GET", f"/api/jobs/{job['id']}/records?limit=1000") or []
    if records:
        outcomes = (
            pd.DataFrame(records)["outcome"]
            .value_counts()
            .rename_axis("outcome")
            .reset_index(name="records")
        )
        st.plotly_chart(
            px.pie(outcomes, names="outcome", values="records", title="Recorded outcomes"),
            width="stretch",
        )
        st.dataframe(records, width="stretch")
    report = httpx.get(f"{API}/api/jobs/{job['id']}/report.json", timeout=60)
    rejected = httpx.get(f"{API}/api/jobs/{job['id']}/rejected.csv", timeout=60)
    left, right = st.columns(2)
    left.download_button(
        "Download complete JSON report",
        report.content,
        f"migration-{job['id']}.json",
        "application/json",
    )
    right.download_button(
        "Download rejected CSV", rejected.content, f"rejected-{job['id']}.csv", "text/csv"
    )
    logs = safe_api("GET", f"/api/jobs/{job['id']}/logs") or []
    with st.expander("Audit log"):
        st.dataframe(logs, width="stretch")


st.sidebar.title("⇄ MigrateIQ AI")
st.sidebar.caption("Intelligent migration, measurable integrity")
page = st.sidebar.radio(
    "Navigate",
    [
        "Dashboard",
        "Projects",
        "Data Sources",
        "Schema Mapping",
        "Transform & Validate",
        "Execution",
        "Reports & Reconciliation",
    ],
)
pages = {
    "Dashboard": page_dashboard,
    "Projects": page_projects,
    "Data Sources": page_sources,
    "Schema Mapping": page_mapping,
    "Transform & Validate": page_transform,
    "Execution": page_execution,
    "Reports & Reconciliation": page_reports,
}
try:
    pages[page]()
except (ValueError, json.JSONDecodeError) as exc:
    st.error(f"Please check the entered configuration: {exc}")
