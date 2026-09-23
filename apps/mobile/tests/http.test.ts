import golden from '../src/api/fixtures/api_v1_examples.json';
import demo from '../src/api/fixtures/visual_demo.json';
import { HttpApiClient, loopbackBase } from '../src/api/http';
import { validateSleep, validateToday } from '../src/api/validate';
import { errorCopy } from '../src/design/copy';

const response = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response;
test('default fetch preserves the browser global receiver', async () => {
  const originalFetch = globalThis.fetch;
  const browserFetch = jest.fn(function (this: unknown) {
    if (this !== globalThis) throw new TypeError('Illegal invocation');
    return Promise.resolve(response({ status: 'ok', schema_version: 'v1' }));
  });
  globalThis.fetch = browserFetch;
  try {
    await expect(new HttpApiClient('http://localhost:8000').getHealth()).resolves.toEqual({ status: 'ok', schema_version: 'v1' });
    expect(browserFetch).toHaveBeenCalledTimes(1);
    expect(browserFetch.mock.contexts[0]).toBe(globalThis);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
test('health, Today and Sleep use only three GET routes with credentials omitted', async () => {
  const fetcher = jest.fn().mockResolvedValueOnce(response({ status: 'ok', schema_version: 'v1' })).mockResolvedValueOnce(response(golden.today)).mockResolvedValueOnce(response(golden.sleep));
  const client = new HttpApiClient('http://127.0.0.1:8000/', fetcher);
  expect(await client.getHealth()).toEqual({ status: 'ok', schema_version: 'v1' });
  expect(await client.getToday()).toEqual(golden.today);
  expect(await client.getLatestSleep()).toEqual(golden.sleep);
  expect(fetcher.mock.calls.map(c => c[0])).toEqual(['http://127.0.0.1:8000/api/v1/health', 'http://127.0.0.1:8000/api/v1/today', 'http://127.0.0.1:8000/api/v1/sleep/latest']);
  for (const [url, options] of fetcher.mock.calls) {
    expect(options).toMatchObject({ method: 'GET', credentials: 'omit', redirect: 'error', cache: 'no-store', referrerPolicy: 'no-referrer', headers: { Accept: 'application/json' } });
    expect(options.signal).toBeInstanceOf(AbortSignal);
    expect(options.body).toBeUndefined();
    expect(url).not.toMatch(/sync|oauth|whoop|token/);
    expect(Object.keys(options.headers)).toEqual(['Accept']);
  }
});
test.each(['https://127.0.0.1:8000', 'http://0.0.0.0:8000', 'http://192.168.1.2:8000', 'http://example.com', 'http://127.1:8000', 'http://2130706433:8000', 'http://localhost.evil.test', 'http://user:pass@localhost:8000', 'http://localhost:8000/sync', 'http://localhost:8000?token=x', 'http://localhost:8000#x', 'http://localhost:99999'])('reject unsafe base URL %s before fetch', url => {
  const fetcher = jest.fn();
  expect(() => new HttpApiClient(url, fetcher)).toThrow('Report unavailable');
  expect(fetcher).not.toHaveBeenCalled();
});
test.each(['http://127.0.0.1:8000', 'http://localhost:8000', 'http://[::1]:8000'])('accept exact loopback %s', url => expect(loopbackBase(url + '/')).toBe(url));
test.each([[403, 'local_access_only'], [404, 'not_found'], [405, 'read_only'], [422, 'invalid_query'], [500, 'internal_error'], [503, 'snapshot_unavailable'], [503, '__proto__']])('HTTP %s consumes sanitized Error code %s', async (status, code) => {
  const fetcher = jest.fn().mockResolvedValue(response({ code, message: 'private raw detail', retryable: status === 503, request_id: 'private-request' }, status as number));
  const failure = await new HttpApiClient('http://localhost:8000', fetcher).getToday().catch(e => e);
  expect(failure).toMatchObject({ code, retryable: status === 503, message: 'Report unavailable' });
  expect(errorCopy(failure.code)).not.toMatch(/private|__proto__|null|NaN/);
  expect(JSON.stringify(failure)).not.toMatch(/private/);
});
test('unknown error code uses generic fallback', async () => {
  const fetcher = jest.fn().mockResolvedValue(response({ code: 'future_code', message: 'secret', retryable: false, request_id: 'sample-id' }, 500));
  const failure = await new HttpApiClient('http://localhost:8000', fetcher).getToday().catch(e => e);
  expect(errorCopy(failure.code)).toBe('We could not load your report.');
});
test('timeout aborts and bounds even a fetch implementation that never settles', async () => {
  jest.useFakeTimers();
  try {
    const fetcher = jest.fn().mockImplementation(() => new Promise(() => {}));
    const pending = new HttpApiClient('http://localhost:8000', fetcher, 50).getToday();
    const check = expect(pending).rejects.toMatchObject({ code: 'request_timeout' });
    await jest.advanceTimersByTimeAsync(50);
    await check;
    expect(fetcher.mock.calls[0]![1].signal.aborted).toBe(true);
  } finally { jest.useRealTimers(); }
});
test('timeout also covers response body reads', async () => {
  jest.useFakeTimers();
  try {
    const fetcher = jest.fn().mockResolvedValue({ ok: true, json: () => new Promise(() => {}) });
    const pending = new HttpApiClient('http://localhost:8000', fetcher, 50).getToday();
    const check = expect(pending).rejects.toMatchObject({ code: 'request_timeout' });
    await jest.advanceTimersByTimeAsync(50); await check;
  } finally { jest.useRealTimers(); }
});
test.each([new TypeError('private URL'), Object.assign(new Error('private abort'), { name: 'AbortError' })])('network/abort failure is safe', async error => {
  const fetcher = jest.fn().mockRejectedValue(error);
  await expect(new HttpApiClient('http://localhost:8000', fetcher).getToday()).rejects.toMatchObject({ code: 'connection_failed', message: 'Report unavailable' });
});
test('malformed JSON and malformed Error fail safely', async () => {
  const fetcher = jest.fn().mockResolvedValueOnce({ ok: true, json: async () => { throw new Error('private payload'); } }).mockResolvedValueOnce(response({ code: 'internal_error' }, 500));
  const client = new HttpApiClient('http://localhost:8000', fetcher);
  await expect(client.getToday()).rejects.toMatchObject({ code: 'invalid_response' });
  await expect(client.getToday()).rejects.toMatchObject({ code: 'invalid_response' });
});
test('health rejects an unsupported schema', async () => {
  const fetcher = jest.fn().mockResolvedValue(response({ status: 'ok', schema_version: 'v2' }));
  await expect(new HttpApiClient('http://localhost:8000', fetcher).getHealth()).rejects.toMatchObject({ code: 'invalid_response' });
});
test.each(['schema_version', 'analysis_version', 'snapshot_id', 'generated_at', 'report_date', 'latest_available_morning', 'data'])('required envelope field %s cannot be missing', key => {
  for (const [source, validate] of [[golden.today, validateToday], [golden.sleep, validateSleep]] as const) {
    const payload: Record<string, unknown> = { ...source }; delete payload[key];
    expect(() => validate(payload)).toThrow('Report unavailable');
  }
});
test.each([
  ['schema_version', 'v2'], ['analysis_version', ''], ['snapshot_id', '  '], ['generated_at', 'invalid'],
  ['report_date', '2026-02-30'], ['latest_available_morning', 22], ['data', null],
])('invalid envelope %s rejected', (key, value) => {
  expect(() => validateToday({ ...golden.today, [key]: value })).toThrow('Report unavailable');
});
test('identity strings are opaque; unknown reason codes, zeros and missing values survive validation', () => {
  const payload = JSON.parse(JSON.stringify(golden.today));
  payload.snapshot_id = 'opaque'; payload.analysis_version = 'future-analysis';
  payload.data.physiology.hrv_ms.metric.value = 0;
  payload.data.quality_codes = ['future_code'];
  expect(validateToday(payload)).toBe(payload);
  payload.data.physiology.hrv_ms.metric.value = null;
  payload.data.physiology.hrv_ms.metric.availability = 'withheld';
  expect(validateToday(payload)).toBe(payload);
  expect(validateToday(demo.today)).toEqual(demo.today);
  expect(validateSleep(demo.sleep)).toEqual(demo.sleep);
});
test.each(['metric', 'signals', 'timing', 'trend', 'need'])('malformed nested %s rejected', target => {
  const t = JSON.parse(JSON.stringify(golden.today)); const s = JSON.parse(JSON.stringify(golden.sleep));
  if (target === 'metric') t.data.physiology.hrv_ms.metric.value = NaN;
  if (target === 'signals') t.data.interpretation.signals = [];
  if (target === 'timing') t.data.last_sleep.timing.local_start = '2026-09-10T23:00:00';
  if (target === 'trend') s.data.trends.actual_sleep.daily_points[0].metric.unit = 'percent';
  if (target === 'need') delete s.data.metrics.nap_adjustment_ms;
  expect(() => { validateToday(t); validateSleep(s); }).toThrow('Report unavailable');
});
