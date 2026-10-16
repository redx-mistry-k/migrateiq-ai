import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from migrateiq.demo_crm import (
    API_KEY,
    ModernCustomer,
    authenticate,
    create_modern_customer,
    list_legacy_customers,
    modern_customers,
    update_modern_customer,
)


def test_demo_crm_auth_pagination_validation_and_upsert():
    with pytest.raises(HTTPException) as unauthorized:
        authenticate(None)
    assert unauthorized.value.status_code == 401
    assert authenticate(API_KEY) is None

    first = list_legacy_customers(
        page=1, page_size=2, simulate_rate_limit=None, response=Response()
    )
    second = list_legacy_customers(
        page=2, page_size=2, simulate_rate_limit=None, response=Response()
    )
    assert first["items"] != second["items"]

    modern_customers.clear()
    payload = {
        "customer_id": "LEG-00001",
        "full_name": "Aarav Sharma",
        "phone": "+919800000001",
        "email": "customer1@example.com",
        "created_at": "2020-01-02",
    }
    customer = ModernCustomer(**payload)
    assert create_modern_customer(customer)["customer_id"] == "LEG-00001"
    with pytest.raises(HTTPException) as conflict:
        create_modern_customer(customer)
    assert conflict.value.status_code == 409

    payload["full_name"] = "Updated Name"
    updated = update_modern_customer("LEG-00001", ModernCustomer(**payload))
    assert updated["full_name"] == "Updated Name"
    with pytest.raises(ValidationError):
        ModernCustomer(**{**payload, "customer_id": "bad", "email": "bad"})
