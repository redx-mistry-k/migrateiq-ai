"""Exercise the live Streamlit data-source upload and connection form."""

from __future__ import annotations

import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "frontend" / "app.py"


def by_label(elements, label: str):
    return next(item for item in elements if item.label == label)


def main() -> None:
    suffix = str(int(time.time()))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    app.sidebar.radio[0].set_value("Data Sources")
    app.run(timeout=60)

    csv_bytes = b"id,name,email\nUI-1,UI Upload,ui-upload@example.test\n"
    app.file_uploader[0].set_value(("ui-acceptance.csv", csv_bytes, "text/csv"))
    by_label(app.text_input, "Connection name").set_value(f"UI upload {suffix}")
    app.run(timeout=60)
    by_label(app.button, "Upload and inspect").click()
    app.run(timeout=60)
    assert not app.exception, [str(item.value) for item in app.exception]
    assert any(item.value == "Source uploaded" for item in app.success)

    by_label(app.selectbox, "Role").set_value("destination")
    app.run(timeout=60)
    by_label(app.selectbox, "Type").set_value("postgresql")
    by_label(app.text_input, "Name").set_value(f"UI PostgreSQL destination {suffix}")
    by_label(app.text_input, "PostgreSQL URL").set_value(
        "postgresql+psycopg://migrateiq:migrateiq@postgres:5432/migrateiq_acceptance"
    )
    by_label(app.button, "Save connection").click()
    app.run(timeout=60)
    assert not app.exception, [str(item.value) for item in app.exception]
    assert any(item.value == "Connection saved" for item in app.success)

    test_widget = by_label(app.selectbox, "Test connection")
    target = next(
        option for option in test_widget.options if option.startswith("UI PostgreSQL destination")
    )
    test_widget.set_value(target)
    app.run(timeout=60)
    by_label(app.button, "Run connection test").click()
    app.run(timeout=60)
    assert not app.exception, [str(item.value) for item in app.exception]
    message = next(item.value for item in app.success if "connection succeeded" in item.value)
    print(f"upload=passed; save_connection=passed; connection_test={message}")


if __name__ == "__main__":
    main()
