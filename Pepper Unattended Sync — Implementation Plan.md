# Pepper Unattended Sync — Implementation Plan

Nov 7, 2026

**Goal: make Pepper (Bank Leumi) sync fully unattended, like the other four sources (Chase, Max, One Zero, Isracard).** Pepper transactions and balance already work via the vendored fork, but each sync needs a fresh login because Pepper's access token lasts only 30 minutes and has no refresh token. The real Pepper app refreshes its session with **password only, no SMS** — replicating that is the remaining work. This doc captures everything needed to implement it later; it is deferred, not abandoned. Current workaround: Pepper is semi-manual — run `pepper-setup` (one SMS) when fresh Pepper data is wanted, then it syncs within the 30-minute window.

## Background: how Pepper auth works

Pepper's auth is **Transmit Security** behind `sa.pepper.co.il`, with data on `fe-sec.pepper.co.il/graphql` (behind an AWS WAF that only accepts the `okhttp/4.12.0` user-agent).

- **First-time login** uses `POST /api/v2/auth/bind` → triggers an **SMS OTP**. This registers the device and returns a `device_id`.
- **Returning device** uses `POST /api/v2/auth/login` → **password only, no SMS**, because the device is already registered.
- The session **token is a \~30-minute access JWT** (iat→exp = 30 min). **There is no refresh token** in any auth response — confirmed by capture. The app stays logged in by re-running login+password on the trusted device.
- The device is recognized by a **`device_id` (`did`)** plus a **`device_name`**. Requests are signed (ECDSA P-256) with a **`Content-Signature` header whose `key-id` is a SHA-256 of the public key, and the public key travels in the request body** — so signing is self-contained (a fresh keypair is accepted for integrity; device identity is the `did`/`device_name`, not the key, pending the open question in Risks).
- TLS: Pepper requires a browser-grade TLS fingerprint (Cloudflare JA4 check), so the fork drives it through **Camoufox** (`requiresBrowserTls: true`), not Node mTLS. A standalone Python/Node client would be blocked — the implementation must stay inside the fork's Camoufox transport.

## The password-only re-auth mechanism

Captured from the real app (mitmproxy, 2026-11-07) when the app re-opened and asked for **password only**. Three calls:

1. `POST /api/v2/auth/login?aid=DIGITAL_BLL&did=<device_id>&locale=en-US&tsm=<ms>`
   - Headers: `Content-Type`, `X-TS-Client-Version`, `Authorization: TSToken <static public id>`, `Content-Signature` (ECDSA), `User-Agent`.
   - Body: `{ headers: [{type:flow_id,...},{type:uid, uid:<phone>}], data: { collection_result: <device fingerprint incl. device_name, hw_authenticators.user_registered=true> } }`.
   - Response: `{ data: { challenge: "<fch>", control_flow: [{ methods: [{ assertion_id: "<id>", type: "password", status: "registered" }] }] } }`. Also yields a `session_id` used as `sid` next.
2. `POST /api/v2/auth/assert?aid=DIGITAL_BLL&did=<device_id>&sid=<session_id>&locale=en-US&tsm=<ms>`
   - Body: `{ action:"authentication", assert:"authenticate", assertion_id:"<id>", fch:"<challenge>", data:{ password:"<pwd>" }, method:"password" }`.
   - Response: `{ data: { token: "<~30-min access JWT>" } }`.
3. (App then calls `config_menu` etc. — not needed for scraping.)

**No SMS anywhere.** The token from step 2 is exactly what the fork's warm-start consumes as the bearer for the GraphQL transactions call. So the whole job is: get that token without SMS, then hand it to the existing (now-fixed) transactions path.

## Current state

**Done:**

- Pepper **transactions + balance work** (verified on the Ils account: signed amounts, dedup). Two bugs fixed: `oshTransactionsNew` → `oshTransactionsPagination` (cursor `nextTransactionSerialNumber`); and the scraper now targets the **Ils** current account (Foreign/Securities return 400/500).
- PR opened upstream: [danielbenzvi/israeli-bank-scrapers#1](https://github.com/danielbenzvi/israeli-bank-scrapers/pull/1) (branch `fix/pepper-transactions-pagination`).
- Local fork rebuilt with the fix; `sync_all.py pepper` works given a valid token.

**Remaining (this doc):** the 30-minute token refresh. Right now a valid `PEPPER_OTP_TOKEN` must be obtained via `pepper-setup` (SMS) and lasts 30 min, so Pepper is **semi-manual**. Implementing the password-only `/auth/login` warm path removes the SMS and makes it unattended.

## Implementation plan

Work inside the vendored fork (`israel/vendor/pepper-fork`), rebuild with `npx tsup`, and sync via the existing Python.

1. **Persist device credentials from a bind.** The fork generates a fresh EC/RSA keypair per run and discards the `device_id`. Change the SMS-bind path so, on success, it outputs: the `device_id` (from the bind response, `carry.deviceId`), the EC private key (PEM), and the RSA private key (PEM). Save them to `.env` as `PEPPER_DEVICE_ID`, `PEPPER_EC_KEY`, `PEPPER_RSA_KEY` (extend `pepper-setup.mjs`).
2. **Run one SMS bind** (`pepper-setup`) to populate those values once.
3. **Inject the persisted keypair** instead of generating fresh: `SmsOtpFlow.prep.ts` → `generateKeypairs` should import the PEM keys from creds when present.
4. **Add the `/auth/login` warm path.** Add `auth.login` to the Pepper `paths`; define a `LOGIN_STEP` (POST `/auth/login`, body = device `collection_result` + public key, query `aid/did/locale/tsm`; extract `challenge`, `assertion_id`, `sid`, `device_id` to carry). Reuse the existing password-assert step. Drop the OTP step on this path.
5. **Branch cold vs warm.** When `PEPPER_DEVICE_ID` + keys exist, run `[LOGIN, ASSERT_PWD]` (no SMS); otherwise `[BIND, ASSERT_PWD, ASSERT_OTP]` (first-time SMS). The flow currently runs a single `config.steps` array — this needs a conditional selection in the flow runner / config.
6. **Wire `scrape.mjs`** Pepper creds to pass `{ phoneNumber, password, deviceId, ecKey, rsaKey }` for the warm path (keep `otpLongTermToken` as a fast path if a token is still fresh).
7. **Live-test** against the account; iterate (see Risks for the one uncertainty). Each failed attempt may need a fresh SMS bind.
8. **Fold into the existing scheduled sync** once it works unattended for several runs across token expiries.

## Key technical references

All paths under `israel/vendor/pepper-fork/src/Scrapers/Pipeline/` unless noted.

| File | Role / change |
| --- | --- |
| `Registry/Config/PipelineBankConfig.ts` | Pepper `headless` block: `identityBase`, `paths` (add `auth.login`), `staticAuth`, `graphqlHeaders`, `requiresBrowserTls:true` |
| `Registry/Config/PipelineBankConfigPepperSteps.ts` | `BIND_STEP` / `ASSERT_PWD_STEP` / `ASSERT_OTP_STEP`; add `LOGIN_STEP` here |
| `Registry/Config/PipelineBankConfigPepper.ts` | `PEPPER_API_DIRECT_CALL`: `steps` array, `warmStart`, `signer` (ECDSA-P256 DER) |
| `Registry/Config/PipelineBankConfigPepperDevice.ts` | `DEVICE_DETAILS_LITERAL` — fixed `device_name` |
| `Registry/Config/PipelineBankConfigPepperFingerprint.ts` | `TS_CLIENT_VERSION`, `AID`, fingerprint/collection\_result |
| `Mediator/ApiDirectCall/Flow/SmsOtpFlow.prep.ts` | `generateKeypairs` — inject persisted keypair here |
| `Mediator/ApiDirectCall/Flow/SmsOtpFlow.result.ts` | flow result / `carry.deviceId` — surface device creds to persist |
| `Mediator/ApiDirectCall/ApiDirectCallActions.callback.ts` | `onAuthFlowComplete` payload — extend with device\_id + keys |
| Pepper scrape shape (already fixed) | `Banks/Pepper/graphql/PepperQueries.ts`, `Banks/Pepper/scrape/PepperShape*.ts` |
| `israel/scrape.mjs`, `israel/pepper-setup.mjs` (finance project) | pass creds; persist device credentials to `.env` |

**Key constants (captured):**

- Fork fixed `device_name`: `e71c2b2f5a0cbd94` (used for bind → what Pepper registers).
- Static public auth header: `TSToken 7cf2d7a7-681d-450a-ab23-06e48d2b8fd6; tid=digital_client_token_token` (shared across installs, not a secret).
- `aid=DIGITAL_BLL`, `locale=en-US`, GraphQL WAF header `user-agent: okhttp/4.12.0`.
- Endpoints: `https://sa.pepper.co.il/api/v2/auth/{bind,login,assert,logout}`, data `https://fe-sec.pepper.co.il/graphql`.
- The app's own `device_id` and `device_name` are **not reusable** — the app signs with a hardware-backed key we cannot extract. Use the fork's own bind-registered device.
- Capture artifacts live under a local temp dir (`reauth_*.json`, `*.query.graphql`, `*.resp.json`, patched APK, APKEditor.jar).

## Feasibility risks & open questions

- **Device ↔ key binding (the main one).** Does `/auth/login` verify the `Content-Signature` against the public key registered at bind, or accept any valid signature once the `device_id` is known? If bound to the key, we must reuse the exact bind keypair (step 1 persistence covers this). If not, a fresh key works and only `device_id` needs persisting. Only a live run settles it — persisting the keypair covers both cases, so do that.
- **Device de-registration.** If the user logs out of the real app, changes password, or Pepper expires the device, `/auth/login` will fail and a new SMS bind is needed. The warm path must detect this (login returns a non-password / re-bind control\_flow) and fall back to the SMS path with a clear `NEEDS LOGIN` message.
- **`collection_result` staleness.** The device fingerprint block is largely static in the fork; timestamps (`master_key_generated`, `tsm`) must be refreshed per call. Watch for Pepper rejecting a stale or mismatched fingerprint.
- **Camoufox dependency.** The flow needs the browser TLS fingerprint; keep running through Camoufox. The \~1.3 GB Camoufox binary and the native `better-sqlite3` build must stay installed (reinstalling `isb-pepper` requires `npm rebuild better-sqlite3` for Node 22).
- **App-version drift.** `X-TS-Client-Version` / `APK_VERSION` may need bumping if Pepper tightens checks. Captured working value: `8.2.3 (10870)` client version, APK 11.x.
- **Terms of service.** This automates a bank login outside the official app; same risk posture the user already accepted for One Zero and Pepper.

## Capture & test methodology

How the app traffic was captured (reuse to re-capture or verify):

1. **Patch the APK to trust a user CA.** apktool (via apk-mitm) **corrupts this app's resources** (nine-patch/drawable crashes) — do **not** use it. Instead use **APKEditor** (`pepper-patch/APKEditor.jar`): merge the split APKs → decode → add `android:debuggable="true"` to `<application>` → build → `zipalign` + `apksigner` with `~/.android/debug.keystore` → `adb install`. `debuggable=true` activates the app's own `debug-overrides` (trust user CAs). This app has **no certificate pinning**, so that is sufficient.
2. **Proxy over USB.** `mitmdump -p 8080 -w flows.mitm` on the PC; `adb reverse tcp:8080 tcp:8080`; `adb shell settings put global http_proxy 127.0.0.1:8080`; install mitmproxy's CA on the phone as a user cert. **Reset after:** `adb shell settings put global http_proxy :0` and `adb reverse --remove-all`.
3. **Parse** `flows.mitm` with mitmproxy's `FlowReader` (Python) — filter `sa.pepper.co.il` (auth) and `fe-sec.pepper.co.il` (GraphQL, grouped by the `queryname` header).

**Test dependency:** building the warm path needs real device credentials, which only come from a one-time SMS bind. Expect to run `pepper-setup` (SMS) at least once to seed credentials, and possibly again between failed test iterations.

**Security cleanup (do when done testing):** the phone currently has a **re-signed, debuggable Pepper** that trusts extra certificates. Reinstall the official Pepper from the Play Store to restore full security. Also remove the mitmproxy CA from the phone's user certificates.
