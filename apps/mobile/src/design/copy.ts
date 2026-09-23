import type { Metric, Interpretation, Error as ApiError } from '../types/api-v1';

function ownCopy(mapping: Readonly<Record<string, string>>, code: string, fallback: string): string {
  return (Object.hasOwn(mapping, code) ? mapping[code] : undefined) ?? fallback;
}

const reasons: Record<string, string> = {
  ambiguous_primary_sleep: 'More than one sleep record could describe this night.',
  primary_sleep_unavailable: 'This night’s sleep is not available.',
  sleep_not_scored: 'Sleep is still being scored.',
  recovery_not_scored: 'Recovery is still being scored.',
  sleep_component_unavailable: 'This part of your sleep was not recorded.',
  historical_value_unavailable: 'No measurement is available for this date.',
  nap_not_scored: 'This nap is still being scored.',
  nap_value_unavailable: 'This nap measurement is not available.',
  conflicting_measurements: 'Conflicting measurements need to be resolved.',
  measurement_unavailable: 'This measurement is not available.',
  latest_value_unavailable: 'The latest measurement is not available.',
  insufficient_baseline: 'More recent measurements are needed for a comparison.',
  comparison_undefined: 'A comparison is not available for this measurement.',
  above_baseline: 'Above your recent average.',
  below_baseline: 'Below your recent average.',
  near_baseline: 'Close to your recent average.',
  conflicting_signals: 'Some markers are above your recent baseline, while others are moving the opposite way.',
  insufficient_signal_coverage: 'More measurements are needed for an overall picture.',
  no_directional_signal: 'Your measurements are close to their recent averages.',
  signals_unavailable: 'Some measurements cannot yet be compared.',
  measurements_unverified: 'Some measurements could not be verified together.',
  training_data_unavailable: 'Training records are not available.',
  newer_sleep_incomplete: 'A newer sleep record is not complete yet.',
  sleep_chronology_uncertain: 'The order of some sleep records is uncertain.',
  sleep_duration_inconsistent: 'Recorded sleep durations do not fully agree.',
  sleep_history_incomplete: 'Some sleep history is incomplete.',
  ambiguous_sleep_history: 'Some past sleep records overlap.',
  ambiguous_physiology: 'Some measurements cannot be matched confidently.',
  recent_offset_transition: 'Recent records use different clock offsets.',
  mixed_offset_date: 'Records on this date use different clock offsets.',
  baseline_offset_transition: 'Your comparison period includes different clock offsets.',
  missing_sleep_components: 'Some sleep details are not available.',
  unrecorded_duration_present: 'Part of the recorded night has no sleep-stage data.',
};
export function reasonCopy(code: string): string {
  return ownCopy(reasons, code, 'Some details are not available for this report.');
}
const availability: Record<Metric['availability'], string> = {
  available: 'Available', pending: 'Awaiting score', missing: 'Not available', withheld: 'Not shown',
};
export function availabilityCopy(code: string): string {
  return ownCopy(availability, code, 'Not available');
}
const overall: Record<Interpretation['overall_state'], string> = {
  'strong positive': 'Your signals look especially favourable',
  'strong negative': 'Your signals are less favourable than usual',
  'generally positive': 'Your signals look favourable',
  'generally negative': 'Some signals are less favourable than usual',
  mixed: 'Mixed signals',
  'insufficient data': 'Your picture is still taking shape',
};
export function overallCopy(code: string): string {
  return ownCopy(overall, code, 'Your picture is still taking shape');
}
const errors: Record<ApiError['code'], string> = {
  local_access_only: 'This connection is not available on this device.',
  not_found: 'This report could not be found.', read_only: 'This action is not supported.',
  invalid_query: 'This report request could not be understood.',
  internal_error: 'We could not load your report.',
  snapshot_unavailable: 'Your report is temporarily unavailable.',
};
export function errorCopy(code: string): string {
  return ownCopy(errors, code, 'We could not load your report.');
}
