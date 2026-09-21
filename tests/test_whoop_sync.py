"""Manual-sync integration using synthetic API records and temporary archives."""

import copy
from datetime import date, timedelta
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import requests
import whoop_entities as entities
import whoop_sync as sync
import whoop_workouts as workouts
from whoop_daily_report import load_daily_report
from test_whoop_history import Client, cycle, recovery, sleep


D = date(2026, 9, 19)


class SimulatedTermination(BaseException):
    """Bypass normal Exception rollback to model abrupt process termination."""
    pass


class SyntheticClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records['/activity/workout'] = []


def client(day=D, score=90, updated='2026-09-19T07:00:00Z'):
    start = str(day-timedelta(days=1))+'T22:00:00Z'
    row = sleep(start=start, end=str(day)+'T06:00:00Z')
    row.update(updated_at=updated)
    row['score']['sleep_performance_percentage'] = score
    return SyntheticClient([cycle(start=start)], [row], [recovery()])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(socket, 'socket', side_effect=AssertionError('No real network')))
        self.enterContext(patch.object(requests.Session, 'request', side_effect=AssertionError('No real HTTP')))
        self.enterContext(patch('whoop_auth.read_config', side_effect=AssertionError('No credentials')))
        self.enterContext(patch('whoop_auth.load_tokens', side_effect=AssertionError('No credentials')))
        self.enterContext(patch.object(sync, '_require_ignored'))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        entities.save_history(entities.collect_history(client(), D), self.root)
        workouts.save_workouts({}, self.root)

    def hashes(self):
        return {name: (self.root/name).read_bytes() for name in sync.FILES}

    def run_sync(self, source=None, today=D):
        source = source or client()
        return sync.sync_recent(self.root, today=today, client_factory=lambda: source)

    def test_recent_three_day_window_padding_and_no_full_history(self):
        source = client()
        result = self.run_sync(source)
        self.assertTrue(result['success'])
        self.assertEqual(sync.recent_window(D), (D-timedelta(days=2), D))
        for path, params, optional in source.calls:
            if params:
                self.assertEqual(params['start'].replace('+00:00','Z'), '2026-09-16T00:00:00Z')
                self.assertEqual(params['end'].replace('+00:00','Z'), '2026-09-21T00:00:00Z')
            self.assertNotIn('profile', path)
        self.assertTrue(any(path=='/activity/workout' for path,_,_ in source.calls))
        self.assertFalse((self.root/sync.TRANSACTION).exists())
        self.assertEqual({p.name for p in self.root.iterdir()}, set(sync.FILES)|{'.sync.lock'})

    def test_stale_incoming_uses_archive_version_in_daily(self):
        before = load_daily_report(self.root)
        self.assertTrue(self.run_sync(client(score=40, updated='2026-09-19T06:00:00Z'))['success'])
        after = load_daily_report(self.root)
        self.assertEqual(after.physiology, before.physiology)
        self.assertEqual(after.interpretation, before.interpretation)
        self.assertEqual(after.provenance['snapshot_consistency']['status'], 'matched')

    def test_history_rows_retained_without_unrelated_archive_leak(self):
        old = client(D-timedelta(days=10))
        old.records['/cycle'][0]['id'] = 2
        old.records['/activity/sleep'][0].update(id='old-sleep', cycle_id=2)
        old.records['/recovery'][0].update(cycle_id=2, sleep_id='old-sleep')
        batch = entities.collect_history(old, D)
        entities.save_history(batch, self.root)
        self.assertTrue(self.run_sync()['success'])
        rows = sync.analysis.load_daily_metrics(self.root/'daily_metrics.csv')
        self.assertEqual({r['sleep_id'] for r in rows}, {'old-sleep','sleep-1'})
        self.assertEqual(load_daily_report(self.root).provenance['snapshot_consistency']['status'], 'matched')

    def test_absent_recent_recovery_not_filled_from_old_archive(self):
        source = client()
        source.records['/recovery'] = []
        self.assertTrue(self.run_sync(source)['success'])
        rows = sync.analysis.load_daily_metrics(self.root/'daily_metrics.csv')
        self.assertEqual(rows[0]['recovery_present'], 'false')
        self.assertIsNone(rows[0]['recovery_score'])

    def test_fetch_failure_sanitized_preserves_data_and_no_raw_files(self):
        before = self.hashes()
        source = client()
        with patch.object(source, 'get', side_effect=RuntimeError('synthetic-secret-response')):
            result = self.run_sync(source)
        self.assertFalse(result['success'])
        self.assertNotIn('synthetic-secret-response', str(result))
        self.assertEqual(before, self.hashes())
        self.assertFalse((self.root/sync.TRANSACTION).exists())

    def test_relationship_conflict_fails_before_publication(self):
        before = self.hashes()
        source = client()
        source.records['/activity/sleep'][0]['nap'] = True
        self.assertFalse(self.run_sync(source)['success'])
        self.assertEqual(before, self.hashes())

    def test_every_publication_failure_restores_all_files(self):
        original = sync.os.replace
        for position in range(1, 6):
            with self.subTest(position=position):
                before = self.hashes()
                count = 0
                def failing_replace(src, dest):
                    nonlocal count
                    if Path(src).parent.name == 'staged' and Path(dest).parent == self.root:
                        count += 1
                        if count == position:
                            raise OSError('synthetic disk failure')
                    return original(src, dest)
                with patch.object(sync.os, 'replace', side_effect=failing_replace):
                    result = self.run_sync(client(score=77, updated='2026-09-19T08:00:00Z'))
                self.assertEqual(count, position)
                self.assertFalse(result['success'])
                self.assertEqual(before, self.hashes())
                self.assertFalse((self.root/sync.TRANSACTION).exists())

    def test_post_write_validation_failure_rolls_back(self):
        before = self.hashes()
        real = load_daily_report
        def fail_after_publish(path):
            if Path(path) == self.root:
                raise ValueError('synthetic validation failure')
            return real(path)
        with patch('whoop_daily_report.load_daily_report', side_effect=fail_after_publish) as loader:
            result = self.run_sync(client(score=77))
        self.assertTrue(any(Path(call.args[0]) == self.root for call in loader.call_args_list))
        self.assertFalse(result['success'])
        self.assertEqual(before, self.hashes())

    def test_interrupted_transaction_blocks_reads_and_recovers(self):
        before = self.hashes()
        transaction = self.root/sync.TRANSACTION
        (transaction/'before').mkdir(parents=True)
        for name in sync.FILES:
            shutil.copyfile(self.root/name, transaction/'before'/name)
        sync._write_manifest(transaction, {'state':'publishing',
                             'original':{name:sync._hash(self.root/name) for name in sync.FILES}})
        (self.root/'sleeps.csv').write_text('interrupted synthetic file', encoding='utf-8')
        with self.assertRaises(sync.SyncError), sync.local_read(self.root):
            pass
        source = client()
        with patch.object(source,'get',side_effect=RuntimeError('no fetch')):
            self.assertFalse(self.run_sync(source)['success'])
        self.assertEqual(before,self.hashes())

    def test_changed_classification_sidecar_prevents_publish(self):
        before = self.hashes()
        original = entities.collect_window
        def change(*args):
            batch = original(*args)
            (self.root/'workout_classifications.csv').write_text('external synthetic change',encoding='utf-8')
            return batch
        with patch.object(entities,'collect_window',side_effect=change):
            self.assertFalse(self.run_sync()['success'])
        self.assertEqual(before,self.hashes())

    def test_unresolved_duplicates_reject_before_writes(self):
        source = client()
        other = copy.deepcopy(source.records['/activity/sleep'][0])
        other['score']['sleep_performance_percentage'] = 50
        source.records['/activity/sleep'].append(other)
        before = self.hashes()
        self.assertFalse(self.run_sync(source)['success'])
        self.assertEqual(before,self.hashes())

    def test_process_lock_prevents_a_second_sync_before_fetch(self):
        before = self.hashes()
        lock = sync._lock_process(self.root)
        source = client()
        try:
            self.assertFalse(self.run_sync(source)['success'])
            self.assertEqual(source.calls, [])
            self.assertEqual(before, self.hashes())
        finally:
            lock.close()

    def test_external_transaction_start_invalidates_local_read(self):
        transaction = self.root / sync.TRANSACTION
        with self.assertRaises(sync.SyncError):
            with sync.local_read(self.root):
                transaction.mkdir()

    def prepare_recovery(self, state='publishing'):
        transaction = self.root / sync.TRANSACTION
        (transaction/'before').mkdir(parents=True)
        manifest = {'state':state, 'original':{name:sync._hash(self.root/name) for name in sync.FILES}}
        for name, digest in manifest['original'].items():
            if digest is not None:
                shutil.copyfile(self.root/name, transaction/'before'/name)
        sync._write_manifest(transaction, manifest)
        return transaction, manifest

    def test_manifest_is_atomic_and_partial_document_never_active(self):
        transaction, manifest = self.prepare_recovery('prepared')
        active = (transaction/'manifest.json').read_bytes()
        def partial_dump(value, file, **kwargs):
            file.write('{')
        with patch.object(sync.json, 'dump', side_effect=partial_dump):
            with self.assertRaises(ValueError):
                sync._state(transaction, manifest, 'publishing')
        self.assertEqual((transaction/'manifest.json').read_bytes(), active)
        sync._recover(self.root, transaction)
        self.assertFalse(transaction.exists())

    def test_crash_before_manifest_publication_leaves_originals(self):
        before = self.hashes()
        real_replace = sync.os.replace
        def crash(src, dest):
            if Path(src).name == 'manifest.tmp':
                raise SimulatedTermination()
            return real_replace(src, dest)
        with patch.object(sync.os, 'replace', side_effect=crash), self.assertRaises(SimulatedTermination):
            self.run_sync()
        transaction = self.root/sync.TRANSACTION
        self.assertFalse((transaction/'manifest.json').exists())
        sync._recover(self.root, transaction)
        self.assertEqual(before,self.hashes())

    def test_crash_before_first_and_after_first_two_publications(self):
        for position in (0,1,2):
            with self.subTest(position=position):
                before = self.hashes()
                real_replace = sync.os.replace
                count = 0
                def crash(src,dest):
                    nonlocal count
                    publication = Path(src).parent.name == 'staged' and Path(dest).parent == self.root
                    if publication and position == 0:
                        raise SimulatedTermination()
                    value = real_replace(src,dest)
                    if publication:
                        count += 1
                        if count == position:
                            raise SimulatedTermination()
                    return value
                with patch.object(sync.os,'replace',side_effect=crash), self.assertRaises(SimulatedTermination):
                    self.run_sync(client(score=77,updated='2026-09-19T08:00:00Z'))
                self.assertEqual(count,position)
                transaction = self.root/sync.TRANSACTION
                sync._recover(self.root,transaction)
                sync._recover(self.root,transaction)
                self.assertEqual(before,self.hashes())

    def test_crash_during_rollback_is_retryable(self):
        before = self.hashes()
        transaction, _ = self.prepare_recovery()
        (self.root/'sleeps.csv').write_text('synthetic partial publication',encoding='utf-8')
        real_replace = sync.os.replace
        def crash(src,dest):
            result = real_replace(src,dest)
            if Path(src).name == 'restore-cycles.csv':
                raise SimulatedTermination()
            return result
        with patch.object(sync.os,'replace',side_effect=crash), self.assertRaises(SimulatedTermination):
            sync._recover(self.root,transaction)
        self.assertEqual(json.loads((transaction/'manifest.json').read_text())['state'],'rollback_required')
        sync._recover(self.root,transaction)
        self.assertEqual(before,self.hashes())

    def test_crash_during_cleanup_uses_terminal_state_not_deleted_backups(self):
        before = self.hashes()
        transaction, _ = self.prepare_recovery()
        (self.root/'sleeps.csv').write_text('synthetic partial publication',encoding='utf-8')
        real_rmtree = sync.shutil.rmtree
        def crash(path,*args,**kwargs):
            result = real_rmtree(path,*args,**kwargs)
            if Path(path).name == 'before':
                raise SimulatedTermination()
            return result
        with patch.object(sync.shutil,'rmtree',side_effect=crash), self.assertRaises(SimulatedTermination):
            sync._recover(self.root,transaction)
        self.assertEqual(json.loads((transaction/'manifest.json').read_text())['state'],'rollback_complete')
        self.assertFalse((transaction/'before').exists())
        with patch.object(sync,'_restore',side_effect=AssertionError('Must not restore again')):
            sync._recover(self.root,transaction)
            sync._recover(self.root,transaction)
        self.assertEqual(before,self.hashes())

    def test_corrupt_backup_preserves_artifacts_before_any_restoration(self):
        transaction, _ = self.prepare_recovery()
        before = self.hashes()
        (transaction/'before'/'sleeps.csv').write_text('synthetic corruption',encoding='utf-8')
        with self.assertRaises(sync.SyncError):
            sync._recover(self.root,transaction)
        self.assertEqual(before,self.hashes())
        self.assertTrue((transaction/'manifest.json').exists())

    def test_restored_hash_mismatch_retains_backups_and_retries(self):
        before = self.hashes()
        transaction, _ = self.prepare_recovery()
        real_replace = sync.os.replace
        def corrupt(src,dest):
            result = real_replace(src,dest)
            if Path(src).name == 'restore-sleeps.csv':
                Path(dest).write_text('synthetic failed restoration',encoding='utf-8')
            return result
        with patch.object(sync.os,'replace',side_effect=corrupt), self.assertRaises(sync.SyncError):
            sync._recover(self.root,transaction)
        self.assertTrue((transaction/'before'/'sleeps.csv').exists())
        self.assertEqual(json.loads((transaction/'manifest.json').read_text())['state'],'rollback_required')
        sync._recover(self.root,transaction)
        self.assertEqual(before,self.hashes())

    def test_originally_absent_file_is_absent_after_recovery(self):
        (self.root/'workouts.csv').unlink()
        transaction, manifest = self.prepare_recovery()
        self.assertIsNone(manifest['original']['workouts.csv'])
        (self.root/'workouts.csv').write_text('synthetic newly published file',encoding='utf-8')
        sync._recover(self.root,transaction)
        self.assertFalse((self.root/'workouts.csv').exists())

    def test_committed_cleanup_never_restores_old_data(self):
        transaction, _ = self.prepare_recovery('committed')
        shutil.rmtree(transaction/'before')
        before = self.hashes()
        with patch.object(sync,'_restore',side_effect=AssertionError('Committed data must survive')):
            sync._recover(self.root,transaction)
        self.assertEqual(before,self.hashes())

    def test_actual_process_exit_after_file_two_recovers(self):
        before = self.hashes()
        code = '''
import sys, os, socket
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, 'tests')
import requests
from test_whoop_sync import client, D
import whoop_sync as sync
root = Path(sys.argv[1])
real = os.replace
count = 0
def terminate(src, dest):
    global count
    result = real(src, dest)
    if Path(src).parent.name == 'staged' and Path(dest).parent == root:
        count += 1
        if count == 2:
            os._exit(23)
    return result
with patch.object(sync, '_require_ignored'), patch.object(sync.os, 'replace', side_effect=terminate), patch.object(socket, 'socket', side_effect=AssertionError('No network')), patch.object(requests.Session, 'request', side_effect=AssertionError('No HTTP')):
    sync.sync_recent(root, today=D, client_factory=lambda:client(score=77,updated='2026-09-19T08:00:00Z'))
'''
        result = subprocess.run([sys.executable,'-B','-c',code,str(self.root)],
                                cwd=Path(__file__).resolve().parents[1],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,23)
        lock = sync._lock_process(self.root)  # OS released the dead process lock.
        try:
            sync._recover(self.root,self.root/sync.TRANSACTION)
        finally:
            lock.close()
        self.assertEqual(before,self.hashes())

    def test_rotated_token_survives_health_publication_failure(self):
        import whoop_auth as auth
        from whoop_fetch import WhoopClient
        token_file = self.root/'synthetic_tokens.json'
        auth.save_tokens({'access_token':'synthetic-old-access','refresh_token':'synthetic-old-refresh'},token_file)
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.json.return_value = {'access_token':'synthetic-new-access','refresh_token':'synthetic-new-refresh','expires_in':3600}
        before = self.hashes()
        def factory():
            WhoopClient(token_file).refresh()
            return client(score=77,updated='2026-09-19T08:00:00Z')
        real_replace = sync.os.replace
        def fail(src,dest):
            if Path(src).parent.name == 'staged' and Path(dest).name == 'sleeps.csv':
                raise OSError('synthetic publication failure')
            return real_replace(src,dest)
        config = {'WHOOP_CLIENT_ID':'synthetic-id','WHOOP_CLIENT_SECRET':'synthetic-secret'}
        with patch.object(auth,'read_config',return_value=config), patch.object(requests,'post',return_value=response) as post, patch.object(sync.os,'replace',side_effect=fail):
            result = sync.sync_recent(self.root,today=D,client_factory=factory)
        self.assertFalse(result['success'])
        self.assertEqual(json.loads(token_file.read_text())['refresh_token'],'synthetic-new-refresh')
        self.assertEqual(post.call_count,1)
        self.assertEqual(before,self.hashes())

    def test_cross_midnight_offset_transition_and_recent_window(self):
        sleeps = [sleep(2,'east','2026-09-16T09:00:00Z','2026-09-16T18:00:00Z','+14:00'),
                  sleep(3,'west','2026-09-18T08:00:00Z','2026-09-18T17:00:00Z','-10:00'),
                  sleep(4,'home','2026-09-18T22:00:00Z','2026-09-19T06:00:00Z','+01:00')]
        cycles = [cycle(s['cycle_id'],s['start'],offset=s['timezone_offset']) for s in sleeps]
        source = SyntheticClient(cycles,sleeps,[recovery(s['cycle_id'],s['id']) for s in sleeps])
        self.assertTrue(self.run_sync(source)['success'])
        rows = {r['sleep_id']:r for r in sync.analysis.load_daily_metrics(self.root/'daily_metrics.csv')}
        for sid,gap,offset in (('east',2,'+14:00'),('west',1,'-10:00'),('home',0,'+01:00')):
            self.assertEqual(rows[sid]['report_date'],D-timedelta(days=gap))
            self.assertEqual(rows[sid]['sleep_timezone_offset'],offset)
        for path,params,_ in source.calls:
            if params:
                self.assertEqual(params['start'].replace('+00:00','Z'),'2026-09-16T00:00:00Z')
                self.assertEqual(params['end'].replace('+00:00','Z'),'2026-09-21T00:00:00Z')


if __name__ == '__main__':
    unittest.main()
