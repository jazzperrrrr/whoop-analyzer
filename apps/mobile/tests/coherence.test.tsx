import { Pressable, Text } from 'react-native';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { MockApiClient } from '../src/api/client';
import { loadSnapshot, MemorySnapshotCache } from '../src/storage/snapshot';
import { ApiProvider, useReports } from '../src/api/provider';
import TodayRoute from '../app/index';

async function pairClient(mismatches: number) {
  const mock = new MockApiClient();
  const today = await mock.getToday(); const sleep = await mock.getLatestSleep();
  let reads = 0;
  return { getToday: jest.fn(async () => ({ ...today, generated_at: '2026-09-12T13:00:00Z' })), getLatestSleep: jest.fn(async () => ({ ...sleep, snapshot_id: reads++ < mismatches ? 'changed' : today.snapshot_id })) };
}
test('matching identity accepts different generation timestamps', async () => {
  const client = await pairClient(0); const cache = new MemorySnapshotCache();
  const snapshot = await loadSnapshot(client, cache);
  expect(cache.read()).toBe(snapshot); expect(client.getToday).toHaveBeenCalledTimes(1);
  expect(snapshot.cached_at).not.toBe(snapshot.today.generated_at);
});
test('Today completes before Sleep starts so the client does not contend with its own Windows read lock', async () => {
  const mock = new MockApiClient();
  const today = await mock.getToday();
  let release!: (value: typeof today) => void;
  const client = { getToday: jest.fn(() => new Promise<typeof today>(resolve => { release = resolve; })), getLatestSleep: jest.fn(() => mock.getLatestSleep()) };
  const pending = loadSnapshot(client, new MemorySnapshotCache());
  await Promise.resolve();
  expect(client.getToday).toHaveBeenCalledTimes(1);
  expect(client.getLatestSleep).not.toHaveBeenCalled();
  release(today); await pending;
  expect(client.getLatestSleep).toHaveBeenCalledTimes(1);
});
test('one mismatched pair is discarded and both endpoints are reacquired once', async () => {
  const client = await pairClient(1); const cache = new MemorySnapshotCache();
  await loadSnapshot(client, cache);
  expect(client.getToday).toHaveBeenCalledTimes(2); expect(client.getLatestSleep).toHaveBeenCalledTimes(2);
  expect(cache.read()!.today.snapshot_id).toBe(cache.read()!.sleep.snapshot_id);
});
test('repeated mismatch rejects without writing a cache or looping forever', async () => {
  const client = await pairClient(Infinity); const cache = new MemorySnapshotCache();
  await expect(loadSnapshot(client, cache)).rejects.toMatchObject({ code: 'incoherent_snapshot' });
  expect(client.getToday).toHaveBeenCalledTimes(2); expect(cache.read()).toBeNull();
});
function Reload() { const { retry } = useReports(); return <Pressable onPress={retry}><Text>Reload report</Text></Pressable>; }
test('last coherent pair stays visible with an explicit failure notice after mismatch', async () => {
  const client = await pairClient(0);
  render(<ApiProvider client={client}><Reload/><TodayRoute/></ApiProvider>);
  expect(await screen.findByText('Good morning')).toBeTruthy();
  const sleep = await new MockApiClient().getLatestSleep();
  client.getLatestSleep.mockResolvedValue({ ...sleep, snapshot_id: 'changed' });
  fireEvent.press(screen.getByText('Reload report'));
  expect(await screen.findByText('Showing the last loaded report.')).toBeTruthy();
  expect(screen.getByText('Good morning')).toBeTruthy();
  expect(screen.getByLabelText('Recovery: 70%')).toBeTruthy();
});
test('no cache plus repeated mismatch displays unavailable/retry state', async () => {
  render(<ApiProvider client={await pairClient(Infinity)}><TodayRoute/></ApiProvider>);
  expect(await screen.findByText('The local snapshot changed while loading. Please try again.')).toBeTruthy();
  expect(screen.queryByText('Good morning')).toBeNull();
  expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
});
