import json
from collections import Counter
from functools import lru_cache

from pydantic import BaseModel, Field, ValidationError

from .config import get_settings
from .schemas import TransformRule


class CleaningSuggestion(BaseModel):
    rule: TransformRule
    confidence: float = Field(ge=0, le=1)
    explanation: str


class CleaningResponse(BaseModel):
    suggestions: list[CleaningSuggestion]


class ValidationExplanation(BaseModel):
    summary: str
    recommended_actions: list[str]


def deterministic_cleaning(columns: list[dict]) -> list[CleaningSuggestion]:
    suggestions = []
    for item in columns:
        name = str(item["name"])
        lowered = name.lower()
        dtype = str(item.get("dtype", ""))
        if "object" in dtype or "string" in dtype:
            suggestions.append(
                CleaningSuggestion(
                    rule=TransformRule(operation="trim", column=name),
                    confidence=0.7,
                    explanation="Text columns commonly contain leading or trailing whitespace.",
                )
            )
        if "email" in lowered:
            suggestions.append(
                CleaningSuggestion(
                    rule=TransformRule(operation="validate_email", column=name),
                    confidence=0.95,
                    explanation="The column name indicates email-address data.",
                )
            )
        if any(token in lowered for token in ("phone", "mobile", "contact_number")):
            suggestions.append(
                CleaningSuggestion(
                    rule=TransformRule(operation="normalize_phone", column=name),
                    confidence=0.9,
                    explanation="The column name indicates phone-number data.",
                )
            )
        if any(token in lowered for token in ("date", "created", "joined", "signup")):
            suggestions.append(
                CleaningSuggestion(
                    rule=TransformRule(operation="normalize_date", column=name),
                    confidence=0.9,
                    explanation="The column name indicates date data that may use mixed formats.",
                )
            )
    return suggestions


@lru_cache(maxsize=128)
def _gemini_cleaning(schema_json: str) -> tuple[CleaningSuggestion, ...]:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini is not configured")
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=(
            "Suggest only supported cleaning rules for this schema metadata. "
            "No record values or PII "
            "are included. Return JSON with a suggestions array; each item contains rule "
            "({operation,column,params}), confidence, and explanation. "
            "Supported operations: trim, lower, upper, title, normalize_date, convert_type, "
            "replace_null, validate_email, normalize_phone, "
            f"rename, combine, split, default, deduplicate. Schema: {schema_json}"
        ),
        config={"response_mime_type": "application/json", "max_output_tokens": 4096},
    )
    try:
        result = CleaningResponse.model_validate_json(response.text)
    except (ValidationError, ValueError, TypeError) as exc:
        raise RuntimeError("Gemini returned invalid cleaning suggestions") from exc
    return tuple(result.suggestions)


def cleaning_suggestions(
    columns: list[dict], use_ai: bool = True
) -> tuple[list[dict], str, str | None]:
    if use_ai and get_settings().gemini_api_key:
        try:
            suggestions = _gemini_cleaning(json.dumps(columns, sort_keys=True))
            return [item.model_dump(mode="json") for item in suggestions], "gemini", None
        except Exception as exc:
            fallback = deterministic_cleaning(columns)
            warning = f"Gemini unavailable ({type(exc).__name__}); deterministic fallback used"
            return [item.model_dump(mode="json") for item in fallback], "deterministic", warning
    fallback = deterministic_cleaning(columns)
    return [item.model_dump(mode="json") for item in fallback], "deterministic", None


@lru_cache(maxsize=128)
def _gemini_validation(errors_json: str) -> ValidationExplanation:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini is not configured")
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=(
            "Explain these validation error messages and recommend safe configuration corrections. "
            "No record values are included. Never suggest arbitrary code or directly modify data. "
            "Return JSON with summary and recommended_actions. Errors: " + errors_json
        ),
        config={"response_mime_type": "application/json", "max_output_tokens": 2048},
    )
    try:
        return ValidationExplanation.model_validate_json(response.text)
    except (ValidationError, ValueError, TypeError) as exc:
        raise RuntimeError("Gemini returned an invalid validation explanation") from exc


def explain_validation(
    errors: list[str], use_ai: bool = True
) -> tuple[ValidationExplanation, str, str | None]:
    if not errors:
        result = ValidationExplanation(
            summary="No validation failures were supplied.", recommended_actions=[]
        )
        return result, "deterministic", None
    if use_ai and get_settings().gemini_api_key:
        try:
            return _gemini_validation(json.dumps(errors[:500], sort_keys=True)), "gemini", None
        except Exception as exc:
            warning = f"Gemini unavailable ({type(exc).__name__}); deterministic fallback used"
    else:
        warning = None
    categories = Counter(error.split(" is ")[0] for error in errors)
    actions = [
        f"Review the source mapping or cleaning rule for {field} ({count} failures)."
        for field, count in categories.most_common(10)
    ]
    result = ValidationExplanation(
        summary=f"Found {len(errors)} validation failures across {len(categories)} fields.",
        recommended_actions=actions,
    )
    return result, "deterministic", warning
