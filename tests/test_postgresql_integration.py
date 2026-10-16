import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from migrateiq.connectors.extractors import extract_records
from migrateiq.models import Connection, ConnectionKind, ConnectionType


@pytest.mark.integration
def test_postgresql_read_only_extraction():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run disposable PostgreSQL integration tests")
    engine = create_engine(database_url)
    table = f"migrateiq_test_{uuid.uuid4().hex[:12]}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE TABLE "{table}" (customer_id text, full_name text)'))
        connection.execute(
            text(f'INSERT INTO "{table}" VALUES (:id, :name)'),
            [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}],
        )
    try:
        source = Connection(
            name="Disposable PostgreSQL",
            kind=ConnectionKind.source,
            type=ConnectionType.postgresql,
            config={"database_url": database_url, "query": f'SELECT * FROM "{table}" ORDER BY 1'},
        )
        frame = extract_records(source)
        assert frame.to_dict(orient="records") == [
            {"customer_id": "1", "full_name": "Alice"},
            {"customer_id": "2", "full_name": "Bob"},
        ]
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
