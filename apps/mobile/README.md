# Mobile V1 · Today → Sleep

An Expo Router / React Native / TypeScript app with **demo as the default** and an
explicit **live-local** mode for Expo Web on this computer. Both use the frozen
[API V1 contract](../../docs/API_V1_CONTRACT.md). Live-local reads the existing
local snapshot through FastAPI; it never calls WHOOP, refreshes tokens or syncs.
These existing local profiles need no authentication. There is no persistent mobile
health-data storage; the opt-in device security preparation is documented below.

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

## Phase 6E-1: native-device security preparation

This is a disabled-by-default security profile, not physical-device connectivity.
Tailscale is **not installed/configured** by this phase. No Expo development client,
SecureStore, EAS build, pairing UI or real device token has been installed/created.
No LAN listener, broad interface binding, public tunnel or firewall change is enabled.
The existing demo, live-local Web, local API and Dashboard profiles remain separate.

### Future server profile

`WHOOP_API_PROFILE` defaults to `local`; only the explicit value `native-device`
selects the device gate. Unknown values fail startup safely. Device mode additionally
requires `WHOOP_DEVICE_HOST` (one exact lowercase DNS hostname, no scheme, port or
wildcard) and `WHOOP_DEVICE_VERIFIER_FILE` (an absolute path **outside the repository**).
These are server-only configuration; never copy them into Expo configuration.
`WHOOP_API_EXPO_WEB=1` and the device profile cannot be enabled together. Use the
existing Phase 6D process profile for Web development; do not turn device access on
as a way to repair a Web connection.

The future private HTTPS proxy must preserve that exact Host and forward to a
loopback-only FastAPI listener. Uvicorn must use `--no-proxy-headers`: the socket
peer, not `Forwarded`/`X-Forwarded-For`, supplies the loopback check. The device gate
does not itself create a proxy, TLS listener or network exposure.

In the device profile, **every request** must have a loopback peer, the exact Host,
one valid Authorization bearer credential, no query string and no browser Origin.
Only GET `/api/v1/health`, `/api/v1/today`, and `/api/v1/sleep/latest` are allowed.
There is no unauthenticated localhost-Host exception, trends access, sync, OAuth,
token route or device CORS allowance. All gate rejections return the same existing
403 `local_access_only` body shape and generic message; validation reasons are not
returned or logged. Existing V1 handlers, calculations and schemas are unchanged.

### Verifier and credential boundary

A future approved provisioning step must generate 32 cryptographically random bytes,
encoded as 64 lowercase hexadecimal characters. This is a separate development
secret, unrelated to WHOOP access/refresh tokens or client secrets. No provisioning
command or persistent real token is supplied in this phase. Tests use intentionally
predictable synthetic inputs only.

The Windows file stores exactly `version` (integer 1), `sha256` (the 64-character
lowercase SHA-256 hex digest of the ASCII token), and `expires_at` (an explicit UTC
timestamp in `YYYY-MM-DDTHH:MM:SSZ` format). It never stores the plaintext token.
The gate hashes the supplied token and uses constant-time digest comparison. It
re-reads the bounded verifier file on each request: deleting it or replacing the
digest revokes the old credential immediately; expiry is enforced at its deadline.
Missing, unreadable or malformed files deny access without exposing paths or details.

Provisioning must place the file outside Git, outside synced/shared folders, with
Windows ownership/DACL restricted to the intended local user/service and system
administrators. Directory permissions must also prevent replacement by other users.
The loader enforces the outside-repository boundary and fails closed on read errors;
it does **not** provision or audit Windows ACLs. That ACL review is a prerequisite
before enabling real device access. Never put plaintext in shell arguments, logs,
README examples, API responses, committed config or `EXPO_PUBLIC_*` variables.

On mobile, `native-device` must be selected explicitly and supplied a runtime
`device` configuration: `profile: 'native-device'`, an exact `approvedOrigin` using
HTTPS on its default port, and an asynchronous `credential` callback. The API base
URL must equal that origin. Only iOS accepts this profile; Web/Android reject it.
Public environment configuration supplies **no credential callback**, so selecting
the mode by environment alone cannot activate authenticated access.

Future pairing will supply the callback from SecureStore/iOS Keychain; neither is
implemented now. Supplying device credentials to demo or Web configuration rejects
that configuration. The HTTP client adds Authorization only to its three fixed API
requests, never Metro/assets, and retains timeout/abort, redirect rejection, response
validation and coherent snapshot acquisition. Credential lookup is inside the timeout;
if it finishes late, no request is sent. Screens stay transport-agnostic. Before
physical use, verify redirect/header behavior on the actual native transport as well
as private HTTPS, Host preservation and loopback socket peers. Mocked tests are not
a substitute for those device checks.

### Repeatable artifact privacy checks

From the repository root, these read-only commands print rule names and safe artifact
labels, never offending path contents. They do not fetch API responses or save bundles:

```powershell
.\.venv\Scripts\python.exe -B apps/mobile/scripts/privacy.py --source --upload-plan
.\.venv\Scripts\python.exe -B apps/mobile/scripts/privacy.py --development-url http://localhost:8081 --metadata apps/mobile/.expo
.\.venv\Scripts\python.exe -B apps/mobile/scripts/privacy.py --artifact <future-mobile-upload-or-build-archive>
```

Findings distinguish `source` repository files, `development` ephemeral Metro
bundles/metadata/source maps, and `distribution` build/upload artifacts. The scanner
detects absolute Windows paths (including escaped/URL-encoded forms), user-home
paths, inline source maps and UTF-16 metadata. It inspects ZIP/IPA/AAB and TAR/TGZ
members without extraction; unsupported compression, unreadable artifacts and scan
limits fail closed. The aggregate scan limit is 64 MiB, 4096 items and three nested
archive levels. This is a path/exclusion gate, not a general secret detector or proof
that arbitrary compiled/obfuscated artifacts contain no personal data.

The existing development bundle is expected to contain Expo/Metro project-root and
route-path metadata. Such findings are explicitly **not delivery approval**. Before
physical-device delivery, scan the actual native manifests, bundles, maps and build
artifact and remove personal absolute workspace paths. This phase detects rather
than sanitizes generated artifacts. Do not commit development bundles or use them
as distributable production artifacts.

### Future EAS upload boundary (no build or upload performed)

The preparatory `.easignore` is deny-by-default: only app/source files and the four
root app/package/lock/TypeScript config files are candidates. Backend `data/`, `.env`,
token files, WHOOP CSVs, classifications, logs, screenshots, caches, generated bundles,
source maps and `node_modules` are excluded. `src/design/tokens.ts` is an explicit
exception for visual design constants, not authentication tokens. Dependencies are
installed by the future builder using the unchanged package manifest and lockfile.

`--upload-plan` lists eligible non-ignored mobile source files and scans them; it
does not claim to reproduce EAS's archive builder. In this repository containing
private backend data, prepare a clean mobile-only build context from that reviewed
list, then inspect the actual EAS upload archive before any network upload. Never
assume private Git, Git ignores or a dry-run candidate list alone proves an upload
safe. Future new build config/assets must be explicitly reviewed for the allowlist.
Native signing, provisioning, installation, Keychain integration, private overlay
networking and end-to-end iPhone verification remain separate approved work.
