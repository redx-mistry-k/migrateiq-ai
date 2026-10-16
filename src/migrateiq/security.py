import ipaddress
import socket
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from .config import get_settings


class UnsafeURLError(ValueError):
    pass


def validate_external_url(url: str, *, allow_private: bool | None = None) -> str:
    """Reject non-HTTP and private-network URLs unless explicitly enabled."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeURLError("Only absolute HTTP(S) URLs are supported")
    permitted = get_settings().allow_private_networks if allow_private is None else allow_private
    if permitted:
        return url
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port)}
    except socket.gaierror as exc:
        raise UnsafeURLError("The remote host could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeURLError("Private, loopback, and link-local destinations are disabled")
    return url


def redact_config(config: dict) -> dict:
    sensitive = {"password", "token", "authorization", "api_key", "secret"}

    def secret_key(key: str) -> bool:
        normalized = key.lower().replace("-", "_")
        return any(term in normalized for term in sensitive)

    result = {}
    for key, value in config.items():
        if secret_key(key):
            result[key] = "***"
        elif key == "database_url" and isinstance(value, str):
            try:
                result[key] = make_url(value).render_as_string(hide_password=True)
            except ValueError:
                result[key] = "***"
        elif key == "headers" and isinstance(value, dict):
            result[key] = {k: ("***" if secret_key(k) else v) for k, v in value.items()}
        else:
            result[key] = value
    return result
