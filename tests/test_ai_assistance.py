from migrateiq.ai_assistance import cleaning_suggestions, explain_validation


def test_ai_assistance_has_safe_deterministic_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    suggestions, provider, warning = cleaning_suggestions(
        [
            {"name": "email_address", "dtype": "object"},
            {"name": "signup_date", "dtype": "object"},
            {"name": "mobile_no", "dtype": "object"},
        ],
        use_ai=False,
    )
    operations = {item["rule"]["operation"] for item in suggestions}
    assert {"trim", "validate_email", "normalize_date", "normalize_phone"} <= operations
    assert provider == "deterministic"
    assert warning is None

    explanation, provider, warning = explain_validation(
        ["email is not a valid email address", "email is not a valid email address"],
        use_ai=False,
    )
    assert "2 validation failures" in explanation.summary
    assert provider == "deterministic"
    assert warning is None
