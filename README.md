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
is rebuilt from the **current collection** for the requested window, so absent
recoveries are not filled from an older archive. Changing the window replaces
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
