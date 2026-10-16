import pandas as pd
import pytest

from migrateiq.mapping import AIResponse, deterministic_mapping, suggest_mapping
from migrateiq.schemas import TransformRule
from migrateiq.transformations import TransformationError, apply_transformations
from migrateiq.validation import validate_record


def test_deterministic_mapping_handles_legacy_names():
    result = deterministic_mapping(
        ["cust_id", "cust_name", "mobile_no", "join_date"],
        ["customer_id", "full_name", "phone_number", "created_at"],
    )
    pairs = {item.source: item.destination for item in result}
    assert pairs == {
        "cust_id": "customer_id",
        "cust_name": "full_name",
        "mobile_no": "phone_number",
        "join_date": "created_at",
    }


def test_deterministic_mapping_matches_ui_destination_names():
    result = deterministic_mapping(
        ["cust_id", "cust_name", "mobile_no", "email_id", "join_date"],
        ["customer_id", "full_name", "phone", "email", "created_at"],
    )
    assert {item.source: item.destination for item in result} == {
        "cust_id": "customer_id",
        "cust_name": "full_name",
        "mobile_no": "phone",
        "email_id": "email",
        "join_date": "created_at",
    }


def test_invalid_ai_response_is_rejected():
    with pytest.raises(ValueError):
        AIResponse.model_validate(
            {"mappings": [{"source": "a", "destination": "b", "confidence": 4}]}
        )


def test_provider_failure_uses_sanitized_deterministic_fallback(monkeypatch):
    monkeypatch.setattr(
        "migrateiq.mapping.get_settings", lambda: type("S", (), {"gemini_api_key": "set"})()
    )
    monkeypatch.setattr(
        "migrateiq.mapping.ai_mapping",
        lambda *_: (_ for _ in ()).throw(ConnectionError("secret provider detail")),
    )
    mappings, provider, warning = suggest_mapping(["cust_id"], ["customer_id"], use_ai=True)
    assert mappings[0].destination == "customer_id"
    assert provider == "deterministic"
    assert "ConnectionError" in warning
    assert "secret provider detail" not in warning


def test_transformations_are_explicit_and_handle_bad_values():
    frame = pd.DataFrame(
        [
            {"name": "  jane DOE ", "date": "31/12/2024", "phone": "(555) 123-4567"},
            {"name": None, "date": "bad", "phone": None},
        ]
    )
    result = apply_transformations(
        frame,
        [
            TransformRule(operation="trim", column="name"),
            TransformRule(operation="title", column="name"),
            TransformRule(operation="normalize_date", column="date"),
            TransformRule(operation="normalize_phone", column="phone"),
            TransformRule(operation="replace_null", column="name", params={"value": "Unknown"}),
        ],
    )
    assert result.iloc[0].to_dict() == {
        "name": "Jane Doe",
        "date": "2024-12-31",
        "phone": "+5551234567",
    }
    assert result.iloc[1]["name"] == "Unknown"
    assert result.iloc[1]["date"] is None
    with pytest.raises(TransformationError):
        apply_transformations(frame, [{"operation": "trim", "column": "missing"}])


def test_validation_reports_actionable_errors():
    errors = validate_record(
        {"customer_id": "", "email": "broken", "created_at": "not-a-date"},
        [
            {"column": "customer_id", "rule": "required"},
            {"column": "email", "rule": "email"},
            {"column": "created_at", "rule": "date"},
        ],
    )
    assert len(errors) == 3
