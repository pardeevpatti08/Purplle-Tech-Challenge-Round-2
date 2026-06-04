from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


class EventMetadata(BaseModel):
    queue_depth: int | None = None
    sku_zone: str | None = None
    session_seq: int | None = None

    model_config = ConfigDict(extra="allow")


class EventIn(BaseModel):
    event_id: UUID
    store_id: str = Field(min_length=1)
    camera_id: str = Field(min_length=1)
    visitor_id: str = Field(min_length=1)
    event_type: EventType
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)


class IngestRequest(BaseModel):
    events: list[dict[str, Any]] = Field(max_length=500)


class ReplaceEventsRequest(BaseModel):
    events: list[dict[str, Any]] = Field(max_length=100000)


class RejectedEvent(BaseModel):
    event: dict[str, Any]
    reason: str


class IngestResponse(BaseModel):
    accepted: int
    rejected: list[RejectedEvent]


class HealthResponse(BaseModel):
    status: str
    db_status: str
    last_event_per_store: dict[str, datetime | None]
    stale_feeds: list[str]
