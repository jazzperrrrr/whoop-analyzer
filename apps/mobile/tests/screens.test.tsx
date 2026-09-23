import { fireEvent, render, screen } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';
import RootLayout from '../app/_layout';
import TodayRoute from '../app/index';
import SleepRoute from '../app/sleep';
import { MockApiClient } from '../src/api/client';
import { ApiProvider } from '../src/api/provider';
import { MetricValue } from '../src/components/ui';
import { SleepScreen } from '../src/features/sleep/SleepScreen';
import { View, StyleSheet } from 'react-native';
import { VisualDemoApiClient } from '../src/api/demo';
import { TodayScreen } from '../src/features/today/TodayScreen';

test.each([
  ['available', 0, '0%'], ['pending', null, 'Awaiting score'],
  ['missing', null, 'Not available'], ['withheld', null, 'Not shown'],
] as const)('Metric component renders %s accessibly', (availability, value, copy) => {
  render(<MetricValue label="Recovery" format="percent" metric={{ value, unit: 'percent', origin: 'whoop', availability, reason_codes: availability === 'available' ? [] : ['future_reason'] }}/>);
  expect(screen.getByLabelText(`Recovery: ${copy}`)).toBeTruthy();
  expect(screen.queryByText('future_reason')).toBeNull();
});
test('Today synthetic screen navigates to the real Sleep route', async () => {
  const navigation = renderRouter({ _layout: RootLayout, index: TodayRoute, sleep: SleepRoute }, { initialUrl: '/' });
  expect(await screen.findByText('Good morning')).toBeTruthy();
  for (const text of ['Recovery', 'HRV', 'Resting HR', 'Last Sleep']) expect(screen.getByText(text)).toBeTruthy();
  expect(screen.getByText('SAMPLE DATA · DEMO')).toBeTruthy();
  expect(screen.getByLabelText('Recovery: 72%')).toBeTruthy();
  fireEvent.press(screen.getByLabelText('Last Sleep, view sleep details'));
  expect(await screen.findByText('Your night')).toBeTruthy();
  expect(navigation.getPathname()).toBe('/sleep');
  for (const text of ['Sleep Stages', 'Restorative', 'Awake', 'Sleep debt', 'Nap adjustment', 'Respiratory Rate', 'Cycles', 'Disturbances', 'Recent sleep']) expect(screen.getByText(text)).toBeTruthy();
});
test('Sleep score content stays in natural flow before stages, including multiline pending text', async () => {
  const response = await new VisualDemoApiClient().getLatestSleep();
  response.data.metrics.consistency_pct = { ...response.data.metrics.consistency_pct, value: null, availability: 'pending', reason_codes: ['future_long_reason'] };
  const rendered = render(<SleepScreen response={response}/>);
  const hero = screen.getByTestId('sleep-hero');
  const scores = screen.getByTestId('sleep-scores');
  const stages = screen.getByTestId('section-Sleep Stages');
  // Composite wrappers are omitted in the rendered tree; compare actual layout siblings.
  const tree = JSON.stringify(rendered.toJSON());
  expect(tree.indexOf('sleep-hero')).toBeLessThan(tree.indexOf('section-Sleep Stages'));
  expect(stages.findAllByType(View).includes(hero)).toBe(false);
  expect(screen.getByLabelText('Consistency: Awaiting score')).toBeTruthy();
  // A percentage flexBasis on a metric inside a vertical container caused the overlap.
  // Keep all hero wrappers intrinsically tall, without clipping or positioned content.
  for (const node of [hero, scores, ...hero.findAllByType(View)]) {
    const style = StyleSheet.flatten(node.props.style) ?? {};
    expect(style.position).not.toBe('absolute');
    expect(style.height).toBeUndefined();
    expect(style.maxHeight).toBeUndefined();
    expect(style.overflow).not.toBe('hidden');
    expect(style.flexBasis).not.toEqual(expect.stringMatching(/%$/));
  }
});
test('demo Sleep shows supplied need difference and leaves pending/absent trend dates without bars', async () => {
  const response = await new VisualDemoApiClient().getLatestSleep();
  render(<SleepScreen response={response}/>);
  expect(screen.getByLabelText('Actual Sleep: 7h 12m')).toBeTruthy();
  expect(screen.getByText('You slept 1h 22m below estimated need')).toBeTruthy();
  expect(screen.getByText('+42 min')).toBeTruthy();
  expect(screen.getByText('−14 min')).toBeTruthy();
  expect(screen.queryByTestId('trend-bar-2026-09-07')).toBeNull();
  expect(screen.queryByTestId('trend-bar-2026-09-05')).toBeNull();
  expect(screen.getByTestId('trend-bar-2026-09-10')).toBeTruthy();
});
test('Today keeps unknown and neutral interpretation copy safe without deriving a signal', async () => {
  const response = await new VisualDemoApiClient().getToday();
  response.data.interpretation.reason_codes = ['no_directional_signal'];
  const view = render(<TodayScreen response={response}/>);
  expect(screen.getByText('Your measurements are close to their recent averages.')).toBeTruthy();
  expect(screen.queryByText(/Some markers are above/)).toBeNull();
  response.data.interpretation.reason_codes = ['__proto__'];
  view.rerender(<TodayScreen response={response}/>);
  expect(screen.getByText('Some details are not available for this report.')).toBeTruthy();
  expect(screen.queryByText('__proto__')).toBeNull();
});
test('Sleep presents unavailable observations and keeps notes collapsed', async () => {
  const response = await new MockApiClient().getLatestSleep();
  response.data.metrics.consistency_pct = { value: null, unit: 'percent', origin: 'whoop', availability: 'pending', reason_codes: ['sleep_not_scored'] };
  response.data.quality_codes = ['future_notice'];
  render(<SleepScreen response={response}/>);
  expect(screen.getByText('Awaiting score')).toBeTruthy();
  expect(screen.queryByText('Some details are not available for this report.')).toBeNull();
  fireEvent.press(screen.getByRole('button', { name: /About these measurements/ }));
  expect(screen.getByText('Some details are not available for this report.')).toBeTruthy();
  expect(screen.queryByText('future_notice')).toBeNull();
});
test('unexpected client errors are sanitized and retry can recover', async () => {
  const mock = new MockApiClient();
  const client = { getToday: jest.fn().mockRejectedValueOnce(new Error('secret/path')).mockImplementation(() => mock.getToday()), getLatestSleep: () => mock.getLatestSleep() };
  render(<ApiProvider client={client}><TodayRoute/></ApiProvider>);
  expect(await screen.findByText('Your report couldn’t be loaded')).toBeTruthy();
  expect(screen.queryByText('secret/path')).toBeNull();
  fireEvent.press(screen.getByRole('button', { name: 'Try again' }));
  expect(await screen.findByText('Good morning')).toBeTruthy();
});
