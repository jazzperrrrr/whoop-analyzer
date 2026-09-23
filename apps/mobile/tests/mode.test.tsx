import { render, screen, fireEvent } from '@testing-library/react-native';
import { createApiClient } from '../src/api/mode';
import { MockApiClient } from '../src/api/client';
import { HttpApiClient } from '../src/api/http';
import { ApiProvider } from '../src/api/provider';
import TodayRoute from '../app/index';
import golden from '../src/api/fixtures/api_v1_examples.json';
import { renderRouter } from 'expo-router/testing-library';
import RootLayout from '../app/_layout';
import SleepRoute from '../app/sleep';
import * as modeConfig from '../src/api/mode';

test('default mode is demo and explicit demo preserves the visual fixture through MockApiClient', async () => {
  expect(createApiClient()).toBeInstanceOf(MockApiClient);
  const client = createApiClient({ mode: 'demo' });
  expect(client).toBeInstanceOf(MockApiClient);
  expect((await client.getLatestSleep()).data.metrics.actual_sleep_ms.value).toBe(25_920_000);
});
test('explicit live-local creates HttpApiClient on web', () => {
  expect(createApiClient({ mode: 'live-local', baseUrl: 'http://127.0.0.1:8000' }, 'web')).toBeInstanceOf(HttpApiClient);
});
test.each(['live', '', 'DEMO', '__proto__'])('invalid mode %s rejects safely', mode => {
  expect(() => createApiClient({ mode }, 'web')).toThrow('Report unavailable');
});
test.each(['ios', 'android'] as const)('live-local rejects native %s', platform => {
  expect(() => createApiClient({ mode: 'live-local', baseUrl: 'http://127.0.0.1:8000' }, platform)).toThrow('Report unavailable');
});
test('missing live URL never falls back to demo', () => {
  expect(() => createApiClient({ mode: 'live-local' }, 'web')).toThrow('Report unavailable');
});
test('configuration failure renders a safe state without fictional values', async () => {
  render(<ApiProvider config={{ mode: 'invalid' }}><TodayRoute/></ApiProvider>);
  expect(await screen.findByText('Your report couldn’t be loaded')).toBeTruthy();
  expect(screen.getByText('DATA UNAVAILABLE')).toBeTruthy();
  expect(screen.queryByText('SAMPLE DATA · DEMO')).toBeNull();
  expect(screen.queryByText('Good morning')).toBeNull();
});
test('failed live connection never shows demo; retry can load only HTTP responses', async () => {
  const fetcher = jest.fn().mockRejectedValueOnce(new Error('private URL'));
  const client = new HttpApiClient('http://localhost:8000', fetcher);
  render(<ApiProvider client={client}><TodayRoute/></ApiProvider>);
  expect(await screen.findByText(/Cannot connect to the local API/)).toBeTruthy();
  expect(screen.getByText('LOCAL DATA')).toBeTruthy();
  expect(screen.queryByText('SAMPLE DATA · DEMO')).toBeNull();
  expect(screen.queryByText('Good morning')).toBeNull();
  fetcher.mockImplementation(async (url: string) => ({ ok: true, json: async () => url.endsWith('/health') ? { schema_version: 'v1', status: 'ok' } : url.endsWith('/today') ? golden.today : golden.sleep }));
  fireEvent.press(screen.getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText('Good morning')).toBeTruthy();
  expect(screen.getByLabelText('Recovery: 70%')).toBeTruthy();
  expect(screen.queryByText('SAMPLE DATA · DEMO')).toBeNull();
});
test('live-local Today to Sleep navigates using one coherent HTTP pair with no demo label', async () => {
  const fetcher = jest.fn().mockImplementation(async (url: string) => ({ ok: true, json: async () => url.endsWith('/health') ? { schema_version: 'v1', status: 'ok' } : url.endsWith('/today') ? golden.today : golden.sleep }));
  jest.spyOn(modeConfig, 'createApiClient').mockReturnValue(new HttpApiClient('http://localhost:8000', fetcher));
  const navigation = renderRouter({ _layout: RootLayout, index: TodayRoute, sleep: SleepRoute }, { initialUrl: '/' });
  expect(await screen.findByText('Good morning')).toBeTruthy();
  expect(screen.getByText('LOCAL DATA')).toBeTruthy();
  expect(screen.queryByText('SAMPLE DATA · DEMO')).toBeNull();
  for (const label of ['Recovery: 70%', 'HRV: 60 ms', 'Resting HR: 53 bpm', 'Sleep Performance: 80%']) expect(screen.getByLabelText(label)).toBeTruthy();
  fireEvent.press(screen.getByLabelText('Last Sleep, view sleep details'));
  expect(await screen.findByText('Your night')).toBeTruthy();
  expect(navigation.getPathname()).toBe('/sleep');
  expect(screen.getByLabelText('Actual Sleep: <1 min')).toBeTruthy();
  expect(screen.getByText('LOCAL DATA')).toBeTruthy();
  expect(screen.queryByText('SAMPLE DATA · DEMO')).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(3);
});
