import json

import pandas as pd
import pytest
import respx
from httpx import Response

from migrateiq.connectors.extractors import ExtractionError, extract_records
from migrateiq.models import Connection, ConnectionKind, ConnectionType


def connection(connection_type, config):
    return Connection(
        name="test",
        kind=ConnectionKind.source,
        type=connection_type,
        config=config,
    )


def test_file_extractors(tmp_path):
    rows = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    csv_path = tmp_path / "source.csv"
    excel_path = tmp_path / "source.xlsx"
    json_path = tmp_path / "source.json"
    xml_path = tmp_path / "source.xml"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    pd.DataFrame(rows).to_excel(excel_path, index=False)
    json_path.write_text(json.dumps({"items": rows}), encoding="utf-8")
    xml_path.write_text(
        "<customers><customer><id>1</id><name>A</name></customer>"
        "<customer><id>2</id><name>B</name></customer></customers>",
        encoding="utf-8",
    )
    assert len(extract_records(connection(ConnectionType.csv, {"path": str(csv_path)}))) == 2
    assert len(extract_records(connection(ConnectionType.excel, {"path": str(excel_path)}))) == 2
    assert (
        len(
            extract_records(
                connection(ConnectionType.json, {"path": str(json_path), "items_path": "items"})
            )
        )
        == 2
    )
    assert len(extract_records(connection(ConnectionType.xml, {"path": str(xml_path)}))) == 2


def test_xml_rejects_entities(tmp_path):
    path = tmp_path / "unsafe.xml"
    path.write_text(
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><items><i>&xxe;</i></items>',
        encoding="utf-8",
    )
    with pytest.raises(ExtractionError, match="unsafe XML"):
        extract_records(connection(ConnectionType.xml, {"path": str(path)}))


@respx.mock
def test_rest_pagination_and_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "migrateiq.connectors.extractors.validate_external_url", lambda value: value
    )
    calls = {"page1": 0}

    def endpoint(request):
        page = int(request.url.params.get("page", "1"))
        if page == 1 and calls["page1"] == 0:
            calls["page1"] += 1
            return Response(429, headers={"Retry-After": "0"})
        if page == 1:
            return Response(200, json={"items": [{"id": 1}, {"id": 2}]})
        return Response(200, json={"items": [{"id": 3}]})

    respx.get("https://api.example.test/customers").mock(side_effect=endpoint)
    frame = extract_records(
        connection(
            ConnectionType.rest_api,
            {
                "url": "https://api.example.test/customers",
                "items_path": "items",
                "pagination": "page",
                "page_size": 2,
            },
        )
    )
    assert frame["id"].tolist() == [1, 2, 3]
    assert calls["page1"] == 1
