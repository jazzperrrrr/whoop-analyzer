"""Product service contracts, entirely synthetic and offline."""

import ast
from dataclasses import asdict, fields
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from product_fixtures import SyntheticInputs, D, NOW, rewrite
import whoop_daily_report as daily
from whoop_product.repository import CsvProductRepository, SnapshotUnavailable, InvalidAnchor
from whoop_product.today_service import get_today
from whoop_product.sleep_service import get_sleep
from whoop_product.trend_service import get_trend


# Separate interpreter: no inherited thread lock, no network, synthetic files only.
TRANSIENT_WRITER = r'''
import csv, io, sys
from pathlib import Path
from whoop_sync import _lock_process
root = Path(sys.argv[1])
try:
    lock = _lock_process(root)
except OSError:
    print('blocked', flush=True)
    sys.exit(0)
path = root/'sleeps.csv'
original = path.read_bytes()
try:
    rows = list(csv.DictReader(io.StringIO(original.decode('utf-8-sig'))))
    rows[0]['total_rem_sleep_time_ms'] = '9876'
    with path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print('published', flush=True)
    sys.stdin.readline()
finally:
    path.write_bytes(original)
    lock.close()
'''


SNAPSHOT_READER = r'''
import sys
from pathlib import Path
from whoop_sync import snapshot_read
with snapshot_read(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.readline()
'''


class ProductTests(SyntheticInputs,unittest.TestCase):
    def setUp(self):
        self.setup_inputs()

    def test_today_exact_domain_semantics(self):
        domain=daily.load_daily_report(self.root)
        result=get_today(self.repo)
        self.assertEqual(result.report_date,domain.report_date)
        for name,p in ((f.name, getattr(result.data.physiology, f.name)) for f in fields(result.data.physiology)):
            self.assertEqual(p.metric.value,domain.physiology[name]['value'])
            for b in p.baselines:
                original=domain.baseline[name][b.window_days]
                self.assertEqual((b.average,b.difference,b.observed_days,b.eligible),
                                 (original['average'],original['difference'],original['days_available'],original['eligible']))
                self.assertEqual(b.percentage_deviation,original['percentage_deviation'])
        self.assertEqual(result.data.interpretation.overall_state, domain.interpretation['overall_state'])
        for name, signal in asdict(result.data.interpretation.signals).items():
            self.assertEqual(signal['state'], domain.interpretation['signals'][name]['state'])
            self.assertEqual(signal['reason_codes'], domain.interpretation['signals'][name]['reason_codes'])
        self.assertEqual(result.data.last_sleep.actual_sleep.value,domain.sleep_product.metrics['actual_sleep_ms'].value)
        self.assertEqual(result.data.yesterday_training.record_count,domain.yesterday_training['record_count'])

    def test_sleep_exact_values_origins_and_separate_naps(self):
        domain=daily.load_daily_report(self.root).sleep_product
        product=get_sleep(self.repo).data
        for name,metric in ((f.name, getattr(product.metrics, f.name)) for f in fields(product.metrics)):
            self.assertEqual(metric.value,domain.metrics[name].value)
            self.assertEqual(metric.origin,domain.metrics[name].origin)
        self.assertEqual(product.metrics.nap_adjustment_ms.value,-1500)
        self.assertEqual(len(product.naps),1)
        self.assertEqual(product.naps[0].actual_sleep.value,7000)
        self.assertEqual(product.metrics.rem_ms.value,2500)
        self.assertIsNotNone(product.timing.local_start.utcoffset())

    def test_missing_zero_pending_and_conflict_are_distinct(self):
        rewrite(self.root,'sleeps',lambda rows:rows[0].update(total_rem_sleep_time_ms='0',respiratory_rate=''))
        product=get_sleep(self.repo).data
        self.assertEqual(product.metrics.rem_ms.value,0)
        self.assertEqual(product.metrics.rem_ms.availability,'available')
        self.assertEqual(product.metrics.respiratory_rate.availability,'missing')
        from whoop_sleep import EXTENDED_FIELDS
        rewrite(self.root,'sleeps',lambda rows:rows[0].update(score_state='PENDING_SCORE',**{f:'' for f in EXTENDED_FIELDS}))
        # No current daily row means unverified, not an explicit conflicting score state.
        self.assertEqual(get_sleep(self.repo).data.metrics.actual_sleep_ms.availability,'pending')
        rewrite(self.root,'sleeps',lambda rows:rows.append(dict(rows[0],sleep_id='synthetic-duplicate')))
        self.assertEqual(get_sleep(self.repo).data.metrics.actual_sleep_ms.availability,'withheld')

    def test_explicit_snapshot_conflict_is_withheld(self):
        def current(rows):
            row=dict(rows[0],report_date=D.isoformat(),cycle_id=str(D.toordinal()),sleep_id='synthetic-sleep-'+str(D),sleep_performance='1')
            rows.append(row)
        rewrite(self.root,'daily_metrics',current)
        result=get_today(self.repo)
        self.assertEqual(result.data.physiology.sleep_performance.metric.availability,'withheld')
        self.assertIn('conflicting_measurements',result.data.physiology.sleep_performance.metric.reason_codes)
        point=get_trend(self.repo,'sleep_performance').data.daily_points[-1]
        self.assertEqual(point.metric.availability,'withheld')

    def test_pending_history_and_naps_do_not_become_missing(self):
        from whoop_sleep import EXTENDED_FIELDS
        def pending(rows):
            for row in (rows[0],rows[1],rows[-1]):
                row.update(score_state='PENDING_SCORE',**{f:'' for f in EXTENDED_FIELDS})
        rewrite(self.root,'sleeps',pending)
        result=get_sleep(self.repo).data
        self.assertEqual(result.naps[0].actual_sleep.availability,'pending')
        points=get_trend(self.repo,'actual_sleep').data.daily_points
        self.assertEqual(points[-1].metric.availability,'pending')
        self.assertEqual(next(p for p in points if p.report_date==D-timedelta(days=1)).metric.availability,'pending')

    def test_mixed_historical_performance_availability_preserves_numeric_baseline(self):
        from test_whoop_daily_report import observation, report, entities
        from whoop_sleep_product import compose_sleep_product
        from whoop_product.trend_service import compose_trend
        day = D-timedelta(days=1)
        cases = (
            (('PENDING_SCORE', None), ('SCORED', None), 'pending', None, 0),
            (('PENDING_SCORE', None), ('PENDING_SCORE', None), 'pending', None, 0),
            (('SCORED', 60), ('PENDING_SCORE', None), 'available', 60, 1),
            (('SCORED', 0), ('PENDING_SCORE', None), 'available', 0, 1),
            (('SCORED', None), ('UNSCORABLE', None), 'missing', None, 0),
            (('SCORED', 60), ('SCORED', 100), 'available', 80, 1),
        )
        for a, b, availability, value, observed in cases:
            with self.subTest(a=a, b=b):
                rows = [observation(day, sleep_id=f'synthetic-{i}', cycle_id=f'cycle-{i}',
                                    sleep_score_state=state, sleep_performance=number)
                        for i, (state, number) in enumerate((a, b))]
                domain = report(physiology=rows)
                product = compose_sleep_product(domain, entities()['sleeps'], today=NOW.date())
                trend = compose_trend(product, 'sleep_performance', 14)
                point = next(p for p in trend.daily_points if p.report_date==day)
                self.assertEqual((point.metric.availability, point.metric.value), (availability, value))
                self.assertEqual(point.metric.reason_codes, [] if value is not None else
                                 ['sleep_not_scored' if availability=='pending' else 'historical_value_unavailable'])
                window = trend.prior_windows[0]
                self.assertEqual((window.average, window.observed_days), (value, observed))
                self.assertEqual(window.average, domain.baseline['sleep_performance'][14]['average'])

    def test_trend_boundaries_gaps_offsets_and_performance_parity(self):
        daily_report=daily.load_daily_report(self.root)
        for window,observed in ((7,2),(14,3),(30,4)):
            trend=get_trend(self.repo,'actual_sleep',window_days=window).data
            b=trend.prior_windows[0]
            self.assertEqual(b.observed_days,observed)
            self.assertEqual((b.window_start,b.window_end),(D-timedelta(days=window),D-timedelta(days=1)))
            self.assertFalse(b.includes_anchor)
            self.assertLess(len(trend.daily_points),window+1)
            self.assertTrue(all(p.recorded_offsets==['+01:00'] for p in trend.daily_points))
            performance=get_trend(self.repo,'sleep_performance',window_days=window).data.prior_windows[0]
            self.assertEqual(performance.average,daily_report.baseline['sleep_performance'][window]['average'])
            self.assertEqual(performance.observed_days,daily_report.baseline['sleep_performance'][window]['days_available'])

    def test_excluded_current_identity_cannot_supply_historical_pending_state(self):
        from test_whoop_daily_report import observation, report, entities
        from whoop_sleep_product import compose_sleep_product
        from whoop_product.trend_service import compose_trend
        source = entities(score_state='PENDING_SCORE', sleep_performance=None)
        current_id = source['sleeps'][0]['sleep_id']
        day = D-timedelta(days=1)
        excluded = observation(day, sleep_id=current_id, sleep_score_state='PENDING_SCORE',
                               sleep_performance=None, sleep_timezone_offset='-05:00')
        independent = observation(day, sleep_id='independent-synthetic-sleep', cycle_id='independent-cycle',
                                  sleep_performance=None)
        domain = report(source=source, physiology=[excluded, independent])
        eligibility = {r['sleep_id']:r['baseline_eligible']['sleep_performance']
                       for r in domain.baseline_observations}
        self.assertEqual(eligibility, {current_id:False, independent['sleep_id']:True})
        product = compose_sleep_product(domain, source['sleeps'], today=NOW.date())
        trend = compose_trend(product, 'sleep_performance', 14)
        point = next(p for p in trend.daily_points if p.report_date==day)
        self.assertEqual(point.metric.availability, 'missing')
        self.assertEqual(point.metric.reason_codes, ['historical_value_unavailable'])
        self.assertIsNone(point.metric.value)
        self.assertEqual(point.recorded_offsets, ['+01:00'])
        self.assertEqual(trend.prior_windows[0].observed_days, 0)
        self.assertIsNone(trend.prior_windows[0].average)
        # Altering an excluded record's score/state changes neither numbers nor
        # readiness. Only the independent record participates in availability.
        reference = report(source=source, physiology=[dict(excluded, sleep_score_state='SCORED',
                                                          sleep_performance=100), independent])
        self.assertEqual(domain.baseline, reference.baseline)
        self.assertEqual(domain.interpretation, reference.interpretation)
        # If the excluded identity is the only row, it cannot invent even a
        # missing historical point or contribute an availability reason.
        only_excluded = report(source=source, physiology=[excluded])
        product = compose_sleep_product(only_excluded, source['sleeps'], today=NOW.date())
        self.assertNotIn(day, [p.report_date for p in compose_trend(product, 'sleep_performance').daily_points])

    def test_historical_anchor_uses_existing_selector_without_substitution(self):
        result=get_trend(self.repo,'actual_sleep',D-timedelta(days=7),7)
        self.assertEqual(result.report_date,D-timedelta(days=7))
        self.assertEqual(result.latest_available_morning,D)
        with self.assertRaises(InvalidAnchor):
            get_trend(self.repo,'actual_sleep',D-timedelta(days=2),7)

    def test_snapshot_id_shared_deterministic_and_clock_independent(self):
        today=get_today(self.repo)
        sleep=get_sleep(self.repo)
        self.assertEqual(today.snapshot_id,sleep.snapshot_id)
        later=get_today(CsvProductRepository(self.root,clock=lambda:NOW+timedelta(days=1)))
        self.assertEqual(today.snapshot_id,later.snapshot_id)
        self.assertNotEqual(today.generated_at,later.generated_at)
        self.assertEqual(today.generated_at.utcoffset(),timedelta(0))
        self.assertIsNone(today.last_successful_sync_at)
        self.assertFalse(today.collection_freshness.collection_time_known)

    def test_snapshot_excludes_secrets_and_changes_with_relevant_input(self):
        before=get_today(self.repo).snapshot_id
        (self.root/'.env').write_text('synthetic-secret',encoding='utf-8')
        (self.root/'whoop_tokens.json').write_text('synthetic-token',encoding='utf-8')
        original=Path.open
        def no_credentials(path,*args,**kwargs):
            if path.name in ('.env','whoop_tokens.json'):
                raise AssertionError('Credentials must not be read')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',no_credentials):
            self.assertEqual(before,get_sleep(self.repo).snapshot_id)
        rewrite(self.root,'sleeps',lambda rows:rows[0].update(total_rem_sleep_time_ms='2600'))
        self.assertNotEqual(before,get_today(self.repo).snapshot_id)

    def test_read_only_and_private_fields_not_exposed(self):
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        public=json.dumps(asdict(get_today(self.repo)),default=str)
        for forbidden in ('synthetic-sleep','sleep_id','cycle_id','workout_id','source_updated_at',str(self.root)):
            self.assertNotIn(forbidden,public)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_snapshot_change_and_pending_transaction_rejected(self):
        with patch.object(self.repo,'_digests',side_effect=[('before',),('after',)]):
            with self.assertRaises(SnapshotUnavailable):
                get_today(self.repo)
        (self.root/'.sync-pending').mkdir()
        with self.assertRaises(SnapshotUnavailable):
            get_sleep(self.repo)

    def transient_writer(self):
        process = subprocess.Popen([sys.executable, '-B', '-c', TRANSIENT_WRITER, str(self.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True)
        def finish():
            if process.poll() is None:
                process.communicate('\n', timeout=15)
        self.addCleanup(finish)
        return process

    def test_cross_process_a_fingerprint_cannot_read_transient_b_then_rollback_a(self):
        original = get_sleep(self.repo)
        digests = self.repo._digests
        loader = daily.load_daily_report
        writer = None
        state = None

        def fingerprint():
            nonlocal writer, state
            result = digests()  # Fingerprint A before the attempted publication.
            if writer is None:
                writer = self.transient_writer()
                state = writer.stdout.readline().strip()
                self.assertIn(state, ('blocked', 'published'))
            return result

        def load_then_rollback(*args):
            result = loader(*args)
            # Without the OS reader lock this loads B, rolls back A, then passes
            # the original before/after fingerprint check while returning B.
            writer.communicate('\n', timeout=15)
            return result

        with patch.object(self.repo, '_digests', side_effect=fingerprint), \
                patch.object(daily, 'load_daily_report', side_effect=load_then_rollback):
            result = get_sleep(self.repo)
        self.assertEqual(state, 'blocked')
        self.assertEqual(writer.returncode, 0)
        self.assertEqual(result.snapshot_id, original.snapshot_id)
        self.assertEqual(result.data, original.data)

    def test_cross_process_active_transient_publication_fails_then_rollback_restores_id(self):
        original = get_today(self.repo)
        writer = self.transient_writer()
        self.assertEqual(writer.stdout.readline().strip(), 'published')
        with self.assertRaises(SnapshotUnavailable):
            get_today(self.repo)
        with self.assertRaises(SnapshotUnavailable):
            get_sleep(self.repo)
        writer.communicate('\n', timeout=15)
        self.assertEqual(writer.returncode, 0)
        self.assertEqual(get_today(self.repo).snapshot_id, original.snapshot_id)
        self.assertEqual(get_sleep(self.repo).snapshot_id, original.snapshot_id)

    def test_absent_coordination_lock_fails_closed_without_creating_it(self):
        lock = self.root/'.sync.lock'
        lock.unlink()
        with self.assertRaises(SnapshotUnavailable):
            get_today(self.repo)
        self.assertFalse(lock.exists())

    def test_terminated_snapshot_reader_releases_os_lock_without_mutation(self):
        from whoop_sync import _lock_process
        before = {p.name:p.read_bytes() for p in self.root.iterdir()}
        original = get_today(self.repo)
        child = subprocess.Popen([sys.executable, '-B', '-c', SNAPSHOT_READER, str(self.root)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'locked')
            # An exclusive sync operation must see the independent reader's lock.
            with self.assertRaises(OSError):
                lock = _lock_process(self.root)
                lock.close()
            # On Windows this uses TerminateProcess: no Python finally/close.
            child.kill()
            child.communicate(timeout=15)
            self.assertNotEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=15)
        self.assertEqual(get_today(self.repo).snapshot_id, original.snapshot_id)
        self.assertEqual(get_sleep(self.repo).snapshot_id, original.snapshot_id)
        lock = _lock_process(self.root)
        lock.close()
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_product_and_domain_have_no_framework_imports(self):
        root=Path(__file__).resolve().parents[1]
        for path in [*(root/'whoop_product').glob('*.py'),*root.glob('whoop_*.py')]:
            tree=ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                self.assertFalse(any(n.split('.')[0] in ('fastapi','starlette','pydantic') for n in names),path.name)
