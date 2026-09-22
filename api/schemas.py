"""Explicit public response types; product dataclasses are framework independent."""

from dataclasses import asdict
from datetime import date, datetime
from typing import Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict
from whoop_product.models import Freshness, TodayReport, SleepReport, TrendReport

T = TypeVar('T')


class Health(BaseModel):
    status: Literal['ok']
    schema_version: Literal['v1']


class Error(BaseModel):
    code: Literal['local_access_only', 'not_found', 'read_only', 'invalid_query',
                  'internal_error', 'snapshot_unavailable']
    message: str
    retryable: bool
    request_id: str


class Envelope(BaseModel, Generic[T]):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    schema_version: Literal['v1']
    analysis_version: str
    snapshot_id: str
    generated_at: datetime
    report_date: date | None
    latest_available_morning: date | None
    collection_freshness: Freshness
    last_successful_sync_at: datetime | None
    data: T


class TodayResponse(Envelope[TodayReport]):
    pass


class SleepResponse(Envelope[SleepReport]):
    pass


class TrendResponse(Envelope[TrendReport]):
    pass


def serialize_product(envelope, schema):
    # Only allowlisted product contracts enter this function, never domain reports.
    return schema.model_validate(asdict(envelope))
