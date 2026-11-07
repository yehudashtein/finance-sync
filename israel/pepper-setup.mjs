// One-time Pepper 2FA setup. Logs in with phone + password; Pepper sends an SMS,
// you type the code, and the reusable long-term token is saved into ../.env as
// PEPPER_OTP_TOKEN so later syncs run without SMS.
//   npm --prefix israel run pepper-setup      (run in your own terminal)
//
// Unlike One Zero, this fork has no separate triggerTwoFactorAuth for Pepper:
// the SMS is sent during scrape(), which then awaits creds.otpCodeRetriever for
// the code and surfaces the token via options.onAuthFlowComplete.

import { generateKeyPairSync } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { CompanyTypes, createScraper } from 'isb-pepper';
import { log } from './logger.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(here, '..', '.env');
dotenv.config({ path: envPath });

// Pepper wants international-flat digits (e.g. 9725...), not +972...; strip non-digits.
const phoneNumber = (process.env.PEPPER_PHONE || '').replace(/\D/g, '');
const password = process.env.PEPPER_PASSWORD;
if (!phoneNumber || !password) {
  log('Set PEPPER_PHONE (international, e.g. +972501234567) and PEPPER_PASSWORD in .env');
  process.exit(1);
}

// Device keypair: Pepper registers these public keys at bind and later verifies
// the /auth/login signature against them (login does NOT re-send the key), so we
// MUST reuse the same keypair for unattended re-auth. Reuse the persisted one if
// present (keeps the device identity stable), else generate fresh.
const b64 = (s) => Buffer.from(s, 'utf8').toString('base64');
const unb64 = (s) => Buffer.from(s, 'base64').toString('utf8');
function genPem(type, opts) {
  return generateKeyPairSync(type, opts).privateKey.export({ type: 'pkcs8', format: 'pem' });
}
const ecPrivateKeyPem = process.env.PEPPER_EC_KEY ? unb64(process.env.PEPPER_EC_KEY)
  : genPem('ec', { namedCurve: 'P-256' });
const rsaPrivateKeyPem = process.env.PEPPER_RSA_KEY ? unb64(process.env.PEPPER_RSA_KEY)
  : genPem('rsa', { modulusLength: 2048 });

function deviceIdFromJwt(jwt) {
  try { return JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString('utf8')).did || ''; }
  catch { return ''; }
}
function upsertEnv(text, key, val) {
  const line = `${key}=${val}`;
  return new RegExp(`^${key}=.*$`, 'm').test(text)
    ? text.replace(new RegExp(`^${key}=.*$`, 'm'), line)
    : `${text.trimEnd()}\n${line}\n`;
}

// Minimal one-line stdin reader. readline/promises can hang while the Camoufox
// browser subprocess is alive, so read one chunk directly with explicit resume().
function promptLine(question) {
  return new Promise((res) => {
    process.stdout.write(question);
    process.stdin.resume();
    process.stdin.setEncoding('utf8');
    const onData = (d) => {
      process.stdin.removeListener('data', onData);
      process.stdin.pause();
      res(String(d).replace(/[\r\n]+$/, '').trim());
    };
    process.stdin.on('data', onData);
  });
}

const otpCodeRetriever = async () => {
  log('[setup] OTP step reached — Pepper sent an SMS; waiting for you to enter the code');
  const code = await promptLine('SMS code sent by Pepper. Enter it: ');
  log(`[setup] received code (length ${code.length})`);
  return code;
};

// Keep the event loop alive: during Camoufox's async launch there is a window
// where no libuv handle is active, and Node would otherwise exit mid-scrape.
const keepAlive = setInterval(() => {}, 1 << 30);

async function main() {
  let longTermToken = '';
  const scraper = createScraper({
    companyId: CompanyTypes.Pepper,
    startDate: new Date(),  // minimal scrape — we only want the token
    onAuthFlowComplete: (info) => { longTermToken = info?.longTermToken || ''; },
  });

  log('[setup] calling scrape() — a browser starts in the background...');
  let result;
  try {
    // Pass the keypair so the bind registers OUR keys (reused for login re-auth).
    result = await scraper.scrape({ phoneNumber, password, otpCodeRetriever,
      ecPrivateKeyPem, rsaPrivateKeyPem });
  } catch (e) {
    log('[setup] scrape threw:', e?.stack || e?.message || String(e));
  }
  log(`[setup] scrape returned: success=${result?.success} errorType=${result?.errorType}`);

  const token = longTermToken || result?.persistentOtpToken || '';
  if (!token) {
    log('Login failed (no token captured):', result?.errorType, result?.errorMessage ?? '');
    return 1;
  }
  const deviceId = deviceIdFromJwt(token);
  if (!deviceId) {
    log('Login succeeded but no device id (did) in token; unattended re-auth unavailable.');
  }

  let text = readFileSync(envPath, 'utf8');
  text = upsertEnv(text, 'PEPPER_OTP_TOKEN', token);
  text = upsertEnv(text, 'PEPPER_EC_KEY', b64(ecPrivateKeyPem));
  text = upsertEnv(text, 'PEPPER_RSA_KEY', b64(rsaPrivateKeyPem));
  if (deviceId) text = upsertEnv(text, 'PEPPER_DEVICE_ID', deviceId);
  writeFileSync(envPath, text, 'utf8');
  log('Saved PEPPER_OTP_TOKEN + device credentials (PEPPER_DEVICE_ID / EC / RSA) to .env.');
  log(deviceId
    ? 'Pepper can now re-auth WITHOUT SMS via the device login path.'
    : 'Token saved (30-min); device re-auth not set up (no did).');
  if (!result?.success) log('Note: auth ok but data fetch failed this run:', result?.errorMessage ?? '');
  return 0;
}

main().then((code) => { process.exitCode = code; }).catch((e) => {
  log('Error:', e?.stack ?? e?.message ?? String(e));
  process.exitCode = 1;
}).finally(() => { clearInterval(keepAlive); });
