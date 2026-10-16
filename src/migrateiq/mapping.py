import json
import re
from difflib import SequenceMatcher

from pydantic import BaseModel, Field, ValidationError

from .config import get_settings
from .schemas import MappingItem

ALIASES = {
    "id": {"identifier", "key"},
    "customer": {"cust", "client", "account"},
    "name": {"full_name", "customer_name", "cust_name"},
    "phone": {"mobile", "telephone", "contact", "contact_number", "mobile_no", "phone_number"},
    "email": {"email_address", "email_id", "mail"},
    "created": {"joined", "signup", "registered", "join", "created_at", "signup_date"},
}


def _tokens(name: str) -> set[str]:
    value = re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower()
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", value)))
    expanded = set(tokens)
    for token in tokens:
        for canonical, aliases in ALIASES.items():
            if token == canonical or token in aliases:
                expanded.add(canonical)
    return expanded


def mapping_score(source: str, destination: str) -> float:
    left, right = _tokens(source), _tokens(destination)
    token_score = len(left & right) / max(len(left | right), 1)
    text_score = SequenceMatcher(None, source.lower(), destination.lower()).ratio()
    canonical_matches = left & right & set(ALIASES)
    alias_score = min(0.98, 0.78 + 0.1 * len(canonical_matches)) if canonical_matches else 0
    suffix_bonus = 0.15 if left & right and ({"id", "date", "name"} & left & right) else 0
    return min(1, round(max(token_score, text_score * 0.8, alias_score) + suffix_bonus, 3))


def deterministic_mapping(
    source_columns: list[str], destination_columns: list[str]
) -> list[MappingItem]:
    suggestions = []
    used: set[str] = set()
    for source in source_columns:
        candidates = sorted(
            (
                (mapping_score(source, dest), dest)
                for dest in destination_columns
                if dest not in used
            ),
            reverse=True,
        )
        score, destination = candidates[0] if candidates else (0, None)
        if score < 0.25:
            destination = None
        elif destination:
            used.add(destination)
        suggestions.append(
            MappingItem(
                source=source,
                destination=destination,
                included=destination is not None,
                confidence=score,
                explanation=(
                    "Matched by normalized field-name similarity"
                    if destination
                    else "No reliable match found"
                ),
            )
        )
    return suggestions


class AISuggestion(BaseModel):
    source: str
    destination: str | None
    confidence: float = Field(ge=0, le=1)
    explanation: str


class AIResponse(BaseModel):
    mappings: list[AISuggestion]


def ai_mapping(source_columns: list[str], destination_columns: list[str]) -> list[MappingItem]:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("Gemini is not configured")
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = (
        "Suggest one-to-one schema mappings. Return JSON only with a 'mappings' array containing "
        "source, destination (or null), confidence 0..1, and a short explanation. "
        "Never invent fields. "
        f"Source columns: {json.dumps(source_columns[:200])}. "
        f"Destination columns: {json.dumps(destination_columns[:200])}."
    )
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config={"response_mime_type": "application/json", "max_output_tokens": 4096},
    )
    try:
        parsed = AIResponse.model_validate_json(response.text)
    except (ValidationError, ValueError, TypeError) as exc:
        raise RuntimeError("Gemini returned an invalid mapping response") from exc
    allowed_source, allowed_destination = set(source_columns), set(destination_columns)
    if any(
        item.source not in allowed_source
        or (item.destination is not None and item.destination not in allowed_destination)
        for item in parsed.mappings
    ):
        raise RuntimeError("Gemini suggested fields outside the approved schemas")
    return [
        MappingItem(**item.model_dump(), included=item.destination is not None)
        for item in parsed.mappings
    ]


def suggest_mapping(
    source_columns: list[str], destination_columns: list[str], use_ai: bool = True
) -> tuple[list[MappingItem], str, str | None]:
    if use_ai and get_settings().gemini_api_key:
        try:
            return ai_mapping(source_columns, destination_columns), "gemini", None
        except Exception as exc:
            return (
                deterministic_mapping(source_columns, destination_columns),
                "deterministic",
                f"Gemini unavailable ({type(exc).__name__}); deterministic fallback used",
            )
    return deterministic_mapping(source_columns, destination_columns), "deterministic", None
