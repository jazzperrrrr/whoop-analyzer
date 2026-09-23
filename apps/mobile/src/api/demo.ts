import type { ApiClient } from './client';
import type { TodayResponse, SleepResponse } from '../types/api-v1';
import demo from './fixtures/visual_demo.json';

// Fictional, static presentation data only. The frozen MockApiClient stays independent.
// Contract validation checks this JSON separately; never loads a backend or personal data.
export class VisualDemoApiClient implements ApiClient {
  async getToday(): Promise<TodayResponse> { return JSON.parse(JSON.stringify(demo.today)); }
  async getLatestSleep(): Promise<SleepResponse> { return JSON.parse(JSON.stringify(demo.sleep)); }
}
