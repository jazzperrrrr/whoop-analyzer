import type { ApiClient } from './client';
import { ApiFailure } from './errors';
import { validateError, validateHealth, validateSleep, validateToday } from './validate';
import { Platform } from 'react-native';
import { deviceBase, type DeviceTransport } from './device';

const paths = ['/api/v1/health', '/api/v1/today', '/api/v1/sleep/latest'] as const;

export type Fetch = typeof fetch;
export function loopbackBase(value: string): string {
  // Match the original input before URL normalization; reject aliases, credentials and paths.
  if (!/^http:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?\/?$/.test(value)) throw new ApiFailure('invalid_configuration', false);
  try { return new URL(value).origin; } catch { throw new ApiFailure('invalid_configuration', false); }
}
export class HttpApiClient implements ApiClient {
  readonly mode: 'live-local' | 'native-device';
  private readonly base: string;
  #device?: DeviceTransport;
  constructor(baseUrl: string, private readonly fetcher: Fetch = globalThis.fetch.bind(globalThis), private readonly timeoutMs = 8000, device?: DeviceTransport) {
    if (device && (device.profile !== 'native-device' || Platform.OS !== 'ios' || typeof device.credential !== 'function')) throw new ApiFailure('invalid_configuration', false);
    this.mode = device ? 'native-device' : 'live-local';
    this.base = device ? deviceBase(baseUrl, device.approvedOrigin) : loopbackBase(baseUrl);
    this.#device = device ? { ...device } : undefined;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new ApiFailure('invalid_configuration', false);
  }
  private async get<T>(path: '/api/v1/health' | '/api/v1/today' | '/api/v1/sleep/latest', validate: (value: unknown) => T): Promise<T> {
    // Runtime guard as well as the TS union: credentials must never reach other paths.
    if (!paths.includes(path)) throw new ApiFailure('invalid_configuration', false);
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => { reject(new ApiFailure('request_timeout')); controller.abort(); }, this.timeoutMs);
    });
    const request = async () => {
      const headers: Record<string, string> = { Accept: 'application/json' };
      if (this.#device) {
        let token: string | null;
        try { token = await this.#device.credential(); }
        catch { throw new ApiFailure('local_access_only', false); }
        if (!token || !/^[0-9a-f]{64}$/.test(token)) throw new ApiFailure('local_access_only', false);
        headers.Authorization = `Bearer ${token}`;
      }
      // A slow credential provider may finish after the timeout; never send afterward.
      if (controller.signal.aborted) throw new ApiFailure('request_timeout');
      const response = await this.fetcher(this.base + path, { method: 'GET', credentials: 'omit', redirect: 'error', cache: 'no-store', referrerPolicy: 'no-referrer', headers, signal: controller.signal });
      let body: unknown;
      try { body = await response.json(); } catch { throw new ApiFailure('invalid_response', false); }
      if (!response.ok) {
        const error = validateError(body);
        throw new ApiFailure(error.code, error.retryable);
      }
      return validate(body);
    };
    try { return await Promise.race([request(), timeout]); }
    catch (error) { throw error instanceof ApiFailure ? error : new ApiFailure('connection_failed'); }
    finally { clearTimeout(timer); }
  }
  getHealth() { return this.get('/api/v1/health', validateHealth); }
  getToday() { return this.get('/api/v1/today', validateToday); }
  getLatestSleep() { return this.get('/api/v1/sleep/latest', validateSleep); }
}
