import re
from typing import Any

import pandas as pd

from .schemas import ValidationRule

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_record(record: dict[str, Any], rules: list[ValidationRule | dict]) -> list[str]:
    errors = []
    for raw in rules:
        rule = raw if isinstance(raw, ValidationRule) else ValidationRule.model_validate(raw)
        value = record.get(rule.column)
        missing = (
            value is None
            or (isinstance(value, float) and pd.isna(value))
            or str(value).strip() == ""
        )
        if rule.rule == "required" and missing:
            errors.append(f"{rule.column} is required")
        elif rule.rule == "email" and not missing and not EMAIL_PATTERN.match(str(value)):
            errors.append(f"{rule.column} is not a valid email address")
        elif (
            rule.rule == "date" and not missing and pd.isna(pd.to_datetime(value, errors="coerce"))
        ):
            errors.append(f"{rule.column} is not a valid date")
        elif rule.rule == "type" and not missing:
            try:
                {"integer": int, "float": float, "string": str}[str(rule.value)](value)
            except (KeyError, ValueError, TypeError):
                errors.append(f"{rule.column} is not a valid {rule.value}")
        elif rule.rule == "regex" and not missing:
            try:
                matches = bool(re.fullmatch(rule.value or "", str(value)))
            except re.error:
                errors.append(f"{rule.column} has an invalid configured regex")
            else:
                if not matches:
                    errors.append(f"{rule.column} does not match the required format")
    return errors
