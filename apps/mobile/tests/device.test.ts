import { Platform } from 'react-native';
import golden from '../src/api/fixtures/api_v1_examples.json';
import { HttpApiClient, type Fetch } from '../src/api/http';
import { createApiClient, publicDataConfig } from '../src/api/mode';
import { deviceBase, type DeviceTransport } from '../src/api/device';
import { errorCopy } from '../src/design/copy';

const token = 'ab'.repeat(32); // Synthetic, predictable test input only.
const origin = 'https://device.synthetic.test';
const device = (credential = jest.fn(async (): Promise<string | null> => token)): DeviceTransport => ({ profile: 'native-device', approvedOrigin: origin, credential });
const reply = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;

test('native device requires explicit runtime injection; public config has no credential', () => {
  expect(createApiClient().mode).toBe('demo');
  expect(publicDataConfig()).not.toHaveProperty('device');
  expect(() => createApiClient({ mode: 'native-device', baseUrl: origin }, 'ios')).toThrow('Report unavailable');
  expect(createApiClient({ mode: 'native-device', baseUrl: origin, device: device() }, 'ios').mode).toBe('native-device');
});

test.each(['web', 'android'] as const)('device profile is rejected on %s', platform => {
  jest.replaceProperty(Platform, 'OS', platform);
  expect(() => createApiClient({ mode: 'native-device', baseUrl: origin, device: device() }, platform)).toThrow('Report unavailable');
  expect(() => new HttpApiClient(origin, jest.fn(), 8000, device())).toThrow('Report unavailable');
});

test.each(['demo', 'live-local'])('credential cannot be configured in %s', mode => {
  const credential = jest.fn(async () => token);
  expect(() => createApiClient({ mode, baseUrl: 'http://localhost:8000', device: device(credential) }, 'web')).toThrow('Report unavailable');
  expect(credential).not.toHaveBeenCalled();
});

test('device injects only into three GET API requests and does not serialize the provider', async () => {
  const credential = jest.fn(async () => token);
  const fetcher = jest.fn().mockResolvedValueOnce(reply({ status: 'ok', schema_version: 'v1' }))
    .mockResolvedValueOnce(reply(golden.today)).mockResolvedValueOnce(reply(golden.sleep));
  const client = new HttpApiClient(origin, fetcher, 8000, device(credential));
  await client.getHealth(); await client.getToday(); await client.getLatestSleep();
  expect(fetcher.mock.calls.map(c => c[0])).toEqual(['/api/v1/health', '/api/v1/today', '/api/v1/sleep/latest'].map(p => origin + p));
  for (const [, options] of fetcher.mock.calls) {
    expect(options).toMatchObject({ method: 'GET', credentials: 'omit', redirect: 'error', cache: 'no-store', headers: { Accept: 'application/json', Authorization: `Bearer ${token}` } });
    expect(options.body).toBeUndefined();
  }
  expect(credential).toHaveBeenCalledTimes(3);
  expect(JSON.stringify(client)).not.toContain(token);
  expect(JSON.stringify(client)).not.toContain('credential');
});

test.each(['/sync', '/oauth', '/token', '/api/v1/trends', '/assets/icon.png', '/index.bundle', 'https://other.synthetic.test/api/v1/today', '/api/v1/health?token=x'])('runtime guard forbids %s before credential lookup', async path => {
  const credential = jest.fn(async () => token); const fetcher = jest.fn();
  const client = new HttpApiClient(origin, fetcher, 8000, device(credential));
  await expect((client as unknown as { get: (path: string, check: (v: unknown) => unknown) => Promise<unknown> }).get(path, v => v)).rejects.toMatchObject({ code: 'invalid_configuration' });
  expect(credential).not.toHaveBeenCalled(); expect(fetcher).not.toHaveBeenCalled();
});

test.each(['http://device.synthetic.test', origin + '/', origin + '/api', origin + '?token=x', origin + '#x', 'https://user:pass@device.synthetic.test', 'https://other.synthetic.test', 'https://127.0.0.1', origin + ':443'])('device origin must exactly match approved HTTPS origin: %s', base => {
  expect(() => deviceBase(base, origin)).toThrow('Report unavailable');
});

test.each([null, '', 'wrong', token + '\r\nX-Header: value'])('missing/malformed synthetic credential fails safely (%#)', async value => {
  const fetcher = jest.fn();
  await expect(new HttpApiClient(origin, fetcher, 8000, device(jest.fn(async () => value))).getHealth()).rejects.toMatchObject({ code: 'local_access_only' });
  expect(fetcher).not.toHaveBeenCalled();
});

test('credential, network and backend exceptions do not leak secrets or log details', async () => {
  const logs = [jest.spyOn(console, 'log'), jest.spyOn(console, 'warn'), jest.spyOn(console, 'error')];
  const cases: Array<[Fetch, DeviceTransport]> = [
    [jest.fn(), device(jest.fn(async () => { throw new Error(token); }))],
    [jest.fn().mockRejectedValue(new Error(token)), device()],
    [jest.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({ code: 'local_access_only', message: token, retryable: false, request_id: token }) }), device()],
  ];
  for (const [fetcher, profile] of cases) {
    const error = await new HttpApiClient(origin, fetcher, 8000, profile).getHealth().catch(e => e);
    expect(String(error)).not.toContain(token); expect(JSON.stringify(error)).not.toContain(token);
    expect(errorCopy(error.code)).not.toContain(token);
  }
  logs.forEach(log => expect(log).not.toHaveBeenCalled());
});

test('credential timeout cannot trigger a late authenticated request', async () => {
  jest.useFakeTimers();
  try {
    let resolve!: (v: string) => void;
    const credential = jest.fn(() => new Promise<string>(done => { resolve = done; }));
    const fetcher = jest.fn();
    const pending = new HttpApiClient(origin, fetcher, 50, device(credential)).getHealth();
    const check = expect(pending).rejects.toMatchObject({ code: 'request_timeout' });
    await jest.advanceTimersByTimeAsync(50); await check;
    resolve(token); await Promise.resolve(); await Promise.resolve();
    expect(fetcher).not.toHaveBeenCalled();
  } finally { jest.useRealTimers(); }
});

test('live-local still sends Accept only, never Authorization', async () => {
  const fetcher = jest.fn().mockResolvedValue(reply({ status: 'ok', schema_version: 'v1' }));
  await new HttpApiClient('http://localhost:8000', fetcher).getHealth();
  expect(fetcher.mock.calls[0]![1].headers).toEqual({ Accept: 'application/json' });
});
