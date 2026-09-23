import type { Metric } from '../types/api-v1';
import { availabilityCopy } from './copy';

export type Format = 'duration' | 'signedDuration' | 'percent' | 'hrv' | 'bpm' | 'breaths' | 'count';
export function duration(value: number, signed = false): string {
  if (!Number.isFinite(value)) return 'Not available';
  const sign = value < 0 ? '−' : signed && value > 0 ? '+' : '';
  if (Math.abs(value) > 0 && Math.abs(value) < 60_000) return `${sign}<1 min`;
  const minutes = Math.round(Math.abs(value) / 60_000);
  const hours = Math.floor(minutes / 60);
  return sign + (hours ? `${hours}h ${minutes % 60}m` : `${minutes} min`);
}
// Format the supplied backend difference; never recompute need from its components.
export function needDifferenceText(metric: Metric): string {
  if (metric.availability !== 'available' || metric.value === null || !Number.isFinite(metric.value)) {
    return `Sleep comparison: ${metricText(metric, 'duration')}`;
  }
  if (metric.value === 0) return 'You met your estimated sleep need';
  return `You slept ${duration(Math.abs(metric.value))} ${metric.value < 0 ? 'below' : 'above'} estimated need`;
}
export function numberText(value: number, format: Format): string {
  if (!Number.isFinite(value)) return 'Not available';
  if (format === 'duration' || format === 'signedDuration') return duration(value, format === 'signedDuration');
  const units = { percent: '%', hrv: ' ms', bpm: ' bpm', breaths: ' /min', count: '' };
  const digits = format === 'hrv' || format === 'breaths' ? 1 : 0;
  return new Intl.NumberFormat('en-GB', { maximumFractionDigits: digits }).format(value) + units[format];
}
export function metricText(metric: Metric, format: Format): string {
  if (metric.availability !== 'available') return availabilityCopy(metric.availability);
  return metric.value === null ? 'Not available' : numberText(metric.value, format);
}
export function dateText(value: string | null, short = false): string {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return 'Date unavailable';
  const date = new Date(`${value}T12:00:00Z`);
  if (!Number.isFinite(date.getTime())) return 'Date unavailable';
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'UTC', day: 'numeric', month: short ? 'short' : 'long', ...(short ? {} : { year: 'numeric' }),
  }).format(date);
}
// Preserve wall-clock digits from the recorded offset-aware timestamp, never the phone timezone.
export function timeText(value: string | null): string {
  const match = value?.match(/^\d{4}-\d{2}-\d{2}T([0-2]\d:[0-5]\d):[0-5]\d(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/);
  return match?.[1] && Number.isFinite(Date.parse(value!)) ? match[1] : 'Time unavailable';
}
