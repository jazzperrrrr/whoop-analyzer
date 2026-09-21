"""Local WHOOP views with a separate, explicit manual sync action."""

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs

import whoop_analysis as analysis
from whoop_daily_report import load_daily_report
from whoop_entities import DATA_DIR
from whoop_fetch import APIError
from whoop_sleep_product import compose_sleep_product
from whoop_sync import local_read, sync_recent, SyncError


INPUTS = ('sleeps.csv', 'recoveries.csv', 'cycles.csv', 'daily_metrics.csv',
          'workouts.csv', 'workout_classifications.csv')
METRICS = ('recovery_score', 'hrv_ms', 'resting_heart_rate_bpm', 'sleep_performance')
LABELS = ('Recovery', 'HRV', 'Resting HR', 'Sleep Performance')
CSS = Path(__file__).with_name('dashboard.css')


class SnapshotChanged(Exception):
    """Local inputs changed while they were being read."""


def fingerprint(root):
    result = []
    for name in INPUTS:
        try:
            stat = (root / name).stat()
            result.append((name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))
        except FileNotFoundError:
            result.append((name, None))
    return result


def load_snapshot(root):
    root = Path(root)
    with local_read(root):
        before = fingerprint(root)
        report = load_daily_report(root)
        if fingerprint(root) != before:
            raise SnapshotChanged
        return report


def e(value):
    return escape(str(value), quote=True)


def number(value, signed=False):
    if value is None:
        return 'Unavailable'
    return f'{value:+.1f}' if signed else f'{value:.1f}'


def interval(minutes):
    if minutes is None:
        return 'Unavailable'
    hours, mins = divmod(round(minutes), 60)
    return f'{hours}h {mins:02d}m'


def shell(content):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>WHOOP Analyzer · Local dashboard</title>'
            '<link rel="stylesheet" href="/dashboard.css"></head><body>'
            '<a class="skip" href="#main">Skip to report</a><div class="page">'
            '<header><a class="brand" href="/">WHOOP <span>ANALYZER</span></a>'
            '<span class="local">LOCAL / MANUAL SYNC</span></header>'
            '<main id="main">' + content + '</main>'
            '<footer>From your local WHOOP snapshot. No automatic sync. '
            'Descriptive signals relative to your baseline.</footer></div></body></html>')


def warning(message):
    return '<p class="notice">' + e(message) + '</p>'


def metric_card(report, metric, label):
    current = report.physiology[metric]
    base = report.baseline[metric][14]
    signal = report.interpretation['signals'][metric]['state']
    tone = signal if signal in ('positive', 'negative', 'neutral') else 'unknown'
    unit = analysis.METRICS[metric][1]
    delta_key = 'percentage_deviation' if metric == 'hrv_ms' else 'difference'
    delta_unit = '%' if metric == 'hrv_ms' else 'bpm' if metric == 'resting_heart_rate_bpm' else 'pp'
    delta = base[delta_key]
    arrow = '' if delta is None or delta == 0 else '↑ ' if delta > 0 else '↓ '
    delta_text = 'Deviation unavailable' if delta is None else f'{arrow}{number(delta, True)} {delta_unit} vs baseline'
    count = base['days_available']
    coverage = 'Full coverage' if count == 14 else 'Partial coverage' if base['eligible'] else 'Insufficient baseline'
    reason = ''
    if current['source_conflicts']:
        reason = warning('Source snapshots conflict; this value is withheld.')
    elif report.selection['ambiguous_primary']:
        reason = warning('Multiple primary sleeps; current value withheld.')
    elif not current['available']:
        reason = warning('Current value is missing or not scored. No earlier value substituted.')
    value = number(current['value'])
    suffix = f'<span class="unit">{e(unit)}</span>' if current['available'] else ''
    baseline = number(base['average']) + (f' {unit}' if base['average'] is not None else '')
    return (f'<article class="metric {tone}"><h2>{e(label)}</h2>'
            f'<p class="value{ " missing" if not current["available"] else ""}">{value}{suffix}</p>'
            f'<p class="delta">{e(delta_text)}</p><p class="muted">14d baseline · {e(baseline)}</p>'
            f'<p class="coverage">{e(coverage)} · {count}/14 days</p>'
            f'<span class="signal">{e(signal)}</span>{reason}</article>')


def training(section, heading):
    out = f'<article class="panel training"><h3>{e(heading)}</h3>'
    if not section:
        return out + '<p>Unavailable without a primary-sleep report date.</p></article>'
    out += f'<p class="muted">{e(section["start_date"])} → {e(section["end_date"])}</p>'
    if not section['record_count']:
        out += '<p>No recorded workouts.</p>'
    else:
        out += '<div class="table-wrap"><table><thead><tr><th scope="col">Activity</th><th scope="col">Minutes</th><th scope="col">Records</th><th scope="col">Dates</th></tr></thead><tbody>'
        for name, activity in section['activities'].items():
            if not activity['record_count']:
                continue
            minutes = activity['duration_seconds']
            minutes = None if minutes is None else minutes / 60
            out += (f'<tr><th scope="row">{e(name)}</th><td>{number(minutes)}</td>'
                    f'<td>{activity["record_count"]}</td><td>{activity["distinct_training_dates"]}</td></tr>')
        out += '</tbody></table></div>'
        out += f'<p class="training-total">{section["record_count"]} WHOOP records · {interval(None if section["duration_seconds"] is None else section["duration_seconds"] / 60)} recorded</p>'
        b = section['badminton']
        if b['record_count']:
            minutes = None if b['zone45_milli'] is None else b['zone45_milli'] / 60000
            out += (f'<p>Badminton Z4+5: {number(minutes)}' + (' min' if minutes is not None else '')
                    + f' · {b["zone45_record_count"]}/{b["record_count"]} records available</p>')
    return out + '<p class="muted small">Coverage unknown. No recorded workouts does not establish rest; records are not confirmed sessions.</p></article>'


def details(report):
    out = '<details class="panel details"><summary>Details / Data Quality</summary><div class="detail-body">'
    out += '<h3>Baseline coverage</h3><p class="muted">Windows exclude the report date. Signal interpretation uses 14 days, with at least 7 observed days per required signal.</p>'
    out += '<div class="table-wrap"><table><thead><tr><th scope="col">Metric</th><th scope="col">Window</th><th scope="col">Dates</th><th scope="col">Mean</th><th scope="col">Days</th></tr></thead><tbody>'
    for metric, label in zip(METRICS, LABELS):
        for window in (7, 14, 30):
            b = report.baseline[metric][window]
            bounds = f'{b["window_start"]} → {b["window_end"]}' if b['window_start'] else 'Unavailable'
            out += (f'<tr><th scope="row">{e(label)}</th><td>{window}d</td><td>{e(bounds)}</td>'
                    f'<td>{number(b["average"])} {e(analysis.METRICS[metric][1])}</td><td>{b["days_available"]}/{window}</td></tr>')
    out += '</tbody></table></div><h3>Stored snapshot</h3><dl class="facts">'
    facts = [('Primary sleep', report.selection['status']),
             ('Snapshot consistency', report.provenance['snapshot_consistency']['status'])]
    for metric, label in zip(METRICS, LABELS):
        facts.append((label + ' source score', report.physiology[metric]['score_state'] or 'unavailable'))
    for key, label in (('recent_offset_transition', 'Recent offset transition'),
                       ('baseline_offset_transition', 'Baseline offset transition'),
                       ('mixed_offset_date', 'Mixed-offset date'),
                       ('mixed_activity_yesterday', 'Mixed activity yesterday')):
        value = report.context[key]
        facts.append((label, 'unknown' if value is None else 'observed' if value else 'not observed'))
    quality = {f['code']: f for f in report.data_quality}
    unfinished = quality['unfinished_cycle']['value']
    facts.append(('Cycle', 'unknown' if unfinished is None else 'unfinished in stored snapshot' if unfinished else 'complete in stored snapshot'))
    for label, value in facts:
        out += f'<div><dt>{e(label)}</dt><dd>{e(value)}</dd></div>'
    out += '</dl><p class="muted">Recorded offsets do not identify location or prove absence of travel. Collection time and live completeness are unknown.</p><h3>Report notes</h3><ul class="notes">'
    # These details are authored by the report module, never raw input/provenance dumps.
    for flag in report.data_quality:
        if flag['value'] is True and not flag['code'].startswith(('baseline_', 'missing_or_unscored_')):
            out += f'<li><strong>{e(flag["code"].replace("_", " ").capitalize())}:</strong> {e(flag["detail"])}</li>'
    return out + '</ul></div></details>'


def sleep_product(report):
    return report.sleep_product or compose_sleep_product(report, [report.selection['sleep']] if report.selection['sleep'] else [])


def sleep_value(product, name):
    metric = product.metrics[name]
    if metric.value is None:
        return 'Unavailable'
    if metric.unit == 'ms':
        sign = '-' if metric.value < 0 else '+' if name == 'actual_minus_need_ms' and metric.value > 0 else ''
        return sign + interval(abs(metric.value) / 60000)
    if metric.unit == 'count':
        return str(int(metric.value))
    return number(metric.value) + ('%' if metric.unit == 'percent' else ' breaths/min')


def sleep_facts(product, fields):
    out = '<dl class="facts">'
    for name, label in fields:
        metric = product.metrics[name]
        out += f'<div><dt>{e(label)} <small>({e(metric.origin)})</small></dt><dd>{e(sleep_value(product, name))}</dd></div>'
    return out + '</dl>'


def controls(csrf_token):
    out = '<nav class="actions"><a class="button" href="/">Refresh local data</a><a class="button" href="/sleep">Sleep Details</a>'
    if csrf_token:
        out += ('<form method="post" action="/sync">'
                f'<input type="hidden" name="csrf" value="{e(csrf_token)}">'
                '<button class="button" type="submit">Sync WHOOP</button></form>')
    return out + '</nav><p class="muted">Refresh rereads files. Sync WHOOP fetches recent records and updates local data.</p>'


def freshness_notice(product):
    age = product.freshness['days_since_latest_available_morning']
    if age is None:
        return '<p class="muted">No available morning. Collection time is unknown.</p>'
    out = f'<p class="muted">{age} calendar days since latest available morning; collection time is unknown.</p>'
    if product.freshness['may_be_out_of_date']:
        out += warning('Local data may be out of date.')
    return out


def render_sleep_details(report, csrf_token=None):
    product = sleep_product(report)
    out = f'<h1>Sleep Details</h1><p>Latest available morning: {e(product.report_date or "Unavailable")}</p>'
    out += freshness_notice(product) + controls(csrf_token)
    out += '<section class="panel"><h2>Last Sleep</h2>'
    if product.timing:
        t = product.timing
        out += f'<p>{t["local_start"]:%Y-%m-%d %H:%M} → {t["local_end"]:%Y-%m-%d %H:%M} ({e(t["recorded_offset"])})</p>'
        out += f'<p>Recorded interval: {interval(t["recorded_interval_ms"] / 60000)}</p>'
    else:
        out += warning('Primary sleep is unavailable or ambiguous; no single-night values substituted.')
    out += sleep_facts(product, [('actual_sleep_ms', 'Actual Sleep'), ('in_bed_ms', 'WHOOP time in bed'), ('total_need_ms', 'Sleep Need')]) + '</section>'
    out += '<section class="panel"><h2>Sleep Stages</h2><p class="muted">Aggregate totals, not a stage timeline. Restorative overlaps Deep and REM.</p>'
    out += sleep_facts(product, [('awake_ms', 'Awake'), ('light_ms', 'Light'), ('deep_ms', 'Deep'), ('rem_ms', 'REM'),
                                ('restorative_sleep_ms', 'Restorative'), ('no_data_ms', 'No-data time'),
                                ('light_pct_actual_sleep', 'Light % of actual sleep'), ('deep_pct_actual_sleep', 'Deep % of actual sleep'),
                                ('rem_pct_actual_sleep', 'REM % of actual sleep'), ('restorative_pct_actual_sleep', 'Restorative % of actual sleep'),
                                ('awake_pct_in_bed', 'Awake % of time in bed')]) + '</section>'
    out += '<section class="panel"><h2>Sleep Need Breakdown</h2><p class="muted">Signed sum of WHOOP-provided components; not a reconstruction of its Sleep Debt algorithm.</p>'
    out += sleep_facts(product, [('baseline_need_ms', 'Baseline need'), ('sleep_debt_need_ms', 'Sleep debt'),
                                ('recent_strain_need_ms', 'Recent strain'), ('nap_adjustment_ms', 'Nap adjustment'),
                                ('total_need_ms', 'Total Sleep Need'), ('actual_minus_need_ms', 'Sleep versus estimated need')])
    out += '<p class="muted">Sleep versus estimated need is a single-night duration difference, not accumulated sleep debt.</p></section>'
    out += '<section class="panel"><h2>Quality</h2>' + sleep_facts(product, [('performance_pct', 'Sleep Performance'),
        ('efficiency_pct', 'Sleep Efficiency'), ('consistency_pct', 'Sleep Consistency'), ('respiratory_rate', 'Respiratory Rate'),
        ('sleep_cycle_count', 'Sleep cycles'), ('disturbance_count', 'Disturbances')]) + '</section>'
    labels = {'actual_sleep_ms': 'Actual Sleep', 'total_need_ms': 'Sleep Need', 'performance_pct': 'Sleep Performance',
              'efficiency_pct': 'Sleep Efficiency', 'consistency_pct': 'Sleep Consistency', 'respiratory_rate': 'Respiratory Rate',
              'deep_ms': 'Deep sleep', 'rem_ms': 'REM sleep', 'restorative_sleep_ms': 'Restorative sleep'}
    out += '<section class="panel"><h2>Trends</h2><p class="muted">Primary sleeps only. Prior windows exclude the selected date; missing dates are not zero-filled. No predictions.</p>'
    for name, trend in product.trends.items():
        unit = 'minutes' if trend['unit'] == 'ms' else trend['unit']
        divisor = 60000 if trend['unit'] == 'ms' else 1
        def display(value):
            return 'Unavailable' if value is None else number(value / divisor)
        out += f'<details><summary>{e(labels[name])} ({e(unit)})</summary><table><tr><th>Prior window</th><th>Mean</th><th>Observed dates</th></tr>'
        for window, stats in trend['windows'].items():
            out += f'<tr><td>{window} days</td><td>{display(stats["average"])}</td><td>{stats["days_available"]}/{window}</td></tr>'
        out += '</table><table><tr><th>Date</th><th>Daily value</th><th>Recorded offset</th></tr>'
        for point in trend['daily']:
            out += f'<tr><td>{e(point["report_date"])}</td><td>{display(point["value"])}</td><td>{e(point["recorded_offset"])}</td></tr>'
        out += '</table></details>'
    out += '</section><section class="panel"><h2>Naps</h2><p class="muted">Separate recorded naps; not added to primary sleep stages. Need adjustment comes only from WHOOP.</p>'
    for nap in product.naps:
        t = nap['timing']
        value = nap['metrics']['actual_sleep_ms'].value
        out += f'<p>{t["local_start"]:%Y-%m-%d %H:%M} → {t["local_end"]:%H:%M} ({e(t["recorded_offset"])}) · Actual nap sleep: {interval(None if value is None else value/60000)}</p>'
    if not product.naps:
        out += '<p>No recorded naps in this local archive.</p>'
    out += '</section><details class="panel"><summary>Sleep Data Quality</summary>'
    out += ''.join(f'<p>{e(flag.replace("_", " "))}</p>' for flag in product.quality)
    out += f'<p>Score state: {e(product.provenance.get("score_state") or "Unavailable")}</p>'
    residual = product.provenance.get('accounting_residual_ms')
    out += f'<p>Duration accounting residual: {e(residual if residual is not None else "Unavailable")} ms</p>'
    out += '<p>Official efficiency remains authoritative; derived ratios are diagnostic only. Source update time is not collection time.</p></details>'
    return shell(out)


def render_report(report, csrf_token=None):
    product = sleep_product(report)
    date_text = str(report.report_date) if report.report_date else 'Unavailable'
    content = ('<div class="intro"><div><p class="eyebrow">YOUR MORNING, IN CONTEXT</p>'
               f'<h1>Latest available morning <span>{e(date_text)}</span></h1>'
               '<p class="muted">Local snapshot · collection freshness unknown</p></div></div>')
    content += freshness_notice(product) + controls(csrf_token)
    selection = report.selection
    if selection['status'] == 'ambiguous_date':
        content += warning('Ambiguous wake-up dates. No morning date or date-dependent windows selected.')
    elif selection['ambiguous_primary']:
        content += warning('Multiple primary-sleep candidates. Current physiology is withheld.')
    elif report.report_date is None:
        content += warning('No completed primary sleep is available. No morning date inferred.')
    fallback = selection['fallback_status']
    prefix = 'Latest completed morning shown.' if report.report_date else 'No completed morning selected.'
    if fallback == 'newer_incomplete_primary':
        content += warning(prefix + ' A newer incomplete primary sleep has an unknown wake-up date.')
    elif fallback == 'incomplete_order_unknown':
        content += warning(prefix + ' Incomplete primary-sleep chronology is unknown.')
    for flag in report.data_quality:
        if flag['value'] and flag['code'] in ('missing_matching_daily_snapshot', 'missing_workout_dataset', 'missing_classification_sidecar'):
            content += warning(flag['detail'])
    content += '<section class="metrics" aria-label="Morning metrics">'
    content += ''.join(metric_card(report, metric, label) for metric, label in zip(METRICS, LABELS)) + '</section>'
    state = report.interpretation
    content += '<div class="context-grid"><section class="panel state"><h2>Physiological State</h2>'
    content += f'<p class="state-title">{e(state["overall_state"].capitalize())}</p>'
    content += ''.join(f'<p>{e(message)}</p>' for message in state['explanations'])
    content += '<ul class="signal-list">'
    for metric, label in zip(METRICS, LABELS):
        content += f'<li><span>{e(label)}</span><strong>{e(state["signals"][metric]["state"])}</strong></li>'
    content += '</ul></section><section class="panel sleep"><h2>Last Sleep</h2>'
    content += sleep_facts(product, [('actual_sleep_ms', 'Actual Sleep'), ('total_need_ms', 'Sleep Need'),
                                    ('performance_pct', 'Sleep Performance'), ('efficiency_pct', 'Sleep Efficiency')])
    if product.timing:
        s = product.timing
        content += (f'<p class="sleep-time">{s["local_start"]:%H:%M} <span>→</span> {s["local_end"]:%H:%M}</p>'
                    f'<p>{s["local_start"]:%Y-%m-%d} → {s["local_end"]:%Y-%m-%d}</p>'
                    f'<p class="muted">Recorded UTC offset {e(s["recorded_offset"])}</p>'
                    f'<div class="sleep-duration"><span>Recorded sleep interval</span><strong>{interval(s["recorded_interval_ms"] / 60000)}</strong></div>')
    elif product.status == 'ambiguous':
        content += warning('Ambiguous candidate interval; not a confirmed single primary sleep.')
    else:
        content += '<p>Primary sleep unavailable or source snapshots conflict.</p>'
    content += '</section></div><section aria-labelledby="training-title"><div class="section-heading"><h2 id="training-title">Recent Training</h2><span>Relative to the report morning</span></div><div class="training-grid">'
    content += training(report.yesterday_training, 'Yesterday') + training(report.recent_training, 'Previous 3 days')
    content += '</div></section>' + details(report)
    return shell(content)


def error_page(changed=False):
    message = ('Local inputs changed while loading. Wait for the local update to finish, then refresh.' if changed else
               'The local report could not be loaded. Check that daily_metrics.csv exists and that the local input files are valid and readable. No files were changed.')
    return shell('<section class="panel error"><p class="eyebrow">LOCAL DATA</p><h1>Report unavailable</h1><p>'
                 + message + '</p><a class="button" href="/">Refresh local data</a></section>')


def make_handler(data_dir):
    csrf_token = secrets.token_urlsafe(32)
    token_lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        server_version = 'LocalDashboard'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass

        def respond(self, status, body, content_type='text/html; charset=utf-8', location=None):
            payload = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'same-origin')
            if location:
                self.send_header('Location', location)
            self.send_header('Connection', 'close')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)
            self.close_connection = True

        def send_error(self, code, message=None, explain=None):
            self.respond(code, 'Request not supported.', 'text/plain; charset=utf-8')

        def local_request(self):
            hosts = self.headers.get_all('Host', [])
            port = self.server.server_address[1]
            if self.client_address[0] != '127.0.0.1' or len(hosts) != 1 or hosts[0] not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                self.respond(403, 'Local access only.', 'text/plain; charset=utf-8')
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.respond(403, 'Local access only.', 'text/plain; charset=utf-8')
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            if self.path == '/dashboard.css':
                self.respond(200, CSS.read_text(encoding='utf-8'), 'text/css; charset=utf-8')
            elif self.path in ('/sync/success', '/sync/failure'):
                message = ('WHOOP sync complete. Reload local data.' if self.path == '/sync/success' else
                           'WHOOP sync failed. No automatic retry. Reload local data or retry recovery.')
                self.respond(200, shell(warning(message) + controls(csrf_token)))
            elif self.path in ('/', '/sleep'):
                try:
                    renderer = render_report if self.path == '/' else render_sleep_details
                    self.respond(200, renderer(load_snapshot(data_dir), csrf_token))
                except SnapshotChanged:
                    self.respond(503, error_page(changed=True))
                except SyncError:
                    self.respond(503, shell(warning('A sync is incomplete. Use Sync WHOOP to recover.') + controls(csrf_token)))
                except (APIError, analysis.AnalysisError, OSError, ValueError):
                    self.respond(503, error_page())
            else:
                self.respond(404, 'Not found.', 'text/plain; charset=utf-8')

        do_HEAD = do_GET

        def do_POST(self):
            nonlocal csrf_token
            if not self.local_request():
                return
            if self.path != '/sync':
                self.respond(405, 'Only explicit Sync WHOOP accepts POST.', 'text/plain; charset=utf-8')
                return
            origins = self.headers.get_all('Origin', [])
            if origins != ['http://' + self.headers['Host']]:
                self.respond(403, 'Same-origin sync required.', 'text/plain; charset=utf-8')
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or self.headers.get('Transfer-Encoding'):
                    raise ValueError
                length = int(lengths[0])
                if not 0 < length <= 1024 or self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                    raise ValueError
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError
                fields = parse_qs(raw.decode('utf-8'), max_num_fields=2, strict_parsing=True)
                values = fields.get('csrf', [])
                with token_lock:
                    if (set(fields) != {'csrf'} or len(values) != 1 or not values[0].isascii()
                            or not secrets.compare_digest(values[0], csrf_token)):
                        raise ValueError
                    # Consume before invoking sync. Replayed forms must be reloaded.
                    csrf_token = secrets.token_urlsafe(32)
            except (ValueError, UnicodeError, OSError):
                self.respond(403, 'Invalid sync action.', 'text/plain; charset=utf-8')
                return
            try:
                result = sync_recent(data_dir)
                ok = result.get('success') is True
            except Exception:
                ok = False
            self.respond(303, '', location='/sync/success' if ok else '/sync/failure')

        def reject_write(self):
            self.respond(405, 'Method not supported.', 'text/plain; charset=utf-8')

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = reject_write

    return Handler


class LocalServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Avoid traces containing paths, arbitrary request text or personal data.
        print('A local request could not be completed; refresh to retry.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR)
    parser.add_argument('--port', type=int, default=8501)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    try:
        with LocalServer(('127.0.0.1', args.port), make_handler(args.data_dir)) as server:
            print(f'WHOOP Analyzer: http://127.0.0.1:{args.port} (local, manual sync only). Ctrl+C to stop.', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except OSError:
        print('Cannot start the local dashboard. The port may be busy; try --port 8502.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
