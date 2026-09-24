import type { Health, TodayResponse, SleepResponse } from '../types/api-v1';
import fixtures from './fixtures/api_v1_examples.json';
import visualDemo from './fixtures/visual_demo.json';

export interface ApiClient {
  readonly mode?: 'demo' | 'live-local' | 'native-device';
  getHealth?(): Promise<Health>;
  getToday(): Promise<TodayResponse>;
  getLatestSleep(): Promise<SleepResponse>;
}
// The Python contract check validates the copied JSON against the frozen response models.
// Copy per call so a consumer can never mutate the bundled fixture for later requests.
export class MockApiClient implements ApiClient {
  readonly mode = 'demo' as const;
  constructor(private readonly fixture: 'golden' | 'visual-demo' = 'golden') {}
  async getToday(): Promise<TodayResponse> { return JSON.parse(JSON.stringify((this.fixture === 'golden' ? fixtures : visualDemo).today)); }
  async getLatestSleep(): Promise<SleepResponse> { return JSON.parse(JSON.stringify((this.fixture === 'golden' ? fixtures : visualDemo).sleep)); }
}
