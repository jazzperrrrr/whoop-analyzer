# WHOOP Analyzer

A beginner-friendly Python project for recording WHOOP measurements and connecting
your WHOOP account.

## Manual CSV entry

Run `python main.py` and enter the five measurements when prompted. Each run appends
today's measurements to `whoop_data.csv`. This works without OAuth or extra packages.

## Connect your WHOOP account

1. Install the authentication dependencies using your Python environment:
   `python -m pip install -r requirements.txt`.
2. In the [WHOOP Developer Dashboard](https://developer.whoop.com/), register this
   exact redirect URI for your app: `http://localhost:8080/callback`.
3. Fill in the project's existing `.env` using the three variable names in
   `.env.example`. If `.env` does not exist, copy `.env.example` to `.env` first.
   Enter your own client ID and client secret, and set
   `WHOOP_REDIRECT_URI=http://localhost:8080/callback`. Never share the secret.
4. Run `python whoop_auth.py`. Your browser opens WHOOP's sign-in and consent page.
5. Complete authorization within three minutes, then check the terminal for success.

The temporary server listens only on your computer at port 8080 and closes after
the callback, timeout, or cancellation. If the port is busy, close the app using it
and retry. A failed or cancelled attempt does not overwrite existing tokens.

The requested permissions are `read:recovery`, `read:cycles`, `read:sleep`,
`read:workout`, and `offline`. WHOOP requires `offline` to issue a refresh token. See the official
[scope reference](https://developer.whoop.com/api/) and
[OAuth documentation](https://developer.whoop.com/docs/developing/oauth/).

Tokens and expiry information are saved in `whoop_tokens.json` beside the scripts.
This is a local, unencrypted credential file: keep it private. `.env`, the token
file, temporary token files, and your measurement CSV are ignored by Git.

## Fetch your latest WHOOP measurements

Run `python whoop_fetch.py` after connecting your account. It prints recovery score,
HRV (ms), resting heart rate (bpm), day strain, and sleep performance percentage.
The command reads the existing token file and refreshes tokens near expiry or once
after a 401 response. Rotated access and refresh tokens are saved atomically.
Run one fetch/authentication process at a time because WHOOP rotates refresh tokens.

Using the current [official v2 API](https://developer.whoop.com/api/), it requests
`GET /developer/v2/cycle?limit=1`, then `/cycle/{cycleId}/recovery` and
`/cycle/{cycleId}/sleep` under the same v2 base. This uses only the already requested
permissions. It selects the newest physiological cycle, labels it by its start
date in WHOOP's recorded timezone, and matches recovery and sleep by their IDs.
It does not assume that today's calendar date has processed data. Pending or
missing scores display as `N/A`; an ongoing cycle's strain can still increase.
It never substitutes an older day's score for a missing measurement.

API responses stay in memory and only the summary is printed. The command does not
create or modify `whoop_data.csv`. If you manually save raw API responses, put them
in the Git-ignored `whoop_responses/` directory. Never add credentials or personal
responses as test fixtures. Errors omit response bodies and credentials.
If refresh is rejected, reconnect with `python whoop_auth.py`. Network or rate-limit
errors can be retried later. No additional dependencies are needed.

## Collect historical WHOOP measurements

Run `python whoop_history.py` for today and the previous 29 reporting dates.
For the fixed investigation period, run:

```sh
python whoop_history.py --end-date 2026-09-19 --days 30
```

The minimum window is 30 days. Run only one authentication/fetch/history process
at a time. The existing client handles authentication and token rotation; no new
permissions or dependencies are required. All API operations on health data are
GET requests. Neither `whoop_history.csv` nor `whoop_data.csv` is written or migrated.

The collector writes four Git-ignored personal files under `data/`:

| File | Identity | Contents |
| --- | --- | --- |
| `cycles.csv` | `cycle_id` (WHOOP `cycle.id`) | Original start/end, recorded offset, timestamps, score state, strain |
| `sleeps.csv` | `sleep_id` (WHOOP `sleep.id`) | Cycle relationship, original start/end, own offset, primary/nap flag, score state, performance |
| `recoveries.csv` | `cycle_id` | Associated `sleep_id`, creation/update times, score state, recovery/HRV/RHR |
| `daily_metrics.csv` | `(cycle_id, sleep_id)` | Derived report date, joined measurements, timestamps, separate sleep/cycle offsets and availability flags |

These are normalized, allowlisted entities, not full raw API responses. Recovery
has no independent start/end or timezone in the API; its referenced sleep supplies
the reporting timestamp. The files contain private health data and remain local.
No credentials or raw responses are logged or exported.

A physiological cycle is not a calendar day. WHOOP documents that a primary sleep
starts a cycle, and recovery describes readiness after waking:
[cycles](https://developer.whoop.com/docs/developing/user-data/cycle/),
[sleep](https://developer.whoop.com/docs/developing/user-data/sleep/),
[recovery](https://developer.whoop.com/docs/developing/user-data/recovery/).

The daily report uses the **primary sleep's end date in that sleep's recorded
UTC offset**. Joins use IDs, never dates. Cycle strain stays attached to its
original cycle. Multiple cycles may produce rows with the same report date;
no cycle is discarded and strain is not summed or apportioned across days.
The cycle and sleep offsets can differ during travel. Neither is replaced with
the computer's current timezone. This rule is explicit reporting policy, not a
guarantee of identical date labels in every WHOOP mobile-app travel scenario.

The collector paginates cycles, sleeps, and recoveries using `next_token` as the
next request's `nextToken`. UTC query bounds extend one day before the first
report date and through midnight two days after the last. ID lookups complete
relationships absent from collection results, including boundary records. The
reporting window is applied only after computing the local sleep-end date.
Boundary entities are retained even if they do not produce rows in the report.

Missing, unscored, or invalid measurements are blank, never invented or zero-filled.
A primary sleep without recovery still produces a row with blank recovery metrics.
Cycles and recoveries without primary sleep remain in entity storage, without an
invented report date. Naps remain separate sleep events and never generate a daily
recovery. Days with only naps or an ongoing multi-day cycle may have no daily row.

Entity files are cumulative archives: repeat fetches upsert by ID, including
blanked measurements and changed recovery/sleep relationships. Records not returned
remain archived; this is not a deletion-synchronization service. The daily report
is rebuilt from **current collection identities**, resolved against archived update
times, for the requested window. The archive and daily snapshot use the same
selected versions. Absent recoveries are not filled from an older archive. Changing the window replaces
that derived report; it does not erase the entity archive. Identical input gives
identical files and no duplicate entity or relationship rows.

All downloads and validation finish before saving. Existing malformed entity files
are rejected. All four files are staged before replacement; each file replacement
is atomic, but the four CSVs are not a single database transaction. If a disk write
is interrupted, rerun collection before reading the dataset. API/authentication
failures leave existing outputs untouched. The command prints counts only.

The old date-keyed history cannot recover discarded identities. Keep it for
comparison and rebuild corrected data from the API; do not relabel its rows as a
migration. The latest-cycle display command remains a cycle-start-labelled view,
separate from the historical report.

## Extended sleep data layer (Phase 5A)

Sleep entities now preserve nullable stage durations, sleep-need components,
efficiency, consistency, respiratory rate, sleep-cycle count and disturbance count.
Source `*_milli` durations become integer `*_ms` fields; percentages remain on
their original percentage scale and respiratory rate is in breaths per minute.
The original `sleep_performance` mapping and daily metrics schema are unchanged.
Naps retain their own IDs and details and remain excluded from primary mornings.

`read_entities(..., "sleeps")` accepts exactly two ordered headers: the original
ten-column header and the extended header. Legacy rows gain null detail fields
in memory without a write. Unknown, partial, reordered or malformed schemas are
rejected. Missing measurements remain null; genuine zeros remain zero. New
durations/counts must be integers, measurements must be finite, and only the
recent-nap need contribution may be negative. Non-scored responses have no
extended measurements; extended CSVs with stale non-scored measurements fail
validation. Existing Sleep Performance validation behavior is retained.

On an explicitly requested future collection, persistence writes the extended
header, retaining unfetched legacy rows with blank details and upserting fetched
rows by sleep ID. Known update times beat missing times, and newer times beat older
times during both collection deduplication and persistence. Collection groups all
normalized candidates by ID before resolving them, including paginated and related
responses. Only the highest-precedence timestamp group participates in resolution.
A real candidate must contain every supplied value from that group (zero counts
as supplied); conflicts or the absence of a covering candidate fail deterministically.
Partial responses are never merged into a manufactured entity. Equal-time
archive refreshes still accept the incoming version. A newer response replaces measurements, including nulls, rather
than borrowing stale details. Creation/update timestamps remain source values.
Reading this version does not backfill history or authenticate; historical
details require a separately authorized future fetch. No personal data was
migrated as part of Phase 5A. Daily rows are derived from resolved incoming IDs only;
unrelated archive records stay excluded. An inconsistent resolved sleep/recovery
relationship fails before any files are staged. Per-file atomic replacement is
unchanged; the four CSVs remain separate files, not a multi-file transaction.

`whoop_sleep.py` contains pure helpers for actual sleep (light + SWS + REM),
restorative sleep (SWS + REM), and total sleep need (all four signed components).
Each sum requires every operand. The accounting residual subtracts awake, light,
SWS, REM and no-data time from in-bed time without correcting any source value.
Efficiency diagnostics expose candidate percentages using in-bed time and
in-bed minus no-data time, plus official-minus-derived differences in percentage
points. Missing operands or nonpositive denominators yield null. Official WHOOP
efficiency remains authoritative; these ratios do not claim to reproduce its
algorithm. Recorded end-minus-start is not substituted for scored in-bed time.

A later read-only `SleepDetails` representation should have these sections:

| Section | Contents |
| --- | --- |
| `identity` | Sleep/cycle IDs, nap flag, sleep-end report date |
| `source` | Original timing and offset, six durations, four need components, official performance/efficiency/consistency, respiratory rate and counts |
| `derived` | Local timing, recorded interval, actual/restorative sleep and total need; any later stage percentages with explicit denominators |
| `quality` | Score state, missing component names, accounting residual, efficiency diagnostics, provenance and schema/backfill status |

Keep source and derived values distinct. Record `legacy_schema` only when the
input header establishes it. After legacy rows are written under the extended
header, all-null details alone cannot distinguish never-backfilled rows from
source-missing details; backfill status must remain unknown unless explicit
provenance is added later. No Sleep Details page, stage percentages, new readiness
rules or Daily Report presentation changes are included in this phase.

## Collect workout / activity history

Run `python whoop_workouts.py --days 30` to collect workouts starting within the
trailing 30 x 24 hours, ending at the current UTC instant. A fixed inclusive UTC
calendar-date range is also supported:

```sh
python whoop_workouts.py --start-date 2026-08-21 --end-date 2026-09-20
python whoop_workouts.py --end-date 2026-09-20 --days 30
```

`--start-date` requires `--end-date` and cannot be combined with `--days`.
Days must be positive. Explicit dates describe UTC bounds, not local report dates;
an inclusive end date becomes midnight of the following day, exclusive.
The collector paginates `GET /developer/v2/activity/workout`, filters by UTC start
time, and writes only `data/workouts.csv`. The existing OAuth client is reused;
enable `read:workout` in the Developer App and reauthorize with `python whoop_auth.py`
if your connection lacks it. Normal authenticated runs may rotate the local token
file. Run one authentication/collection process at a time.

The Git-ignored CSV is a cumulative entity archive with one row per workout UUID:

| Columns | Meaning |
| --- | --- |
| `workout_id`, `sport_name`, `sport_id` | UUID identity and original WHOOP activity fields |
| `start`, `end`, `timezone_offset` | UTC ISO timestamps and the workout's original recorded offset |
| `local_start`, `local_end`, `report_date` | Offset-aware local timestamps; report date is the local **start** date |
| `duration_seconds`, `created_at`, `updated_at`, `score_state` | Elapsed duration, UTC source metadata, scoring status |
| `strain`, `average_heart_rate`, `max_heart_rate` | Workout strain and heart rates in bpm |
| `kilojoule`, `kcal`, `percent_recorded` | Original energy, kcal = kilojoule / 4.184, raw recording coverage |
| `distance_meter`, `altitude_gain_meter`, `altitude_change_meter` | Nullable distance/elevation; altitude change may be negative |
| `zone_zero_milli` through `zone_five_milli` | Six HR-zone durations in milliseconds |

Local timestamps always use the individual workout's recorded offset, never the
computer's current timezone. A workout crossing midnight retains both local dates
and one start-date reporting label. Offsets do not identify geographic locations
or establish timezone transitions within a workout. Activity names are preserved:
`activity` is not inferred to mean strength training. Future custom categories
belong in a separate derived layer; no cycle relationship is guessed from dates.

Missing values serialize as blank, while real zero values remain zero. Pending or
unscorable workouts retain metadata with blank score metrics. Invalid nonfinite
or malformed numeric values fail safely. Recording coverage is stored unchanged,
without assuming a percent-versus-fraction scale. Zone totals are not forced to
equal elapsed duration. No sets, repetitions, load, or HR samples are invented.

Reruns upsert by UUID and retain newer `updated_at` versions, including across
pages and existing archives. Records outside the requested window remain archived;
missing API records are not treated as deletions. Identical input produces identical
CSV bytes. The entire collection is validated before saving; malformed existing
CSVs are rejected, and the single CSV is replaced atomically. Other entity and
legacy CSVs are untouched. Raw API responses remain in memory, never on disk.
Console output contains the query window, counts, and original activity names.

## Analyze workouts (read only)

Run `python whoop_workout_analysis.py` for observed 7-, 14-, and 30-calendar-day
summaries ending on the latest local workout date. Use `--as-of YYYY-MM-DD` to
choose another anchor. `--workouts`, `--daily-metrics`, and `--classifications`
accept alternative input paths; defaults are relative to the project `data/`.
This command makes no API calls and writes no files. It prints aggregate results,
never individual workout UUIDs or raw physiological records.

The pipeline is normalized workouts -> canonical classification -> individual
record analysis -> daily aggregation -> rolling summaries -> exact D+1 pairing.
It does not change the physiological readiness rules or predict recovery.

Personal classifications live separately in Git-ignored
`data/workout_classifications.csv`, with columns `workout_id`, `recorded_activity`,
`canonical_activity`, `provenance`, and `classification_version`. Corrections
apply only to the listed UUID and matching original label. Provenance is
`original_label`, `user_confirmed`, `user_confirmed_category`, or `unresolved`.
User-confirmed corrections and category assignments apply only to reviewed UUIDs.
No personal UUIDs or classification records belong in source code or tests.
Future unreviewed Pickleball/Squash retain their original sport identities; an
absent sidecar applies no corrections and prints a notice. Original labels are
always retained. Removing a correction reverses that classification without
changing `workouts.csv`. Unspecified Activity defaults to Unknown, included in
overall totals but excluded from named-sport attribution.

Every UUID stays separate with an unset training-session ID and unreviewed
grouping status. Record counts do not claim to count real-world sessions. Reports
also expose distinct training dates and gaps between those dates. Daily summaries
use local start dates from each workout's own offset, retain UUID provenance, and
flag mixed offsets, midnight crossings, mixed activities, and UTC overlaps.
Durations are sums of recorded durations, not union time when records overlap.

Windows include the anchor and preceding N-1 dates. Calendar coverage remains
unknown; empty dates are never manufactured as rest days and the final day may
be partial. Numeric summaries include available-value denominators. All-missing
measurements remain null; genuine zeros stay zero. Strain mean/median/max describe
record scores. The structured result also exposes `arithmetic_session_strain_sum`,
which is explicitly not Day Strain or an additive physiological load.
Zone percentages use only records containing all six zones, divided by their
summed zone time; zone totals separately retain each metric's available values.
Average HR is duration-weighted and approximate. Cardiovascular metrics do not
quantify muscular workload. Energy retains kJ and derives kcal by dividing by 4.184.

Pairing aggregates the entire workout day D before looking for report date D+1.
Missing D+1 remains missing: D+2 is never substituted. Multiple cycle/sleep outcomes
are retained as ambiguous candidates, not independent observations. Each outcome
retains cycle/sleep provenance, missing metrics, offset and chronology flags, and
an in-progress-cycle flag. Pending/unscorable or absent recovery values are not
used as scored outcomes. A scored morning recovery can coexist with an unfinished
cycle; current cycle strain is not used in this pairing. No outcome is attributed
to a sport on a mixed-activity day. All relationships are descriptive, not causal.

## Analyze personal baselines

Run `python whoop_analysis.py` to print an offline report using
`data/daily_metrics.csv`. An alternative input can be supplied with
`--input path/to/daily_metrics.csv`. The default path is relative to the project,
not the terminal's working directory. Analysis never writes files, reads credentials,
or calls an external API. It uses only the Python standard library.

For HRV, resting heart rate, recovery, sleep performance, and cycle strain, the
module calculates rolling 7-, 14-, and 30-calendar-day averages. For a report date
D, each window includes D minus N days through D minus one day. The report date
itself and later dates are excluded. Existing `report_date` labels are used as-is;
timestamps are not converted again using the computer's timezone.

The concise terminal report interprets every cycle/sleep row on the latest available
date using the 14-day baseline. Add `--details` to include all numeric comparisons
for the 7-, 14-, and 30-day windows. Distinct relationships are preserved. Within a historical
date, available measurements are averaged per metric before averaging across
dates, giving each date equal weight. Repeated strain/recovery measurements for
the same cycle on the same date count once; sleep performance is keyed by sleep
ID. These analytical means are not new WHOOP entities or summed strain totals.
Conflicting values for the same entity/date are rejected.

Missing values and dates are not filled, zeroed, or replaced with older values.
Each metric shows its own available-day count; a baseline with fewer than N
observed days is explicitly marked partial. Even one prior observation can form
a partial baseline, while zero observations produces N/A. The 30-day history
export normally supplies at most 29 previous days for its latest date; collect a
longer window if a full 30-day baseline is needed.

Differences use the metric's units (percentage points for recovery and sleep
performance). Relative deviation is `(current - baseline) / baseline * 100`;
it is N/A when either value is missing or the baseline is zero. An unfinished
cycle's strain is labelled provisional/current load and never contributes to the
overall state or morning readiness.

Interpretation uses explicit descriptive project rules, not clinical cutoffs.
`SIGNAL_RULES`, `INTERPRETATION_WINDOW`, and `MIN_BASELINE_DAYS` in
`whoop_analysis.py` centralize the policy. Each signal needs its latest value and
at least **7 observed days within the previous 14 calendar days**. HRV also needs
a nonzero baseline for its relative comparison. Missing or insufficient signals
remain unclassified; partial numeric baselines are still available with `--details`.

| Signal | Positive relative to baseline | Negative relative to baseline |
| --- | --- | --- |
| HRV (physiological) | Increase of at least 10% | Decrease of at least 10% |
| Resting heart rate (physiological) | Decrease of at least 3 bpm | Increase of at least 3 bpm |
| Sleep performance (sleep context) | Increase of at least 5 percentage points | Decrease of at least 5 percentage points |
| WHOOP Recovery (separate signal) | Increase of at least 10 percentage points | Decrease of at least 10 percentage points |

Changes inside these inclusive boundaries are neutral. These labels describe
direction relative to personal baseline, not absolute health or sleep adequacy.
Overall state requires both physiological signals and at least one context signal:

- **Strong positive/negative:** all four signals agree in that direction.
- **Generally positive/negative:** at least one signal points in that direction,
  with the others neutral or unavailable and none pointing the opposite way.
- **Mixed:** positive and negative signals coexist, with their names and directions
  explained; or all classified signals are neutral, explicitly reported as no clear direction.
- **Insufficient data:** the overall coverage requirement is unmet. Any available
  conflicting signals are still explained independently.

The report separates physiology, sleep, WHOOP Recovery, and current load. It makes
no diagnoses, causal claims, or recommendations and uses no external services.

Malformed CSVs, invalid/nonfinite/negative numbers, duplicate relationship rows,
and invalid dates/IDs fail with safe errors instead of silently biasing averages.
Additional metadata columns from the history model are accepted.

## Run checks

After installing the dependencies, run `python -m unittest discover -s tests -v`.
The checks use simulated WHOOP responses and never use your real credentials.

## Local web dashboard

Double-click `start_dashboard.cmd`, or run from the repository:

```powershell
.\.venv\Scripts\python.exe -B whoop_dashboard.py
```

Open http://127.0.0.1:8501. The server binds only to this computer's loopback
interface; Ctrl+C stops it. Use `--port 8502` if the port is already occupied.
The launcher uses the repository's own virtual environment and passes through
command-line arguments.

The dashboard reads this repository's `data/` by default, independently of the
terminal's current directory. `--data-dir <directory>` can select another local
snapshot (manual sync is disabled for alternative directories). Personal CSVs
remain Git-ignored. **Refresh local data** only rereads local files: GET requests
never call WHOOP or refresh tokens. No personal reports are saved.

CLI and dashboard share `load_daily_report()` and the existing selection,
consistency, baseline and readiness logic. The page shows morning metrics,
physiological state, Last Sleep, recent training, and expandable data-quality
details. It uses local CSS with no remote assets or analytics.
Run the synthetic dashboard tests with
`python -B -m unittest discover -s tests -p "test_whoop_dashboard.py" -v`.

### Sleep product and freshness (Phase 5C)

Home shows actual sleep, estimated Sleep Need, official Sleep Performance and
Efficiency, with bedtime and wake time in the recorded offset. `/sleep` adds
aggregate stages, the signed need breakdown, quality metrics, separate naps, and
descriptive history. These are stage totals, not a hypnogram. Light/Deep/REM and
restorative percentages use actual sleep; Awake uses WHOOP in-bed time. Actual
sleep, restorative sleep and total need are locally derived; official scores stay
distinct. Actual minus estimated need describes that night's difference, not a
reconstruction of WHOOP's accumulated sleep debt.

`whoop_sleep_product.py` consumes the existing report selection and consistency
checks. Primary-only trends show daily observations and prior 7/14/30-calendar-day
means with counts; D is excluded, absent dates stay absent, and every observed date
has equal weight. Sleep Performance reuses the Daily Report's already filtered
history, same-date aggregation and computed windows exactly. Other sleep-detail
metrics exclude ambiguous dates. Recorded offsets remain with observations.
No clock-time averages, predictions, medical alerts or readiness
changes are introduced. Arithmetic is shared with the existing baseline engine.

The latest available morning and its age relative to the computer's local date
are displayed. An older date says "Local data may be out of date"; collection
time is unknown, so this does not establish a missing sleep or failed recovery.

### Explicit manual WHOOP sync

**Sync WHOOP** submits `POST /sync`; it is the only dashboard path that creates
an authenticated client. Loopback, exact Host, same Origin and a per-process form
token are required. `Referrer-Policy: same-origin` permits the legitimate browser
form's same-origin Origin; null and hostile origins remain rejected. The form
token is consumed before sync and rotated: an old form cannot be replayed and
other open tabs must reload it. Completed attempts use POST -> 303 redirect -> GET,
so refreshing the result page never repeats collection. Result pages contain only
fixed, sanitized status messages (their URL is not proof of a completed sync).
The existing scopes and token-refresh/save flow are reused; credentials and raw
responses are never rendered or persisted as API exports.

The sync overlaps **three reporting dates including today** to capture recent
revisions. Existing collection semantics pad the UTC query from midnight one day
before the first date to midnight two days after the last, with ID lookups for
missing relationships. Workouts use that same bounded UTC window. This is not a
full historical sync; older revisions require a separately requested collection.

`whoop_sync.py` stages normalized archives using the existing collectors,
deterministic entity resolution and persistence. The daily snapshot retains its
existing identities plus recent incoming identities, all using effective archive
versions; unrelated historical archive rows are not introduced. Incoming sleeps
do not borrow absent recoveries from an older archive. Classifications are read
for validation and never published or changed.

Only the five normalized datasets are published, with per-file atomic replacement,
rollback copies and a durable recovery marker in Git-ignored `data/.sync-pending/`.
Manifests are written to a temporary file, flushed, fsynced, read back and validated,
then atomically replaced. States are `prepared`, `publishing`, `rollback_required`,
`rollback_complete`, and `committed`. Recovery verifies every backup first and
every restored file against its original SHA-256 (including originally absent
files). Only then is `rollback_complete` persisted. Terminal markers survive
backup cleanup until the last step: a crash during cleanup resumes cleanup only,
never restoration from deleted backups. Verification failures preserve artifacts.
Dashboard readers are locked out during publication and pause if an interrupted
transaction remains; the next explicit sync first restores that transaction.
An OS lock prevents concurrent dashboard syncs. Run no other collector or auth
process concurrently: separate CSV files are not a database transaction. Token
rotation, if required, remains managed independently by the existing auth flow.
Tests use synthetic records, mocked collectors and temporary archives only.
