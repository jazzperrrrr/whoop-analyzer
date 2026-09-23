import demo from '../src/api/fixtures/visual_demo.json';
import golden from '../src/api/fixtures/api_v1_examples.json';
import { MockApiClient } from '../src/api/client';
import { VisualDemoApiClient } from '../src/api/demo';
import { sameSnapshot } from '../src/storage/snapshot';

test('visual demo is explicitly fictional, isolated from the frozen contract client and detached per read', async () => {
  expect(demo.notice).toMatch(/Completely fictional/);
  expect(demo.notice).toMatch(/No person/);
  const client = new VisualDemoApiClient();
  const today = await client.getToday();
  const sleep = await client.getLatestSleep();
  expect(sameSnapshot(today, sleep)).toBe(true);
  expect(today.snapshot_id).not.toBe(golden.today.snapshot_id);
  expect(today.last_successful_sync_at).toBeNull();
  expect(sleep.last_successful_sync_at).toBeNull();
  expect(await new MockApiClient().getLatestSleep()).toEqual(golden.sleep);
  today.data.physiology.hrv_ms.metric.value = 999;
  expect((await client.getToday()).data.physiology.hrv_ms.metric.value).toBe(64);
});

test('fictional stage totals, denominators, signed need and Today summary remain coherent', () => {
  const m = demo.sleep.data.metrics;
  expect(m.light_ms.value + m.deep_ms.value + m.rem_ms.value).toBe(m.actual_sleep_ms.value);
  expect(m.deep_ms.value + m.rem_ms.value).toBe(m.restorative_sleep_ms.value);
  expect(m.actual_sleep_ms.value + m.awake_ms.value + m.no_data_ms.value).toBe(m.in_bed_ms.value);
  expect(m.light_pct_actual_sleep.value + m.deep_pct_actual_sleep.value + m.rem_pct_actual_sleep.value).toBeCloseTo(100);
  expect(m.restorative_pct_actual_sleep.value).toBeCloseTo(m.restorative_sleep_ms.value / m.actual_sleep_ms.value * 100);
  expect(m.awake_pct_in_bed.value).toBeCloseTo(m.awake_ms.value / m.in_bed_ms.value * 100);
  expect(m.baseline_need_ms.value + m.sleep_debt_need_ms.value + m.recent_strain_need_ms.value + m.nap_adjustment_ms.value).toBe(m.total_need_ms.value);
  expect(m.actual_sleep_ms.value - m.total_need_ms.value).toBe(m.actual_minus_need_ms.value);
  expect(demo.today.data.last_sleep.actual_sleep).toEqual(m.actual_sleep_ms);
  expect(demo.today.data.last_sleep.sleep_need).toEqual(m.total_need_ms);
  expect(demo.today.data.physiology.sleep_performance.metric).toEqual(m.performance_pct);
  expect(m.actual_sleep_ms.value).toBeGreaterThanOrEqual(7 * 3_600_000);
  expect(m.actual_sleep_ms.value).toBeLessThanOrEqual(8.5 * 3_600_000);
});

test('fictional history varies, preserves absent/pending dates, and prior averages exclude the anchor', () => {
  const trend = demo.sleep.data.trends.actual_sleep;
  expect(new Set(trend.daily_points.map(p => p.metric.value)).size).toBeGreaterThan(5);
  expect(trend.daily_points.find(p => p.report_date === '2026-09-05')).toBeUndefined();
  expect(trend.daily_points.find(p => p.report_date === '2026-09-07')?.metric).toMatchObject({ value: null, availability: 'pending' });
  for (const summary of trend.prior_windows) {
    const values = trend.daily_points.filter(p => p.report_date >= summary.window_start && p.report_date <= summary.window_end && p.metric.value !== null).map(p => p.metric.value!);
    expect(summary.includes_anchor).toBe(false);
    expect(summary.window_end < trend.anchor_date).toBe(true);
    expect(summary.observed_days).toBe(values.length);
    expect(summary.average).toBeCloseTo(values.reduce((a, b) => a + b, 0) / values.length);
  }
});
