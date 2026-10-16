import random
from datetime import date, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, EmailStr, Field

app = FastAPI(title="MigrateIQ Demo CRM", version="0.1.0")
API_KEY = "demo-secret"


class LegacyCustomer(BaseModel):
    cust_id: str
    cust_name: str
    contact_number: str | None = None
    email_id: str | None = None
    signup_date: str | None = None
    legacy_segment: str | None = None


class ModernCustomer(BaseModel):
    customer_id: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    email: EmailStr | None = None
    created_at: str | None = None


def authenticate(x_api_key: str | None = Header(default=None)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid X-API-Key")


def generate_legacy(count: int = 250) -> list[dict]:
    first = ["Aarav", "Meera", "Vikram", "Sara", "Kabir", "Ananya", "Rohan", "Isha"]
    last = ["Sharma", "Patel", "Singh", "Khan", "Nair", "Gupta", "Rao", "Das"]
    rng = random.Random(42)
    rows = []
    for index in range(1, count + 1):
        name = f"{rng.choice(first)} {rng.choice(last)}"
        rows.append(
            {
                "cust_id": f"LEG-{index:05d}",
                "cust_name": f"  {name} " if index % 7 == 0 else name,
                "contact_number": "invalid" if index % 31 == 0 else f"98{index:08d}"[-10:],
                "email_id": None if index % 17 == 0 else f"customer{index}@example.com",
                "signup_date": (date(2020, 1, 1) + timedelta(days=index)).strftime(
                    "%d/%m/%Y" if index % 5 == 0 else "%Y-%m-%d"
                ),
                "legacy_segment": rng.choice(["retail", "SMB", "enterprise"]),
            }
        )
    return rows


legacy_customers = generate_legacy()
modern_customers: dict[str, dict] = {}
rate_limit_seen: set[str] = set()


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "legacy_records": len(legacy_customers),
        "modern_records": len(modern_customers),
    }


@app.get("/legacy/customers", dependencies=[Depends(authenticate)])
def list_legacy_customers(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    simulate_rate_limit: str | None = None,
    response: Response = None,
):
    if simulate_rate_limit and simulate_rate_limit not in rate_limit_seen:
        rate_limit_seen.add(simulate_rate_limit)
        raise HTTPException(429, "Simulated rate limit", headers={"Retry-After": "0.1"})
    start = (page - 1) * page_size
    items = legacy_customers[start : start + page_size]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": len(legacy_customers),
        "next": page + 1 if start + page_size < len(legacy_customers) else None,
    }


@app.get(
    "/legacy/customers/{customer_id}",
    response_model=LegacyCustomer,
    dependencies=[Depends(authenticate)],
)
def get_legacy_customer(customer_id: str):
    record = next((row for row in legacy_customers if row["cust_id"] == customer_id), None)
    if not record:
        raise HTTPException(404, "Customer not found")
    return record


@app.get("/modern/customers", dependencies=[Depends(authenticate)])
def list_modern_customers(
    page: int = Query(default=1, ge=1), page_size: int = Query(default=500, ge=1, le=1000)
):
    values = list(modern_customers.values())
    start = (page - 1) * page_size
    return {"items": values[start : start + page_size], "total": len(values)}


@app.get(
    "/modern/customers/{customer_id}",
    response_model=ModernCustomer,
    dependencies=[Depends(authenticate)],
)
def get_modern_customer(customer_id: str):
    if customer_id not in modern_customers:
        raise HTTPException(404, "Customer not found")
    return modern_customers[customer_id]


@app.post(
    "/modern/customers",
    response_model=ModernCustomer,
    status_code=201,
    dependencies=[Depends(authenticate)],
)
def create_modern_customer(customer: ModernCustomer):
    if customer.customer_id in modern_customers:
        raise HTTPException(409, "Customer already exists")
    modern_customers[customer.customer_id] = customer.model_dump(mode="json")
    return modern_customers[customer.customer_id]


@app.put(
    "/modern/customers/{customer_id}",
    response_model=ModernCustomer,
    dependencies=[Depends(authenticate)],
)
def update_modern_customer(customer_id: str, customer: ModernCustomer):
    if customer_id not in modern_customers:
        raise HTTPException(404, "Customer not found")
    if customer.customer_id != customer_id:
        raise HTTPException(422, "Path and payload customer IDs must match")
    modern_customers[customer_id] = customer.model_dump(mode="json")
    return modern_customers[customer_id]


@app.delete("/modern/customers", status_code=204, dependencies=[Depends(authenticate)])
def clear_modern_customers():
    modern_customers.clear()
    return Response(status_code=204)
