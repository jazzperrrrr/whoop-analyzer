# Mobile API V1 contract (Phase 6B)

This is the frozen wire contract for the initial Today -> Sleep mobile slice.
Product dataclasses are independent of FastAPI. Existing domain selection,
calculations, baseline eligibility, readiness, Dashboard and Sync remain authoritative.
All fields described here are required response keys; nullable means the key is
present with a null value, not omitted. No client should parse backend prose.

## Endpoints and OpenAPI

| Method/path | operation_id | 200 component |
| --- | --- | --- |
| GET /api/v1/health | get_health | Health |
| GET /api/v1/today | get_today | TodayResponse |
| GET /api/v1/sleep/latest | get_latest_sleep | SleepResponse |
| GET /api/v1/trends | get_trends | TrendResponse |

There are no other routes, including public OpenAPI/docs, write, sync or auth routes.
`app.openapi()` generates OpenAPI 3.1 in memory without reading health data.
It is suitable for later `openapi-typescript` generation; no Node tool is required
by this backend. Health only returns `status: "ok"` and `schema_version: "v1"`;
it does not establish dataset readiness. Canonical paths have no trailing slash;
FastAPI redirects a trailing slash with HTTP 307.

Stable component names:

`ActivitySummary`, `Baseline`, `Error`, `Freshness`, `Health`, `Interpretation`,
`InterpretationSignals`, `Metric`, `Nap`, `Physiology`, `Signal`, `SleepMetrics`,
`SleepReport`, `SleepResponse`, `SleepSummary`, `SleepTrends`, `Timing`,
`TodayPhysiology`, `TodayReport`, `TodayResponse`, `TrainingSummary`, `TrendPoint`,
`TrendReport`, `TrendResponse`.

## Envelope, snapshot and versions

TodayResponse, SleepResponse and TrendResponse contain exactly:

| Field | Contract |
| --- | --- |
| schema_version | Literal `v1` |
| analysis_version | Currently `phase5c-v1`; analysis policy version |
| snapshot_id | Opaque committed-input fingerprint; equality comparisons only |
| generated_at | UTC date-time when the response snapshot was generated |
| report_date | Selected report date, or null |
| latest_available_morning | Latest selectable morning in this snapshot, or null |
| collection_freshness | Freshness object |
| last_successful_sync_at | Nullable date-time; always null while genuinely unknown |
| data | The endpoint's named product report |

Here "committed input" means published local dataset input, **not a Git commit**.
Snapshot hashing is unchanged: an opaque SHA-256 over the fixed six normalized
input content fingerprints, including classifications. No paths or added entity
IDs are embedded. Byte-level input changes can change the ID even if measurements
are semantically identical. A coherent read takes the existing OS read lock and
checks fingerprints around acquisition; an interrupted publication blocks reads.

Mobile reconciliation identity is `(schema_version, analysis_version, snapshot_id)`.
Today and Sleep with that identity share inputs. Separate requests can encounter
different snapshots and must be reconciled, not silently combined. A snapshot ID
is not an entity ID, collection timestamp, Git revision or exact response ETag.

Generated time is not WHOOP collection time, the report date, or phone cache time.
Identical snapshot IDs can have different generation times and freshness metadata.
Freshness has `status: "unknown"`, nullable integer
`days_since_latest_available_morning`, boolean `may_be_out_of_date`, and
`collection_time_known: false`. Report age is relative to the backend's local
calendar date. False `may_be_out_of_date` does not prove collection completeness;
it is also false when no report date exists. Recorded offset differences and
incomplete newer sleep notices must not be mistaken for confirmed travel or sync.

After freeze, removed/renamed fields, changed units/meaning, incompatible
nullability or requiredness, and expansion/removal of closed enum values require
a new schema major. Keep `/api/v1` for this frozen contract. New optional fields
can be compatible when consumers tolerate additional fields. Reason and quality
codes are explicitly extensible strings: unknown codes get generic client text.
Never interpret an unknown state as available or positive. Change analysis_version
when selection, exclusion, weighting, derivation, thresholds or interpretation
semantics change. Schema naming, formatting and annotations alone do not change
analysis_version. Neither version should be interpreted as a Git version.

## Frozen enums

| Type | Exact values |
| --- | --- |
| Availability | available, missing, pending, withheld |
| Origin | whoop, derived |
| Signal state | positive, negative, neutral, insufficient data |
| Overall state | strong positive, strong negative, generally positive, generally negative, mixed, insufficient data |
| Coverage | full, partial, insufficient, unavailable |
| Sleep status | available, partial, unavailable, ambiguous |
| Collection freshness | unknown |
| Training calendar coverage | unknown |
| Training availability | available, missing (subset of Availability) |
| Health | ok |
| Unit | ms, percent, bpm, breaths/min, count |
| Window | 7, 14, 30 |
| Trend metric | actual_sleep, sleep_need, sleep_performance, sleep_efficiency, sleep_consistency, respiratory_rate, deep_sleep, rem_sleep, restorative_sleep |
| Error code | local_access_only, not_found, read_only, invalid_query, internal_error, snapshot_unavailable |

Enum wire values retain their spelling, including spaces in interpretation states.
No raw WHOOP scoring states are exposed.

## Metric

Required fields: `value`, `unit`, `origin`, `availability`, `reason_codes`.
Value is a finite number or null. Zero is an observation. Available metrics have
a non-null value and normally no reasons. Missing, pending and withheld metrics
have null values; these three states are distinct. Withheld means deliberately
suppressed because of ambiguity/conflict, not merely absent. Pending means scoring
is pending; missing does not imply that collection or scoring is in progress.

Duration observations and counts are integers; HRV and baseline means may be
fractional even when their unit is ms. Percent means percentage units, not a
0-to-1 fraction. Signed nap adjustments and actual-minus-need values stay signed.
The server does not add labels, precision, localization or generic directionality.
Formatting and rounding are client concerns; baseline logic is not.

Current metric reasons:

`ambiguous_primary_sleep`, `primary_sleep_unavailable`, `sleep_not_scored`,
`recovery_not_scored`, `sleep_component_unavailable`, `historical_value_unavailable`,
`nap_not_scored`, `nap_value_unavailable`, `conflicting_measurements`,
`measurement_unavailable`.

Internal `source_snapshot_conflict` becomes `conflicting_measurements` and
`source_value_unavailable` becomes `measurement_unavailable`. Reason codes are
lowercase snake_case product conditions, never IDs, values, paths or exception text.

## Baseline

Required fields: `window_days`, `window_start`, `window_end`, `anchor_date`,
`includes_anchor`, `observed_days`, `required_days`, `average`, `difference`,
`percentage_deviation`, `eligible`, `coverage_status`.

Date boundaries and anchor are nullable ISO dates. Prior windows cover
`[anchor_date - window_days, anchor_date)`; includes_anchor is always false.
Window end is the preceding date. Observed days count usable daily numeric
observations, not record counts, missing dates or pending observations.
Average, difference and percentage_deviation are nullable numbers.

Difference is current minus average in the original unit; for percentage metrics
it is percentage points. Percentage deviation is `100 * difference / average`,
or null when undefined (including a zero average). Both are computed by the
existing backend; mobile must not duplicate physiological comparison rules.

Required days and eligible are nullable. Physiology and Sleep Performance retain
the existing seven-observation threshold. Eligibility describes baseline coverage,
not sufficient overall readiness. Other sleep windows are descriptive and have
null required_days/eligible/difference/percentage_deviation. Partial averages may
remain numeric with insufficient coverage. Coverage is unavailable with no numeric
observations, full for all prior dates, insufficient below a defined threshold,
and otherwise partial.

## Today

TodayReport fields: `latest_available_morning`, `physiology`, `interpretation`,
`last_sleep`, `yesterday_training`, `quality_codes`.
The nested latest_available_morning equals the envelope value.

TodayPhysiology has exactly `recovery_score`, `hrv_ms`, `resting_heart_rate_bpm`,
`sleep_performance`. Each Physiology has `metric` and `baselines` (7, 14, 30).
Units are percent, ms, bpm, percent respectively; all four originate from WHOOP.

Interpretation has `overall_state`, `window_days: 14`, `signals`, `reason_codes`.
InterpretationSignals has the same four fixed keys. Each Signal has only `state`
and `reason_codes`. Existing domain prose remains for Dashboard/CLI, not mobile.
Reasons are added in the original classification branches without changing rules.

Individual reasons: `latest_value_unavailable`, `insufficient_baseline`,
`comparison_undefined`, `above_baseline`, `below_baseline`, `near_baseline`.
Above/below refers to numeric movement beyond the existing threshold, not its
desirability: lower resting HR can be below_baseline and positive.
Overall reasons: `conflicting_signals`, `insufficient_signal_coverage`,
`no_directional_signal`, `signals_unavailable`.

SleepSummary has `status`, `timing`, `actual_sleep`, `sleep_need`,
`sleep_performance`, `sleep_efficiency`; the four measurements are Metrics.
Status can be partial because a detailed sleep component is missing even when
all four summary values are present.

TrainingSummary has `availability`, `start_date`, `end_date`, `record_count`,
`observed_dates`, `duration_ms`, `calendar_coverage`, `activities`.
Yesterday means the day before report_date, not the phone's current yesterday.
Available means a valid anchor and a readable training dataset, not full calendar
coverage. A readable empty dataset yields zero **recorded** rows, null duration,
and empty activities. It never establishes zero real-world workouts or a rest day.
Missing dataset/anchor yields availability missing, null counts/duration and no
activities. Date boundaries remain available when there is an anchor.
Calendar coverage is always unknown. Nonempty ActivitySummary entries have
`activity`, `record_count`, `observed_dates`, `duration_ms`. Counts are source
records, not confirmed sessions. Activity labels are allowed; UUIDs and
classification provenance are not.

## Sleep

SleepReport has `report_date`, `status`, `timing`, `metrics`, `quality_codes`,
`naps`, `trends`. Its report_date equals the envelope value.
Timing contains nullable `local_start`, `local_end` (offset-aware date-times),
`recorded_offset` (Z or signed HH:MM), and integer `recorded_interval_ms`.
Preserve the recorded offset for bedtime/wake display; do not silently substitute
the phone's timezone. Timestamp elapsed interval and reported in-bed time are
different concepts and must not replace one another.

SleepMetrics is a fixed typed object, not a map of arbitrary domain fields:

| Group | Fields |
| --- | --- |
| Duration | in_bed_ms, actual_sleep_ms, no_data_ms |
| Need breakdown | baseline_need_ms, sleep_debt_need_ms, recent_strain_need_ms, nap_adjustment_ms, total_need_ms, actual_minus_need_ms |
| Stage totals | awake_ms, light_ms, deep_ms, rem_ms, restorative_sleep_ms |
| Stage percentages | light_pct_actual_sleep, deep_pct_actual_sleep, rem_pct_actual_sleep, restorative_pct_actual_sleep, awake_pct_in_bed |
| Scores | performance_pct, efficiency_pct, consistency_pct, respiratory_rate |
| Counts | sleep_cycle_count, disturbance_count |

All fields are Metrics. Actual sleep, restorative sleep, total need,
actual-minus-need and stage percentages are derived. Other measurements have
WHOOP origin. Stage percentages use actual sleep; awake percentage uses in-bed
time. Restorative overlaps deep/REM; it is not an additional exclusive stage.
No hypnogram, stage timeline or reconstructed accumulated sleep debt is implied.

Nap has only `timing` and `actual_sleep: Metric`. Include only naps whose recorded
local **end date equals report_date**, sorted by end instant descending. If there
is no report date, naps is empty. Older and newer dates are excluded. This is a
one-calendar-date display scope and not attribution to the main sleep's nap need
adjustment. There is no arbitrary truncation count and no persisted nap identity.

SleepTrends has the nine fixed metric keys from the enum table. Each value is a
TrendReport with window_days null to mean the embedded multi-window view, daily
points spanning up to 30 prior dates plus anchor, and 7/14/30 prior summaries.
When there is no anchor, its daily_points and prior_windows are empty.

## Curated quality

Quality arrays are sorted, deduplicated product codes, never direct domain dumps.
The following explicit mapping is the only public projection:

| Internal condition | Public code |
| --- | --- |
| missing_primary_sleep | primary_sleep_unavailable |
| source_snapshot_conflict | conflicting_measurements |
| missing_matching_daily_snapshot / daily_snapshot_unverified | measurements_unverified |
| missing_workout_dataset | training_data_unavailable |
| newer_incomplete_primary | newer_sleep_incomplete |
| incomplete_order_unknown | sleep_chronology_uncertain |
| duration_accounting_difference | sleep_duration_inconsistent |
| invalid_history_interval | sleep_history_incomplete |
| ambiguous_history_date | ambiguous_sleep_history |

Preserved notices: `ambiguous_primary_sleep`, `ambiguous_physiology`,
`recent_offset_transition`, `mixed_offset_date`, `baseline_offset_transition`,
`sleep_not_scored`, `missing_sleep_components`, `unrecorded_duration_present`.
Today includes applicable curated sleep notices as well as report notices.
Baseline implementation flags, missing_or_unscored duplicates, unfinished cycle,
source freshness limitations, unknown workout coverage duplicates, raw association
diagnostics, identity exclusions and classification-sidecar diagnostics stay internal.
Unknown future domain flags are not automatically published.

## Trends

`GET /api/v1/trends?metric=actual_sleep&anchor_date=2026-09-10&window_days=7`

Metric is required. Window is optional, default 14; query enum values are strings
`7`, `14`, `30`, while response windows are integers. Anchor is optional; omit it
to use the latest selectable morning. Supplied anchor must be a real canonical
YYYY-MM-DD date. Datetimes, Unix timestamps, literal null, compact dates and empty
strings are invalid. Duplicate recognized parameters and unknown parameters return
422. No historical neighbor is substituted when an anchor is unavailable.

TrendReport contains `metric`, `anchor_date`, `window_days`, `daily_points`,
`prior_windows`. Standalone requests return their selected integer window and
one prior summary when an anchor exists. Each TrendPoint has `report_date`,
`recorded_offsets: string[]`, and `metric: Metric`.

Displayed points cover D-window_days through D inclusive, so seven prior days
may have eight displayed dates. Summaries exclude D. Missing dates remain absent;
an observed unavailable date carries null and its availability/reasons. No
zero-filling, imputation or clock-time averaging occurs. Points are date-ordered.
Offsets may contain multiple recorded offsets for aggregated observations; they
are not geographic zones or evidence of travel. Sleep Performance reuses the
existing filtered daily observations and computed windows without recalculation.

## Error

Required keys: `code`, `message`, `retryable`, `request_id`.
The body request_id matches X-Request-ID. Messages are fixed, sanitized fallback
text; client behavior uses code/retryable, not exception details or parsed prose.

| HTTP | Code | Retryable |
| --- | --- | --- |
| 403 | local_access_only | false |
| 404 | not_found | false |
| 405 | read_only | false |
| 422 | invalid_query | false |
| 500 | internal_error | false |
| 503 | snapshot_unavailable | true |

Unexpected programming/application exceptions produce 500. Known snapshot read,
validation, contention and interrupted publication failures produce 503. Retryable
means a later read may succeed after backend state changes, not that corruption
or an interrupted transaction will repair itself. Use bounded retries; the API
never performs recovery or sync. A missing required input/coordination lock is 503;
readable empty reports and missing measurements are 200 with nulls/status.
All six error response models are explicitly represented in OpenAPI, replacing
the default validation-error schema. Unknown GET routes are 404; non-GET is 405
after local-access checks. No new exception middleware layer is introduced.

## Privacy, cache and integration boundaries

Default responses omit raw IDs, provenance, paths, CSV schemas, credentials,
exception details, Git state and internal flags. The repository reads only the
existing local product inputs and never creates its read lock or writes data.
It does not load credentials or invoke WHOOP. Host, Origin and client-loopback
restrictions remain; physical-phone connectivity requires a separate approved
design. Do not expose this unauthenticated API remotely.

Responses retain Cache-Control: no-store. No ETag, HTTP 304, mobile persistence,
auth/account endpoints, collection-time tracking or deployment is included.
A future app-managed cache must distinguish endpoint/query identity, schema and
analysis versions, snapshot ID, generated time, report date and phone cached_at.
Cached freshness must not be assumed current merely because input hashes match.

## Synthetic examples and verification

[Complete synthetic Today, Sleep and Trend JSON](../tests/fixtures/api_v1_examples.json)
is checked against actual service serialization. It contains deliberately small
synthetic millisecond values and a zero-filled placeholder snapshot fingerprint;
the placeholder is not a real dataset fingerprint. No private values are used.
Contract tests additionally cover missing/pending/withheld values, gaps, ancient
naps, empty versus unavailable training, errors, strict queries and privacy.
The golden examples are complete payloads, not abbreviated schema projections.
