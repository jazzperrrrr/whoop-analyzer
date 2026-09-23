import type { TodayResponse, SleepResponse } from '../types/api-v1';
import fixtures from './fixtures/api_v1_examples.json';

export interface ApiClient {
  getToday(): Promise<TodayResponse>;
  getLatestSleep(): Promise<SleepResponse>;
}
// The Python contract check validates the copied JSON against the frozen response models.
// Copy per call so a consumer can never mutate the bundled fixture for later requests.
export class MockApiClient implements ApiClient {
  async getToday(): Promise<TodayResponse> { return JSON.parse(JSON.stringify(fixtures.today)); }
  async getLatestSleep(): Promise<SleepResponse> { return JSON.parse(JSON.stringify(fixtures.sleep)); }
}
// A later HttpApiClient implements this interface and validates unknown JSON at its boundary.
// No URL, fetch, credentials, WHOOP SDK or HTTP implementation is enabled in Phase 6C.
