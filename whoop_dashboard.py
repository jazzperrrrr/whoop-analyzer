"""Offline, read-only WHOOP dashboard. Bind only to this computer's loopback."""

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import whoop_analysis as analysis
from whoop_daily_report import load_daily_report
from whoop_entities import DATA_DIR
from whoop_fetch import APIError


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
            '<span class="local">LOCAL / READ ONLY</span></header>'
            '<main id="main">' + content + '</main>'
            '<footer>From your local WHOOP snapshot. No live sync. '
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


def render_report(report):
    date_text = str(report.report_date) if report.report_date else 'Unavailable'
    content = ('<div class="intro"><div><p class="eyebrow">YOUR MORNING, IN CONTEXT</p>'
               f'<h1>Latest available morning <span>{e(date_text)}</span></h1>'
               '<p class="muted">Local snapshot · collection freshness unknown</p></div>'
               '<a class="button" href="/">Refresh local data <span aria-hidden="true">↻</span></a></div>')
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
    if report.sleep:
        s = report.sleep
        content += (f'<p class="sleep-time">{s["local_start"]:%H:%M} <span>→</span> {s["local_end"]:%H:%M}</p>'
                    f'<p>{s["local_start"]:%Y-%m-%d} → {s["local_end"]:%Y-%m-%d}</p>'
                    f'<p class="muted">Recorded UTC offset {e(s["recorded_offset"])}</p>'
                    f'<div class="sleep-duration"><span>Recorded sleep interval</span><strong>{interval(s["recorded_interval_minutes"])}</strong></div>')
        if s['ambiguous']:
            content += warning('Ambiguous candidate interval; not a confirmed single primary sleep.')
    else:
        content += '<p>Primary sleep unavailable.</p>'
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
    class Handler(BaseHTTPRequestHandler):
        server_version = 'LocalDashboard'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass

        def respond(self, status, body, content_type='text/html; charset=utf-8'):
            payload = body.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Connection', 'close')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)
            self.close_connection = True

        def send_error(self, code, message=None, explain=None):
            self.respond(code, 'Request not supported.', 'text/plain; charset=utf-8')

        def do_GET(self):
            hosts = self.headers.get_all('Host', [])
            port = self.server.server_address[1]
            if len(hosts) != 1 or hosts[0] not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                self.respond(403, 'Local access only.', 'text/plain; charset=utf-8')
                return
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                self.respond(403, 'Local access only.', 'text/plain; charset=utf-8')
                return
            if self.path == '/dashboard.css':
                self.respond(200, CSS.read_text(encoding='utf-8'), 'text/css; charset=utf-8')
            elif self.path == '/':
                try:
                    self.respond(200, render_report(load_snapshot(data_dir)))
                except SnapshotChanged:
                    self.respond(503, error_page(changed=True))
                except (APIError, analysis.AnalysisError, OSError, ValueError):
                    self.respond(503, error_page())
            else:
                self.respond(404, 'Not found.', 'text/plain; charset=utf-8')

        do_HEAD = do_GET

        def do_POST(self):
            self.respond(405, 'Read-only dashboard.', 'text/plain; charset=utf-8')

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

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
            print(f'WHOOP Analyzer: http://127.0.0.1:{args.port} (local, read only). Ctrl+C to stop.', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except OSError:
        print('Cannot start the local dashboard. The port may be busy; try --port 8502.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
