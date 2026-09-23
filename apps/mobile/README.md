# Mobile V1 · Today → Sleep

An Expo Router / React Native / TypeScript app with **demo as the default** and an
explicit **live-local** mode for Expo Web on this computer. Both use the frozen
[API V1 contract](../../docs/API_V1_CONTRACT.md). Live-local reads the existing
local snapshot through FastAPI; it never calls WHOOP, refreshes tokens or syncs.
There is no authentication or persistent mobile health-data storage.

## Install and run

Use Node 22.13+ (Node 24 LTS is suitable). From this directory, after approving
the dependency proposal:

```sh
npm install
npm run typecheck
npm test
npm run web
```

Once a lockfile exists, use `npm ci` for reproducible installs. `npm start` starts
Expo on localhost; `npm run android` targets a local Android emulator with the
required Android development tools. iOS simulator development requires macOS.
The web preview provides a local way to review the UI on Windows. Demo mode needs
no Python server. Start scripts do not enable a LAN or tunnel connection.

## Structure

- `app/`: root stack, Today and Sleep routes only.
- `src/api/`: injectable clients, mode selection, runtime validation and safe errors.
- `src/features/`: feature-specific screen presentation and aggregate trend bars.
- `src/components/`: reusable typography, metrics, availability and page states.
- `src/design/`: restrained light palette, type/spacing/radius/motion tokens,
  formatting, central reason/quality/error copy, reduced-motion preference.
- `src/storage/`: in-memory coherent snapshot pair and cache interface.
- `src/types/`: static types derived from the frozen in-memory OpenAPI components.
- `tests/`: unit, component and real Router navigation tests. Network is forbidden.

The public wire field names remain snake_case. `HttpApiClient` validates all required
envelope/nested V1 fields before returning them. Screens do not fetch or inspect
CSVs and have no client-specific presentation. The loader publishes a pair only
when `(schema_version, analysis_version, snapshot_id)` agrees. `cached_at` is phone
cache time and is distinct from `generated_at`, `report_date` and unknown collection
time. The cache is volatile and disappears when the provider/app is destroyed.
A pair reads Today then Sleep sequentially to avoid self-contention on the existing
Windows snapshot read lock. A mismatched pair triggers exactly one fresh two-endpoint acquisition. A repeated
mismatch fails safely without changing the cache. A previously coherent pair may
remain visible with an explicit failure notice and retry; with no cache, a safe
unavailable state appears. No failure substitutes demo values for local data.

## Contract and synthetic data checks

Demo mode selects `MockApiClient('visual-demo')`, serving only the separate, static
`src/api/fixtures/visual_demo.json`. Every value is completely fictional, with no
personal/device source or network access. Screens display **SAMPLE DATA · DEMO**.
The fixture includes coherent aggregate stages and signed sleep need, varied
history, absent dates, and one pending observation. Values and prior summaries
are authored fixture outputs; the UI only formats them.

`MockApiClient()` without arguments and the contract drift check use the unchanged frozen
`src/api/fixtures/api_v1_examples.json` copied from the root golden fixture.

From the repository root, using the existing Python environment:

```sh
python -B apps/mobile/scripts/contract.py
```

This checks static TypeScript types against all named frozen OpenAPI components,
including enums, required keys and nullable values, and compares the copied fixture
with `tests/fixtures/api_v1_examples.json`. It validates synthetic responses with
the existing models. It creates no server and does not read local health data.
`--write` explicitly regenerates the two mobile artifacts when approved. No Node
OpenAPI generator is installed. Type checking plus the drift check are both needed.

The approved fixture deliberately contains very small sleep durations. Positive
values below one minute display `<1 min`; they must not be mistaken for missing
data or replaced with realistic personal values. Zero is shown as zero. Pending,
missing and withheld measurements have distinct copy; unknown codes get safe
generic text. All mapped English wording lives in `src/design/copy.ts`.

## Presentation rules

Today shows four physiological metrics, the backend's mapped interpretation,
and a tappable Last Sleep summary. Sleep shows its hero scores/timing, aggregate
stage composition, signed need breakdown, quality measures and a representative
actual-sleep trend with the backend's 7/14/30 summaries. Notes start collapsed.
Light/deep/REM form the aggregate composition; restorative overlaps deep/REM and
is shown separately. Awake uses the backend's in-bed percentage denominator.
No hypnogram, physiological threshold or baseline calculation is reconstructed.

Trend rows show observed dates explicitly; dates absent from the report are not
invented, connected or zero-filled. Pending/missing/withheld dates have no bar.
Bar widths are visual scaling only. Timestamps retain recorded wall-clock times
and offsets rather than silently changing to the device timezone. The optional
native stack animation is disabled for reduced motion; press feedback is opacity.

Typography roles: hero, metric, title, section, body, caption. Spacing is
4/8/12/16/24/32/48/64; radii are 12/20/28. Motion tokens include quick (120ms),
standard (220ms), and hero spring parameters. System fonts and semantic neutral
colors keep future dark-mode work separate from screen code.

## Phase 6C visual polish V0

Review at **440 × 956** first, then **402 × 874**. Web caps the complete route and
header at 440px and centers it without a phone bezel. Native uses available width,
with the navigator owning the top inset/back button and the page owning left,
right and bottom safe areas. Scroll content retains bottom padding. Native device
safe-area and Dynamic Type checks still require a device/simulator.

Metrics use intrinsic height; only horizontal wrappers own column sizing. No
normal-flow section has an absolute position or fixed height. Today uses a 2×2
physiology grid, concise mapped interpretation and one Last Sleep link. Sleep
keeps three hero scores in one flexible row, then stages, signed need, compact
quality and actual-sleep history. Restorative is separate from the three exclusive
stage segments. Missing dates are marked as gaps, and pending values have no bar.
Press feedback uses opacity; the existing route transition respects reduced motion.

Start the localhost-only preview with `EXPO_NO_TELEMETRY=1` and `npm run web`.
Verify Today → Sleep in the browser and inspect the runtime console. SDK checks
use `npx --no-install expo install --check` (read-only; never `--fix` here).

## Deferred

Physical phone/backend connectivity needs a separately approved security design;
the frozen Python API remains loopback-only. No backend exposure, auth/accounts,
cloud deployment, persistent storage, HTTP cache, live sync, Training/Nutrition
screens, or advanced chart interactions are included. Native device visual and
accessibility validation remains necessary before calling this a phone release.

## Phase 6D: local real-data Web development

Configuration is public and contains no secrets:

| Variable | Values / purpose |
| --- | --- |
| `EXPO_PUBLIC_DATA_MODE` | `demo` (default when unset) or `live-local`; any other value fails safely |
| `EXPO_PUBLIC_API_BASE_URL` | Required in live-local; the verified local API origin is `http://localhost:8000` |
| `EXPO_NO_DOTENV` | Set to `1` to disable Expo's automatic `.env` loading |
| `EXPO_NO_TELEMETRY` | Set to `1` for local preview sessions |
| `WHOOP_API_EXPO_WEB` | API process only: `1` opts into the exact Expo Web origin allowance |

Use process environment variables, not `.env` files. Never put secrets in
`EXPO_PUBLIC_*`. Restart Expo when changing modes; the provider/cache are scoped
to one mode for their lifetime. Live-local is rejected on iOS/Android for now.

For demo, from `apps/mobile/` in PowerShell:

```powershell
$env:EXPO_NO_DOTENV='1'
$env:EXPO_NO_TELEMETRY='1'
$env:EXPO_PUBLIC_DATA_MODE='demo'
npm run web
```

The verified browser setup uses `http://localhost:8081` for Expo Web, listening
on `[::1]:8081`, and `http://localhost:8000` for FastAPI, listening on `[::1]:8000`.
Both services must remain **loopback-only**. IPv6 loopback `::1` is the currently
verified setup. IPv4 `127.0.0.1` is also loopback, but both services should use a
consistent hostname/address-family configuration so browser resolution matches
their listeners.

For the real local snapshot, start in this order:

1. From the repository root, start the existing API with its opt-in Web transport:

   ```powershell
   $env:WHOOP_API_EXPO_WEB='1'
   .\.venv\Scripts\python.exe -B -m uvicorn api.app:app --host ::1 --port 8000 --no-access-log --no-proxy-headers
   ```

2. In a separate terminal, from `apps/mobile/`, stop any prior Expo instance and run:

   ```powershell
   $env:EXPO_NO_DOTENV='1'
   $env:EXPO_NO_TELEMETRY='1'
   $env:EXPO_PUBLIC_DATA_MODE='live-local'
   $env:EXPO_PUBLIC_API_BASE_URL='http://localhost:8000'
   npm run web
   ```

3. Open `http://localhost:8081`. Confirm **LOCAL DATA**, the API-supplied report date,
   and Today → Sleep navigation. **SAMPLE DATA · DEMO** appears only in demo mode.
   The local snapshot can be older than today; its date is never forced or replaced.

`HttpApiClient` issues only GET health/Today/latest-Sleep requests, with an 8-second
timeout (including body reads), redirects blocked, browser credentials omitted,
and HTTP caching disabled. Invalid URLs/configuration, network failures, schema
failures and frozen Error envelopes map to safe central copy. Raw response messages,
request IDs, exceptions and paths are never displayed or logged by the client.

The CORS change is **transport-only**, separate from frozen V1 payloads. With the
flag unset, the API retains its original same-origin checks. With it set, only
`http://localhost:8081` and `http://127.0.0.1:8081` receive an exact
`Access-Control-Allow-Origin` for GETs to existing V1 routes. Host and actual peer
must still be loopback. There is no wildcard, credential/cookie allowance,
preflight/write support or new endpoint. Fetch uses simple GET/Accept requests.
Other ports and origins fail closed. Use port 8081 rather than accepting an Expo
fallback port. `--no-proxy-headers` prevents forwarded headers changing peer identity.

This is localhost/loopback development on this computer only. Physical iPhone
connectivity is **not enabled** and remains deferred to Phase 6E. There is no
LAN/tunnel access, auth or cloud deployment. No sync is performed; the existing
local snapshot and read lock must already exist. Do not create/repair snapshots
or run sync as a way to resolve a connection error.

Expo/Metro development bundles may contain local workspace-path metadata,
including project-root and route-module paths. Generated bundles are not
committed and must not be treated as distributable production artifacts.

Tests use injected mocked fetch, never real local health data. Run `npm run
typecheck`, `npm test`, `npx --no-install expo install --check`, then the repository
Python suite. Do not save real API responses, screenshots, credentials or private
health values as fixtures or test logs.
