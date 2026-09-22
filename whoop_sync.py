"""Explicit manual sync only. Stage normalized archives, then publish with rollback."""

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import threading

from whoop_auth import PROJECT_DIR
import whoop_analysis as analysis
import whoop_entities as entities
from whoop_fetch import WhoopClient
import whoop_workouts as workouts


DATA_LOCK = threading.RLock()
FILES = ('cycles.csv', 'sleeps.csv', 'recoveries.csv', 'daily_metrics.csv', 'workouts.csv')
TRANSACTION = '.sync-pending'
FAILURE = 'WHOOP sync failed. Local data was retained or restored; no automatic retry.'


class SyncError(Exception):
    pass


def _lock_process(directory, *, read_only=False):
    """Read-only snapshot locks and sync writers use the same OS-released byte.

    Readers never create the coordination file. Missing/busy locks fail closed.
    The sync owner holds its exclusive lock through recovery and publication.
    """
    lock = (directory / '.sync.lock').open('rb' if read_only else 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBRLCK if read_only else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), (fcntl.LOCK_SH if read_only else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        return lock
    except Exception:
        lock.close()
        raise


@contextmanager
def snapshot_read(directory):
    """Bounded cross-process acquisition, released before product serialization.

    The manifest still rejects interrupted publications after the writer dies.
    A surviving writer cannot publish or roll back while this lock is held.
    """
    lock = _lock_process(Path(directory), read_only=True)
    try:
        with local_read(directory):
            yield
    finally:
        lock.close()


@contextmanager
def local_read(directory):
    with DATA_LOCK:
        if (Path(directory) / TRANSACTION).exists():
            raise SyncError('An interrupted sync needs recovery. Use Sync WHOOP to retry recovery.')
        yield
        if (Path(directory) / TRANSACTION).exists():
            raise SyncError('Another dashboard process started a sync during this read.')


def recent_window(today=None):
    """Three reporting dates, including today; UTC padding matches history collection."""
    last = today or date.today()
    return last - timedelta(days=2), last


def _require_ignored(directory):
    # Do not turn an arbitrary --data-dir into a location for private exports.
    if directory.resolve() != entities.DATA_DIR.resolve():
        raise SyncError('Sync is available only for this repository data directory.')
    result = subprocess.run(['git', 'check-ignore', '--stdin'], cwd=PROJECT_DIR,
                            input='\n'.join('data/' + name for name in (*FILES, '.sync.lock', TRANSACTION + '/manifest.json')) + '\n',
                            text=True, capture_output=True, check=False)
    if result.returncode != 0 or len(result.stdout.splitlines()) != len(FILES) + 2:
        raise SyncError('Private sync files must remain Git-ignored.')


STATES = {'prepared', 'publishing', 'rollback_required', 'rollback_complete', 'committed'}


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _validate_manifest(manifest):
    if (not isinstance(manifest, dict) or set(manifest) != {'state', 'original'}
            or manifest['state'] not in STATES or not isinstance(manifest['original'], dict)
            or set(manifest['original']) != set(FILES)):
        raise SyncError('Invalid recovery manifest.')
    for digest in manifest['original'].values():
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                                   or any(c not in '0123456789abcdef' for c in digest)):
            raise SyncError('Invalid recovery digest.')
    return manifest


def _write_manifest(transaction, manifest):
    """Only a complete, fsynced, validated document becomes active state."""
    _validate_manifest(manifest)
    temporary = transaction / 'manifest.tmp'
    with temporary.open('w', encoding='utf-8') as file:
        json.dump(manifest, file, sort_keys=True)
        file.flush()
        os.fsync(file.fileno())
    parsed = _validate_manifest(json.loads(temporary.read_text(encoding='utf-8')))
    if parsed != manifest:
        raise SyncError('Recovery state verification failed.')
    os.replace(temporary, transaction / 'manifest.json')


def _state(transaction, manifest, state):
    updated = dict(manifest, state=state)
    _write_manifest(transaction, updated)
    return updated


def _cleanup(transaction):
    # Keep the terminal state until all potentially partially deleted backups
    # are gone. A crash during cleanup therefore never requests another rollback.
    for path in transaction.iterdir():
        if path.name == 'manifest.json':
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    (transaction / 'manifest.json').unlink(missing_ok=True)
    transaction.rmdir()


def _restore(directory, transaction, manifest):
    originals = manifest['original']
    # Verify every backup before touching even the first live file.
    for name, digest in originals.items():
        if digest is not None and _hash(transaction / 'before' / name) != digest:
            raise SyncError('Recovery backup verification failed.')
    for name in FILES:
        path = directory / name
        if originals[name] is not None:
            # Copy backups so they remain available if restoration itself fails.
            restore = transaction / ('restore-' + name)
            shutil.copyfile(transaction / 'before' / name, restore)
            with restore.open('r+b') as file:
                os.fsync(file.fileno())
            os.replace(restore, path)
        else:
            path.unlink(missing_ok=True)
    if any(_hash(directory / name) != digest for name, digest in originals.items()):
        raise SyncError('Restored data verification failed.')


def _recover(directory, transaction):
    if not transaction.exists():
        return
    manifest_path = transaction / 'manifest.json'
    if manifest_path.exists():
        manifest = _validate_manifest(json.loads(manifest_path.read_text(encoding='utf-8')))
        if manifest['state'] not in ('rollback_complete', 'committed'):
            manifest = _state(transaction, manifest, 'rollback_required')
            _restore(directory, transaction, manifest)
            _state(transaction, manifest, 'rollback_complete')
    # No manifest means publication never started, or terminal cleanup finished.
    _cleanup(transaction)


def _daily_collection(collection, archived, old_daily):
    """Retain existing snapshot identities, never add unrelated archive rows."""
    incoming = collection['entities']
    previous = {r['sleep_id']: r for r in old_daily}
    sleep_ids = {(sid,) for sid in previous}
    for key in incoming['sleeps']:
        row = archived['sleeps'][key]
        day = date.fromisoformat(entities.sleep_report_date(row))
        if collection['first'] <= day <= collection['last']:
            sleep_ids.add(key)
    selected = {kind: {} for kind in ('sleeps', 'cycles', 'recoveries')}
    for key in sleep_ids:
        row = archived['sleeps'][key]
        selected['sleeps'][key] = row
        cid = (row['cycle_id'],)
        old = previous.get(row['sleep_id'], {})
        for kind, flag in (('cycles', 'cycle_present'), ('recoveries', 'recovery_present')):
            # An incoming sleep uses this collection's availability, not an old recovery.
            present = cid in incoming[kind] if key in incoming['sleeps'] else old.get(flag) == 'true'
            if present and cid in archived[kind]:
                selected[kind][cid] = archived[kind][cid]
    days = [date.fromisoformat(entities.sleep_report_date(r)) for r in selected['sleeps'].values()]
    return {'first': min(days, default=collection['first']), 'last': max(days, default=collection['last']),
            'entities': selected}


def sync_recent(directory=entities.DATA_DIR, today=None, client_factory=None):
    """Only POST /sync calls this in production; GET/render paths never instantiate clients.

    Multi-file publication is serialized for dashboard readers. Each replacement is
    atomic. Backups and a durable manifest permit rollback after an exception or an
    interrupted process. Other collectors must not run concurrently.
    """
    directory = Path(directory)
    if not DATA_LOCK.acquire(blocking=False):
        return {'success': False, 'message': 'A local update is already running.'}
    transaction = directory / TRANSACTION
    original_hashes = {}
    owned = False
    process_lock = None
    try:
        _require_ignored(directory)
        if directory.is_symlink() or any((directory / name).is_symlink() for name in (*FILES, TRANSACTION, '.sync.lock')):
            raise SyncError('Symbolic links are not allowed for sync output.')
        directory.mkdir(parents=True, exist_ok=True)
        process_lock = _lock_process(directory)
        if transaction.exists():
            _recover(directory, transaction)
        transaction.mkdir()
        owned = True
        staged = transaction / 'staged'
        backup = transaction / 'before'
        staged.mkdir()
        backup.mkdir()
        before = {}
        for name in FILES:
            path = directory / name
            before[name] = path.read_bytes() if path.exists() else None
            original_hashes[name] = _hash(path)
            if path.exists():
                shutil.copyfile(path, staged / name)
                shutil.copyfile(path, backup / name)
        sidecar = directory / 'workout_classifications.csv'
        sidecar_before = sidecar.read_bytes() if sidecar.exists() else None
        if sidecar_before is not None:
            (staged / sidecar.name).write_bytes(sidecar_before)
        old_daily = analysis.load_daily_metrics(staged / 'daily_metrics.csv') if (staged / 'daily_metrics.csv').exists() else []
        first, last = recent_window(today)
        client = (client_factory or WhoopClient)()
        collection = entities.collect_window(client, first, last)
        start = datetime.combine(first - timedelta(days=1), time.min, timezone.utc).isoformat()
        end = datetime.combine(last + timedelta(days=2), time.min, timezone.utc).isoformat()
        new_workouts = workouts.collect_workouts(client, start, end)
        entities.save_history(collection, staged)
        archived = {kind: entities.read_entities(staged / (kind + '.csv'), kind)
                    for kind in ('cycles', 'sleeps', 'recoveries')}
        # Validate retained relationships too, before any target is replaced.
        entities.derive_daily(archived, date.min, date.max)
        entities.save_history(_daily_collection(collection, archived, old_daily), staged)
        workouts.save_workouts(new_workouts, staged)
        from whoop_daily_report import load_daily_report
        load_daily_report(staged)
        # Reject another writer rather than overwrite changes made during the fetch.
        if any((directory / name).read_bytes() != contents if contents is not None else (directory / name).exists()
               for name, contents in before.items()):
            raise SyncError('Inputs changed during collection.')
        if (sidecar.read_bytes() if sidecar.exists() else None) != sidecar_before:
            raise SyncError('Classifications changed during collection.')
        for path in [*(backup / name for name in FILES if original_hashes[name] is not None),
                     *(staged / name for name in FILES)]:
            with path.open('r+b') as file:
                os.fsync(file.fileno())
        manifest = {'state': 'prepared', 'original': original_hashes}
        _write_manifest(transaction, manifest)
        manifest = _state(transaction, manifest, 'publishing')
        for name in FILES:
            os.replace(staged / name, directory / name)
        load_daily_report(directory)
        _state(transaction, manifest, 'committed')
        _cleanup(transaction)
        owned = False
        return {'success': True, 'message': 'WHOOP sync complete. Reload the local report.',
                'reporting_days': 3}
    except Exception:
        if owned:
            try:
                _recover(directory, transaction)
            except Exception:
                return {'success': False, 'message': 'Sync recovery is pending. Local reports are paused; retry Sync WHOOP.'}
        return {'success': False, 'message': FAILURE}
    finally:
        if process_lock is not None:
            process_lock.close()
        DATA_LOCK.release()
