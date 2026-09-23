import { Platform } from 'react-native';
import { MockApiClient, type ApiClient } from './client';
import { HttpApiClient } from './http';
import { ApiFailure } from './errors';

export type DataMode = 'demo' | 'live-local';
export type DataConfig = { mode?: string; baseUrl?: string };
export function createApiClient(config: DataConfig = {}, platform = Platform.OS): ApiClient {
  const mode = config.mode ?? 'demo';
  if (mode === 'demo') return new MockApiClient('visual-demo');
  if (mode !== 'live-local' || platform !== 'web' || !config.baseUrl) throw new ApiFailure('invalid_configuration', false);
  return new HttpApiClient(config.baseUrl);
}
export function publicDataConfig(): DataConfig {
  // Expo statically replaces these two literal references. They must never contain secrets.
  return { mode: process.env.EXPO_PUBLIC_DATA_MODE, baseUrl: process.env.EXPO_PUBLIC_API_BASE_URL };
}
