"""CSV knowledge stays here. Reads are coherent; this interface has no writes."""

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
from typing import Callable, Protocol

import whoop_analysis as analysis
import whoop_daily_report as daily
import whoop_entities as entities
from whoop_sleep_product import compose_sleep_product, freshness
from whoop_sync import snapshot_read

INPUTS = ('sleeps.csv', 'recoveries.csv', 'cycles.csv', 'daily_metrics.csv',
          'workouts.csv', 'workout_classifications.csv')


class SnapshotUnavailable(Exception):
    """Never carries an underlying exception or private path to the API."""


class InvalidAnchor(Exception):
    pass


@dataclass(frozen=True)
class SnapshotMetadata:
    snapshot_id: str
    generated_at: datetime
    latest_available_morning: date | None


@dataclass(frozen=True)
class ReadSnapshot:
    """Internal read model; services explicitly map it to public contracts."""
    _report: daily.DailyReport
    _entities: dict
    _physiology: list
    _metadata: SnapshotMetadata
    _today: date

    def load_daily_report(self):
        return self._report

    def load_sleep_product(self, anchor_date=None):
        if anchor_date is None or anchor_date == self._report.report_date:
            return self._report.sleep_product
        # Limit the historical input, then let the existing selector choose.
        # Never infer a missing date or substitute a neighbouring morning.
        rows = [r for r in self._entities['sleeps'].values()
                if date.fromisoformat(entities.sleep_report_date(r)) <= anchor_date]
        report = daily.build_daily_report(rows, self._entities['recoveries'],
                                         self._entities['cycles'], self._physiology, [])
        if report.report_date != anchor_date:
            raise InvalidAnchor('No report morning for this anchor.')
        return compose_sleep_product(report, self._entities['sleeps'], today=self._today)

    def load_primary_sleep_history(self, anchor_date=None):
        return self.load_sleep_product(anchor_date).trends

    def load_training_context(self):
        return self._report.yesterday_training

    def load_snapshot_metadata(self):
        return self._metadata


class ProductRepository(Protocol):
    def read_snapshot(self) -> ReadSnapshot: ...


class CsvProductRepository:
    def __init__(self, data_dir=entities.DATA_DIR, clock: Callable[[], datetime] | None = None):
        self._root = Path(data_dir)
        self._clock = clock or (lambda: datetime.now().astimezone())

    def _digests(self):
        # Fixed allowlist: credentials, environment, generated files are excluded.
        # Only content digests enter the opaque ID, never paths or added entity IDs.
        return tuple(hashlib.sha256((self._root/name).read_bytes()).hexdigest()
                     if (self._root/name).exists() else 'absent' for name in INPUTS)

    def read_snapshot(self):
        try:
            with snapshot_read(self._root):
                before = self._digests()
                report = daily.load_daily_report(self._root)
                tables = {kind: entities.read_entities(self._root/(kind+'.csv'),kind)
                          for kind in ('sleeps','recoveries','cycles')}
                physiology = analysis.load_daily_metrics(self._root/'daily_metrics.csv')
                if before != self._digests():
                    raise SnapshotUnavailable()
                now = self._clock()
                if now.tzinfo is None or now.utcoffset() is None:
                    raise SnapshotUnavailable()
                report.sleep_product = replace(report.sleep_product,
                                               freshness=freshness(report.report_date, now.date()))
                snapshot_id = hashlib.sha256(('snapshot-v1|'+'|'.join(before)).encode()).hexdigest()
                metadata = SnapshotMetadata(snapshot_id, now.astimezone(timezone.utc), report.report_date)
                return ReadSnapshot(report, tables, physiology, metadata, now.date())
        except Exception:
            raise SnapshotUnavailable('Local snapshot unavailable.') from None
