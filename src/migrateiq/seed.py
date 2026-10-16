import argparse
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, init_db
from .models import Connection, ConnectionKind, ConnectionType, Project


def generate_customers(count: int, seed: int = 42) -> pd.DataFrame:
    rng = random.Random(seed)
    first_names = ["Aarav", "Ananya", "Kabir", "Isha", "Meera", "Rohan", "Sara", "Vikram"]
    last_names = ["Das", "Gupta", "Khan", "Nair", "Patel", "Rao", "Sharma", "Singh"]
    records = []
    for index in range(1, count + 1):
        customer_id = f"CUST-{index:06d}"
        if index % 997 == 0:
            customer_id = f"CUST-{index - 1:06d}"
        signup = date(2019, 1, 1) + timedelta(days=index % 2200)
        records.append(
            {
                "cust_id": customer_id,
                "cust_name": f"  {rng.choice(first_names)} {rng.choice(last_names)}  "
                if index % 11 == 0
                else f"{rng.choice(first_names)} {rng.choice(last_names)}",
                "mobile_no": "not-a-phone" if index % 113 == 0 else f"9{index:09d}"[-10:],
                "email_id": None
                if index % 47 == 0
                else ("malformed-email" if index % 173 == 0 else f"customer{index}@example.com"),
                "join_date": signup.strftime(
                    "%d/%m/%Y" if index % 3 == 0 else "%m-%d-%Y" if index % 5 == 0 else "%Y-%m-%d"
                ),
                "legacy_code": rng.choice(["A", "B", "C", None]),
                "unexpected_notes": "synthetic record",
            }
        )
    if records:
        records[-1]["cust_id"] = None
        records[-1]["join_date"] = "impossible-date"
    return pd.DataFrame(records)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate repeatable synthetic MigrateIQ demo data"
    )
    parser.add_argument("--count", type=int, default=10_000)
    parser.add_argument("--output", type=Path, default=Path("demo_data/customers_10000.csv"))
    parser.add_argument(
        "--register", action="store_true", help="Register file and demo project in metadata DB"
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    generate_customers(args.count).to_csv(args.output, index=False)
    print(f"Generated {args.count:,} synthetic records at {args.output}")
    if args.register:
        init_db()
        with SessionLocal() as session:
            source = Connection(
                name=f"Synthetic CSV ({args.count:,})",
                kind=ConnectionKind.source,
                type=ConnectionType.csv,
                config={"path": str(args.output.resolve())},
            )
            destination = session.scalar(
                select(Connection).where(
                    Connection.name == "MigrateIQ PostgreSQL",
                    Connection.kind == ConnectionKind.destination,
                )
            )
            if not destination:
                destination = Connection(
                    name="MigrateIQ PostgreSQL",
                    kind=ConnectionKind.destination,
                    type=ConnectionType.postgresql,
                    config={"database_url": get_settings().database_url},
                )
                session.add(destination)
            session.add(source)
            session.flush()
            session.add(
                Project(
                    name=f"Synthetic customer migration ({args.count:,})",
                    description="Repeatable portfolio demonstration dataset; contains no real PII.",
                    source_connection_id=source.id,
                    destination_connection_id=destination.id,
                    business_key="customer_id",
                )
            )
            session.commit()
        print("Registered source, destination, and project")


if __name__ == "__main__":
    main()
