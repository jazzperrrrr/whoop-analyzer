"""Synthetic inputs and strict guards shared by the product/API tests."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch
import requests

from test_whoop_dashboard import write_inputs
from test_whoop_daily_report import D, entities
from test_whoop_sleep import EXPECTED
from whoop_entities import SCHEMAS
from whoop_product.repository import CsvProductRepository

NOW = datetime(2026,9,12,12,tzinfo=timezone.utc)


def rewrite(root,kind,change):
    path=root/(kind+'.csv')
    with path.open(newline='',encoding='utf-8') as file:
        rows=list(csv.DictReader(file))
    change(rows)
    with path.open('w',newline='',encoding='utf-8') as file:
        writer=csv.DictWriter(file,fieldnames=SCHEMAS[kind])
        writer.writeheader()
        writer.writerows(rows)


class SyntheticInputs:
    def setup_inputs(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.enterContext(patch.object(requests.Session,'request',side_effect=AssertionError('HTTP forbidden')))
        self.enterContext(patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')))
        for target in ('whoop_auth.load_tokens','whoop_auth.read_config','whoop_auth.refresh_tokens',
                       'whoop_fetch.WhoopClient','whoop_sync.sync_recent'):
            self.enterContext(patch(target,side_effect=AssertionError('WHOOP/credentials forbidden')))
        original=Path.open
        project=Path(__file__).resolve().parents[1]
        def guarded(path,*args,**kwargs):
            resolved=path.resolve()
            if resolved.is_relative_to(project/'data') or (resolved.parent==project and
                    (resolved.name.startswith('.env') or 'token' in resolved.name.lower())):
                raise AssertionError('Private project inputs forbidden')
            return original(path,*args,**kwargs)
        self.enterContext(patch.object(Path,'open',guarded))
        write_inputs(self.root,extended=True)
        # Sync creates this coordination file; API readers only open it read-only.
        (self.root/'.sync.lock').touch()
        def add(rows):
            for gap in (1,7,14,30,31):
                row=entities(D-timedelta(days=gap))['sleeps'][0]
                row.update(EXPECTED)
                rows.append(row)
            nap=entities(D)['sleeps'][0]
            nap.update(EXPECTED)
            nap.update(sleep_id='synthetic-nap',nap='true',total_rem_sleep_time_ms=1000)
            rows.append(nap)
        rewrite(self.root,'sleeps',add)
        self.repo=CsvProductRepository(self.root,clock=lambda:NOW)
