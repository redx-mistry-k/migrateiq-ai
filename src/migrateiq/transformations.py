import re
from collections.abc import Callable

import pandas as pd

from .schemas import MappingItem, TransformRule


class TransformationError(ValueError):
    pass


def apply_mapping(frame: pd.DataFrame, mappings: list[MappingItem | dict]) -> pd.DataFrame:
    result: dict[str, pd.Series] = {}
    for raw in mappings:
        item = raw if isinstance(raw, MappingItem) else MappingItem.model_validate(raw)
        if item.included and item.destination and item.source in frame.columns:
            result[item.destination] = frame[item.source].copy()
    if not result:
        raise TransformationError("No included mappings match the source columns")
    return pd.DataFrame(result, index=frame.index)


def _require_column(frame: pd.DataFrame, column: str | None) -> str:
    if not column or column not in frame.columns:
        raise TransformationError(f"Transformation column not found: {column}")
    return column


def _string_operation(frame: pd.DataFrame, rule: TransformRule, method: str) -> pd.DataFrame:
    column = _require_column(frame, rule.column)
    mask = frame[column].notna()
    frame.loc[mask, column] = getattr(frame.loc[mask, column].astype(str).str, method)()
    return frame


def _trim(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    return _string_operation(frame, rule, "strip")


def _lower(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    return _string_operation(frame, rule, "lower")


def _upper(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    return _string_operation(frame, rule, "upper")


def _title(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    return _string_operation(frame, rule, "title")


def _normalize_date(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    column = _require_column(frame, rule.column)
    parsed = pd.to_datetime(frame[column], errors="coerce", format="mixed")
    output_format = rule.params.get("format", "%Y-%m-%d")
    frame[column] = parsed.dt.strftime(output_format).where(parsed.notna(), None)
    return frame


def _convert_type(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    column = _require_column(frame, rule.column)
    target = rule.params.get("type")
    if target == "string":
        frame[column] = frame[column].where(frame[column].isna(), frame[column].astype(str))
    elif target == "integer":
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    elif target == "float":
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    elif target == "boolean":
        truthy = {"true", "1", "yes", "y"}
        frame[column] = frame[column].map(
            lambda value: None if pd.isna(value) else str(value).lower() in truthy
        )
    else:
        raise TransformationError("convert_type requires string, integer, float, or boolean")
    return frame


def _replace_null(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    column = _require_column(frame, rule.column)
    frame[column] = frame[column].fillna(rule.params.get("value"))
    return frame


def _validate_email(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    _require_column(frame, rule.column)
    return frame


def _normalize_phone(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    column = _require_column(frame, rule.column)
    country_code = str(rule.params.get("country_code", ""))

    def normalize(value):
        if pd.isna(value):
            return None
        digits = re.sub(r"\D", "", str(value))
        if country_code and len(digits) == int(rule.params.get("local_length", 10)):
            digits = country_code.lstrip("+") + digits
        return f"+{digits}" if digits else None

    frame[column] = frame[column].map(normalize)
    return frame


def _rename(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    column = _require_column(frame, rule.column)
    destination = rule.params.get("to")
    if not destination:
        raise TransformationError("rename requires a 'to' parameter")
    return frame.rename(columns={column: str(destination)})


def _combine(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    columns = rule.params.get("columns", [])
    target = rule.column or rule.params.get("target")
    if not target or not columns or any(column not in frame.columns for column in columns):
        raise TransformationError("combine requires existing columns and a target")
    separator = str(rule.params.get("separator", " "))
    frame[target] = frame[columns].fillna("").astype(str).agg(separator.join, axis=1).str.strip()
    return frame


def _split(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    column = _require_column(frame, rule.column)
    targets = rule.params.get("targets", [])
    if not targets:
        raise TransformationError("split requires target columns")
    parts = (
        frame[column]
        .fillna("")
        .astype(str)
        .str.split(str(rule.params.get("separator", " ")), n=len(targets) - 1, expand=True)
    )
    for position, target in enumerate(targets):
        frame[target] = parts[position] if position in parts.columns else None
    return frame


def _default(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    if not rule.column:
        raise TransformationError("default requires a column")
    if rule.column not in frame.columns:
        frame[rule.column] = rule.params.get("value")
    else:
        frame[rule.column] = frame[rule.column].fillna(rule.params.get("value"))
    return frame


def _deduplicate(frame: pd.DataFrame, rule: TransformRule) -> pd.DataFrame:
    columns = rule.params.get("columns") or ([rule.column] if rule.column else None)
    if columns and any(column not in frame.columns for column in columns):
        raise TransformationError("deduplicate references an unknown column")
    return frame.drop_duplicates(subset=columns, keep=rule.params.get("keep", "first"))


REGISTRY: dict[str, Callable[[pd.DataFrame, TransformRule], pd.DataFrame]] = {
    "trim": _trim,
    "lower": _lower,
    "upper": _upper,
    "title": _title,
    "normalize_date": _normalize_date,
    "convert_type": _convert_type,
    "replace_null": _replace_null,
    "validate_email": _validate_email,
    "normalize_phone": _normalize_phone,
    "rename": _rename,
    "combine": _combine,
    "split": _split,
    "default": _default,
    "deduplicate": _deduplicate,
}


def apply_transformations(frame: pd.DataFrame, rules: list[TransformRule | dict]) -> pd.DataFrame:
    result = frame.copy()
    for raw in rules:
        rule = raw if isinstance(raw, TransformRule) else TransformRule.model_validate(raw)
        operation = REGISTRY.get(rule.operation)
        if not operation:
            raise TransformationError(f"Unsupported transformation: {rule.operation}")
        result = operation(result, rule)
    return result
