import type { Metric } from '../src/types/api-v1';
import { dateText, duration, metricText, needDifferenceText, numberText, timeText } from '../src/design/format';
import { reasonCopy, errorCopy, overallCopy, availabilityCopy } from '../src/design/copy';
import { MockApiClient } from '../src/api/client';
import { loadSnapshot, MemorySnapshotCache, sameSnapshot } from '../src/storage/snapshot';

const value: Metric = { value: 0, unit: 'ms', origin: 'whoop', availability: 'available', reason_codes: [] };
test('zero is an observation, not missing', () => {
  expect(metricText(value, 'duration')).toBe('0 min');
  expect(metricText(value, 'hrv')).toBe('0 ms');
  expect(metricText({ ...value, value: null, availability: 'missing' }, 'duration')).toBe('Not available');
});
test.each([
  ['pending', 'Awaiting score'], ['missing', 'Not available'], ['withheld', 'Not shown'],
] as const)('%s keeps its own availability copy', (availability, expected) => {
  expect(metricText({ ...value, availability, value: null }, 'percent')).toBe(expected);
});
test('invalid numbers do not escape into copy', () => {
  for (const v of [NaN, Infinity, -Infinity]) expect(metricText({ ...value, value: v }, 'hrv')).toBe('Not available');
  expect(metricText({ ...value, value: null }, 'duration')).toBe('Not available');
});
test('duration preserves signs, zero and sub-minute synthetic observations', () => {
  expect(duration(28_800_000)).toBe('8h 0m');
  expect(duration(5_400_000)).toBe('1h 30m');
  expect(duration(0, true)).toBe('0 min');
  expect(duration(-900_000, true)).toBe('−15 min');
  expect(duration(900_000, true)).toBe('+15 min');
  expect(duration(8500)).toBe('<1 min');
  expect(duration(-8500, true)).toBe('−<1 min');
});
test('formats units without recalculating observations', () => {
  expect(numberText(85.2, 'percent')).toBe('85%');
  expect(numberText(52.65, 'hrv')).toBe('52.7 ms');
  expect(numberText(48, 'bpm')).toBe('48 bpm');
  expect(numberText(14.25, 'breaths')).toBe('14.3 /min');
  expect(numberText(0, 'count')).toBe('0');
});
test('recorded wall times and calendar dates ignore the phone timezone', () => {
  expect(timeText('2026-09-10T23:45:00-07:00')).toBe('23:45');
  expect(timeText('2026-09-11T07:05:00+05:30')).toBe('07:05');
  expect(timeText('2026-09-11T07:05:00Z')).toBe('07:05');
  expect(timeText('2026-09-11T07:05:00')).toBe('Time unavailable');
  expect(timeText(null)).toBe('Time unavailable');
  expect(dateText('2026-09-10')).toBe('10 September 2026');
  expect(dateText(null)).toBe('Date unavailable');
});
test('stable codes use central copy; unknown future codes never leak', () => {
  expect(reasonCopy('sleep_not_scored')).toBe('Sleep is still being scored.');
  expect(reasonCopy('below_baseline')).toBe('Below your recent average.');
  expect(reasonCopy('future_private_code')).toBe('Some details are not available for this report.');
  expect(errorCopy('internal_error')).toBe('We could not load your report.');
  expect(errorCopy('private exception')).toBe('We could not load your report.');
  expect(overallCopy('strong negative')).not.toBe('strong negative');
});
test.each(['constructor', 'toString', '__proto__', 'future_unknown_code'])('copy lookups safely reject %s', code => {
  expect(reasonCopy(code)).toBe('Some details are not available for this report.');
  expect(errorCopy(code)).toBe('We could not load your report.');
  expect(availabilityCopy(code)).toBe('Not available');
  expect(overallCopy(code)).toBe('Your picture is still taking shape');
});
test('own-property lookups preserve known copy', () => {
  expect(reasonCopy('sleep_not_scored')).toBe('Sleep is still being scored.');
  expect(errorCopy('snapshot_unavailable')).toBe('Your report is temporarily unavailable.');
  expect(availabilityCopy('pending')).toBe('Awaiting score');
  expect(overallCopy('mixed')).toBe('Mixed signals');
});
test('need difference formats the supplied value with safe null, zero and sign behavior', () => {
  expect(needDifferenceText({ ...value, value: -4_920_000 })).toBe('You slept 1h 22m below estimated need');
  expect(needDifferenceText({ ...value, value: 600_000 })).toBe('You slept 10 min above estimated need');
  expect(needDifferenceText(value)).toBe('You met your estimated sleep need');
  expect(needDifferenceText({ ...value, value: null })).toBe('Sleep comparison: Not available');
  expect(needDifferenceText({ ...value, value: NaN })).toBe('Sleep comparison: Not available');
  expect(needDifferenceText({ ...value, value: null, availability: 'pending' })).toBe('Sleep comparison: Awaiting score');
});
test('snapshot identity uses all three fields, not generated_at', async () => {
  const today = await new MockApiClient().getToday();
  expect(sameSnapshot(today, { ...today, generated_at: '2099-01-01T00:00:00Z' } as typeof today)).toBe(true);
  expect(sameSnapshot(today, { ...today, analysis_version: 'other' })).toBe(false);
  expect(sameSnapshot(today, { ...today, snapshot_id: 'other' })).toBe(false);
  expect(sameSnapshot(today, { ...today, schema_version: 'v2' } as unknown as typeof today)).toBe(false);
});
test('cache only publishes coherent pairs and does not replace a good pair on mismatch', async () => {
  const cache = new MemorySnapshotCache();
  const client = new MockApiClient();
  const first = await loadSnapshot(client, cache);
  expect(cache.read()).toBe(first);
  const sleep = await client.getLatestSleep();
  await expect(loadSnapshot({ getToday: () => client.getToday(), getLatestSleep: async () => ({ ...sleep, snapshot_id: 'changed' }) }, cache)).rejects.toThrow('Incoherent snapshot');
  expect(cache.read()).toBe(first);
  cache.clear();
  expect(cache.read()).toBeNull();
});
test('mock consumers cannot mutate subsequent fixture responses', async () => {
  const client = new MockApiClient();
  const first = await client.getToday();
  first.data.physiology.hrv_ms.metric.value = 999;
  expect((await client.getToday()).data.physiology.hrv_ms.metric.value).not.toBe(999);
});
