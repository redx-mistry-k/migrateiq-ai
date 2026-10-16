import pytest

from migrateiq.security import UnsafeURLError, redact_config, validate_external_url


def test_connection_secrets_are_redacted():
    result = redact_config(
        {
            "database_url": "postgresql+psycopg://user:password@db.example.com/app",
            "headers": {"X-API-Key": "secret-value", "Accept": "application/json"},
            "token": "secret-value",
        }
    )
    assert "password" not in result["database_url"]
    assert result["headers"]["X-API-Key"] == "***"
    assert result["headers"]["Accept"] == "application/json"
    assert result["token"] == "***"


def test_private_networks_are_blocked_by_default():
    with pytest.raises(UnsafeURLError):
        validate_external_url("http://127.0.0.1/internal", allow_private=False)
    assert validate_external_url("http://127.0.0.1/internal", allow_private=True)
