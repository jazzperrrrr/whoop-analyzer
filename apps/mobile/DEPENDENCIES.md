# Dependencies · Expo SDK 57

The approved exact direct version pins are in [package.json](package.json).
Dependencies are installed and package-lock.json records the resolved tree.
Use `npm ci` to reproduce that tree; use `npm install` after reviewed manifest changes.

| Package | Pin | Purpose |
| --- | --- | --- |
| expo | 57.0.24 | Stable SDK, Metro and development CLI |
| expo-constants | 57.0.19 | Runtime configuration required by Router |
| expo-linking | 57.0.10 | Router linking support |
| expo-router | 57.0.22 | File-based Today and Sleep navigation |
| expo-status-bar | 57.0.1 | System status-bar contrast |
| react | 19.2.3 | Component runtime |
| react-dom | 19.2.3 | Local browser preview renderer |
| react-native | 0.86.3 | Native components and transitions |
| react-native-gesture-handler | 2.32.0 | Pin Router's native peer tree to SDK 57 |
| react-native-reanimated | 4.5.1 | Pin Router/drawer native peer tree to SDK 57 |
| react-native-safe-area-context | 5.7.0 | Screen insets |
| react-native-screens | 4.26.0 | Native navigation screens |
| react-native-web | 0.21.2 | Local Windows browser preview; Expo compatibility patch |
| react-native-worklets | 0.10.1 | SDK 57-compatible peer for Reanimated/Expo core |
| @react-native/jest-preset | 0.86.3 | Native test transforms/mocks required by jest-expo |
| @testing-library/react-native | 13.3.3 | Component and navigation assertions |
| @types/jest | 29.5.14 | Test types |
| @types/react | 19.2.4 | Component types; Expo compatibility patch |
| jest | 29.7.0 | Test runner compatible with jest-expo |
| jest-expo | 57.0.5 | Expo test preset |
| react-test-renderer | 19.2.3 | Matching React renderer required by testing-library 13 |
| typescript | 6.0.3 | Static contract and screen checks |

SDK pins follow the official [SDK 57 template](https://github.com/expo/expo/blob/sdk-57/templates/expo-template-default/package.json)
and [compatibility list](https://github.com/expo/expo/blob/sdk-57/packages/expo/bundledNativeModules.json).
Native version pins use the installed Expo SDK 57 bundledNativeModules.json.
The online Expo compatibility check additionally requires the web and React type
patches above. Router's broad peer ranges initially resolved incompatible newer
gesture-handler/Reanimated/worklets versions; explicit exact pins keep one coherent
SDK 57 tree. These packages were already transitive dependencies. Application
motion still uses built-in APIs; no new animation behavior was added.

No chart, persistence, HTTP client or OpenAPI generation package is included.
Expo Router's transitive packages do not enable cloud deployment or a live data
client. No Expo SDK upgrade or audit-driven dependency change was made.

Verification uses `npm ls --depth=0`, `expo install --check`, `npm run typecheck`
and `npm test`. Native peer inspection also checks the three pinned packages with
`npm ls ... --all`. Audits are read-only; do not run automatic audit fixes.

The Router test asserts its typed `renderRouter().getPathname()` result. Jest
transforms the react-native package family so the official safe-area mock works.
No fake matcher declaration, relaxed TypeScript setting or application behavior
change is needed. Native device and bundle verification are separate follow-up work.
