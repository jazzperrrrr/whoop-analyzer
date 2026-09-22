"""Explicit public response types; product dataclasses are framework independent."""

from dataclasses import asdict
from datetime import date, datetime
from typing import Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict
from whoop_product.models import Freshness, TodayReport, SleepReport, TrendReport

T = TypeVar('T')


class Health(BaseModel):
    status: Literal['ok'] = 'ok'
    schema_version: Literal['v1'] = 'v1'


class Error(BaseModel):
    code: str
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


TodayResponse = Envelope[TodayReport]
SleepResponse = Envelope[SleepReport]
TrendResponse = Envelope[TrendReport]


def serialize_product(envelope, schema):
    # Only allowlisted product contracts enter this function, never domain reports.
    return schema.model_validate(asdict(envelope))
