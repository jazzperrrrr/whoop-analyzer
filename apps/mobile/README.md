# Mobile V1 · Today → Sleep

An Expo Router / React Native / TypeScript preview using **synthetic data only**.
The two screens use the frozen [API V1 contract](../../docs/API_V1_CONTRACT.md).
There is no live backend connection, WHOOP request, authentication or persistent
health-data storage. A visible sample-data label identifies every preview screen.

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
The web preview provides a local way to review the UI on Windows. No Python
server is required. Start scripts do not enable a LAN or tunnel connection.

## Structure

- `app/`: root stack, Today and Sleep routes only.
- `src/api/`: injectable `ApiClient`, synthetic `MockApiClient`, shared loader.
- `src/features/`: feature-specific screen presentation and aggregate trend bars.
- `src/components/`: reusable typography, metrics, availability and page states.
- `src/design/`: restrained light palette, type/spacing/radius/motion tokens,
  formatting, central reason/quality/error copy, reduced-motion preference.
- `src/storage/`: in-memory coherent snapshot pair and cache interface.
- `src/types/`: static types derived from the frozen in-memory OpenAPI components.
- `tests/`: unit, component and real Router navigation tests. Network is forbidden.

The public wire field names remain snake_case. A later `HttpApiClient` can replace
the mock through `ApiProvider`; it must validate responses at the boundary. Screens
do not fetch or inspect CSVs. The loader publishes Today and Sleep together only
when `(schema_version, analysis_version, snapshot_id)` agrees. `cached_at` is phone
cache time and is distinct from `generated_at`, `report_date` and unknown collection
time. The cache is volatile and disappears when the provider/app is destroyed.

## Contract and synthetic data checks

The preview defaults to `VisualDemoApiClient`, serving only the separate, static
`src/api/fixtures/visual_demo.json`. Every value is completely fictional, with no
personal/device source or network access. Screens display **SAMPLE DATA · DEMO**.
The fixture includes coherent aggregate stages and signed sleep need, varied
history, absent dates, and one pending observation. Values and prior summaries
are authored fixture outputs; the UI only formats them.

`MockApiClient` and the contract drift check continue to use the unchanged frozen
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
