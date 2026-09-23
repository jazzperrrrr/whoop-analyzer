import type { ApiClient } from './client';
import { ApiFailure } from './errors';
import { validateError, validateHealth, validateSleep, validateToday } from './validate';

export type Fetch = typeof fetch;
export function loopbackBase(value: string): string {
  // Match the original input before URL normalization; reject aliases, credentials and paths.
  if (!/^http:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?::[0-9]{1,5})?\/?$/.test(value)) throw new ApiFailure('invalid_configuration', false);
  try { return new URL(value).origin; } catch { throw new ApiFailure('invalid_configuration', false); }
}
export class HttpApiClient implements ApiClient {
  readonly mode = 'live-local' as const;
  private readonly base: string;
  constructor(baseUrl: string, private readonly fetcher: Fetch = globalThis.fetch.bind(globalThis), private readonly timeoutMs = 8000) {
    this.base = loopbackBase(baseUrl);
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new ApiFailure('invalid_configuration', false);
  }
  private async get<T>(path: '/api/v1/health' | '/api/v1/today' | '/api/v1/sleep/latest', validate: (value: unknown) => T): Promise<T> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => { reject(new ApiFailure('request_timeout')); controller.abort(); }, this.timeoutMs);
    });
    const request = async () => {
      const response = await this.fetcher(this.base + path, { method: 'GET', credentials: 'omit', redirect: 'error', cache: 'no-store', referrerPolicy: 'no-referrer', headers: { Accept: 'application/json' }, signal: controller.signal });
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
