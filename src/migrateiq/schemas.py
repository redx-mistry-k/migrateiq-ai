from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .models import ConnectionKind, ConnectionType, JobStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: ConnectionKind
    type: ConnectionType
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectionRead(ConnectionCreate, ORMModel):
    id: str
    created_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    source_connection_id: str | None = None
    destination_connection_id: str | None = None
    business_key: str = Field(default="customer_id", min_length=1, max_length=100)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    source_connection_id: str | None = None
    destination_connection_id: str | None = None
    business_key: str | None = Field(default=None, min_length=1, max_length=100)


class ProjectRead(ProjectCreate, ORMModel):
    id: str
    created_at: datetime
    updated_at: datetime


class MappingItem(BaseModel):
    source: str
    destination: str | None = None
    included: bool = True
    confidence: float = Field(default=1, ge=0, le=1)
    explanation: str = ""


class TransformRule(BaseModel):
    operation: Literal[
        "trim",
        "lower",
        "upper",
        "title",
        "normalize_date",
        "convert_type",
        "replace_null",
        "validate_email",
        "normalize_phone",
        "rename",
        "combine",
        "split",
        "default",
        "deduplicate",
    ]
    column: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ValidationRule(BaseModel):
    column: str
    rule: Literal["required", "email", "date", "type", "regex"]
    value: str | None = None


class MappingConfigWrite(BaseModel):
    mappings: list[MappingItem]
    transformations: list[TransformRule] = Field(default_factory=list)
    validation_rules: list[ValidationRule] = Field(default_factory=list)


class MappingConfigRead(MappingConfigWrite, ORMModel):
    id: str
    project_id: str
    updated_at: datetime


class MappingSuggestionRequest(BaseModel):
    source_columns: list[str]
    destination_columns: list[str]
    use_ai: bool = True


class PreviewRequest(BaseModel):
    project_id: str
    limit: int = Field(default=20, ge=1, le=200)
    transformations: list[TransformRule] | None = None


class JobCreate(BaseModel):
    project_id: str
    batch_size: int = Field(default=500, ge=1, le=10000)


class ValidationExplainRequest(BaseModel):
    errors: list[str] = Field(max_length=500)


class JobRead(ORMModel):
    id: str
    project_id: str
    status: JobStatus
    batch_size: int
    checkpoint_offset: int
    source_count: int
    processed_count: int
    inserted_count: int
    updated_count: int
    skipped_count: int
    rejected_count: int
    current_batch: int
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

    @property
    def progress_percent(self) -> float:
        return round(100 * self.processed_count / self.source_count, 2) if self.source_count else 0


class RestConfig(BaseModel):
    url: HttpUrl
    headers: dict[str, str] = Field(default_factory=dict)
    items_path: str = "items"
    pagination: Literal["none", "page", "offset", "cursor", "next_link"] = "none"
