import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from defusedxml import ElementTree
from sqlalchemy import create_engine, text

from ..models import Connection, ConnectionType
from ..security import validate_external_url


class ExtractionError(RuntimeError):
    pass


def _read_xml(path: Path) -> pd.DataFrame:
    try:
        root = ElementTree.parse(path).getroot()
    except Exception as exc:
        raise ExtractionError(f"Invalid or unsafe XML: {exc}") from exc
    rows = []
    for element in list(root):
        record = {child.tag: child.text for child in list(element)}
        if record:
            rows.append(record)
    return pd.DataFrame(rows)


def _file_frame(connection: Connection) -> pd.DataFrame:
    raw_path = connection.config.get("path")
    if not raw_path:
        raise ExtractionError("File connection has no path")
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise ExtractionError(f"Source file does not exist: {path.name}")
    try:
        if connection.type == ConnectionType.csv:
            return pd.read_csv(path, dtype=object, keep_default_na=True)
        if connection.type == ConnectionType.excel:
            return pd.read_excel(
                path, sheet_name=connection.config.get("sheet_name", 0), dtype=object
            )
        if connection.type == ConnectionType.json:
            payload = json.loads(path.read_text(encoding="utf-8"))
            items_path = connection.config.get("items_path")
            if items_path:
                for part in items_path.split("."):
                    payload = payload[part]
            return pd.json_normalize(payload)
        if connection.type == ConnectionType.xml:
            return _read_xml(path)
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"Could not parse {path.name}: {exc}") from exc
    raise ExtractionError(f"Unsupported file type: {connection.type.value}")


def _validate_read_query(query: str) -> str:
    cleaned = query.strip().rstrip(";")
    if ";" in cleaned or not re.match(r"^(select|with)\s", cleaned, flags=re.I):
        raise ExtractionError("Database extraction query must be one read-only SELECT statement")
    forbidden = re.search(
        r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|do)\b",
        cleaned,
        flags=re.I,
    )
    if forbidden:
        raise ExtractionError("Potentially mutating SQL is not allowed")
    return cleaned


def _database_frame(connection: Connection) -> pd.DataFrame:
    url = connection.config.get("database_url")
    query = _validate_read_query(connection.config.get("query", ""))
    if not url:
        raise ExtractionError("PostgreSQL source requires database_url")
    try:
        source_engine = create_engine(url, pool_pre_ping=True)
        with source_engine.connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            return pd.read_sql_query(text(query), conn)
    except Exception as exc:
        raise ExtractionError(f"PostgreSQL extraction failed: {exc}") from exc


def _get_path(payload: Any, path: str) -> Any:
    value = payload
    for part in path.split(".") if path else []:
        value = value[part]
    return value


def _request_with_retry(
    client: httpx.Client, url: str, *, headers: dict, params: dict
) -> httpx.Response:
    delay = 0.25
    for attempt in range(5):
        try:
            response = client.get(url, headers=headers, params=params)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                time.sleep(min(float(retry_after) if retry_after else delay, 5))
                delay = min(delay * 2, 5)
                continue
            if response.status_code >= 500:
                if attempt == 4:
                    response.raise_for_status()
                time.sleep(delay)
                delay = min(delay * 2, 5)
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            if attempt == 4:
                raise ExtractionError(f"REST request failed after retries: {exc}") from exc
            time.sleep(delay)
            delay = min(delay * 2, 5)
    raise ExtractionError("REST request retry limit reached")


def _rest_frame(connection: Connection) -> pd.DataFrame:
    config = connection.config
    url = validate_external_url(config.get("url", ""))
    headers = config.get("headers", {})
    pagination = config.get("pagination", "none")
    items_path = config.get("items_path", "items")
    page_size = min(int(config.get("page_size", 100)), 1000)
    max_pages = min(int(config.get("max_pages", 1000)), 10000)
    params = dict(config.get("params", {}))
    rows: list[dict] = []
    with httpx.Client(timeout=httpx.Timeout(30, connect=10)) as client:
        next_url: str | None = url
        cursor = None
        for page in range(1, max_pages + 1):
            if not next_url:
                break
            request_params = dict(params)
            if pagination == "page":
                request_params.update(
                    {config.get("page_param", "page"): page, "page_size": page_size}
                )
            elif pagination == "offset":
                request_params.update({"offset": (page - 1) * page_size, "limit": page_size})
            elif pagination == "cursor" and cursor:
                request_params[config.get("cursor_param", "cursor")] = cursor
            response = _request_with_retry(client, next_url, headers=headers, params=request_params)
            payload = response.json()
            items = _get_path(payload, items_path) if items_path else payload
            if not isinstance(items, list):
                raise ExtractionError("Configured REST items path did not contain a list")
            rows.extend(items)
            if pagination == "none" or not items:
                break
            if pagination in {"page", "offset"} and len(items) < page_size:
                break
            if pagination == "cursor":
                cursor = _get_path(payload, config.get("cursor_path", "next_cursor"))
                if not cursor:
                    break
            if pagination == "next_link":
                next_url = _get_path(payload, config.get("next_link_path", "next"))
                if next_url:
                    validate_external_url(next_url)
            else:
                next_url = url
    return pd.json_normalize(rows)


def extract_records(connection: Connection) -> pd.DataFrame:
    if connection.type in {
        ConnectionType.csv,
        ConnectionType.excel,
        ConnectionType.json,
        ConnectionType.xml,
    }:
        return _file_frame(connection)
    if connection.type == ConnectionType.postgresql:
        return _database_frame(connection)
    if connection.type == ConnectionType.rest_api:
        return _rest_frame(connection)
    raise ExtractionError(f"Unsupported source type: {connection.type.value}")


def inspect_source(connection: Connection, limit: int = 20) -> dict[str, Any]:
    frame = extract_records(connection)
    sample = frame.head(limit).astype(object)
    sample = sample.where(pd.notna(sample), None)
    issues = []
    duplicate_count = int(frame.duplicated().sum())
    if duplicate_count:
        issues.append(f"{duplicate_count} duplicate rows detected")
    null_columns = {
        str(c): int(frame[c].isna().sum()) for c in frame.columns if frame[c].isna().any()
    }
    if null_columns:
        issues.append("Null values found in: " + ", ".join(null_columns))
    return {
        "row_count": len(frame),
        "columns": [{"name": str(c), "dtype": str(frame[c].dtype)} for c in frame.columns],
        "records": sample.to_dict(orient="records"),
        "issues": issues,
    }
