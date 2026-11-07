// One-time One Zero 2FA setup. Sends an SMS code to ONEZERO_PHONE, asks you
// for it, and saves the resulting long-term token into ../.env as
// ONEZERO_OTP_TOKEN so later syncs run without SMS.
//   npm --prefix israel run onezero-setup      (run in your own terminal)

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { createInterface } from 'node:readline/promises';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { CompanyTypes } from 'israeli-bank-scrapers';
import { createScraper } from 'isb-onezero'; // reviewed PR #1209, see scrape.mjs

const here = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(here, '..', '.env');
dotenv.config({ path: envPath });

const phone = process.env.ONEZERO_PHONE;
if (!phone || !phone.startsWith('+')) {
  console.error('Set ONEZERO_PHONE in .env in international format, e.g. +972501234567');
  process.exit(1);
}

const scraper = createScraper({ companyId: CompanyTypes.oneZero, startDate: new Date() });
const trigger = await scraper.triggerTwoFactorAuth(phone);
if (!trigger.success) {
  console.error('Could not send SMS:', trigger.errorType, trigger.errorMessage ?? '');
  process.exit(1);
}

const rl = createInterface({ input: process.stdin, output: process.stdout });
const code = (await rl.question(`SMS code sent to ${phone}. Enter it: `)).trim();
rl.close();

const res = await scraper.getLongTermTwoFactorToken(code);
if (!res.success) {
  console.error('Code rejected:', res.errorType, res.errorMessage ?? '');
  process.exit(1);
}

// Log in once to exchange the SMS token for the ~10-year idToken.
const login = await scraper.login({
  email: process.env.ONEZERO_EMAIL,
  password: process.env.ONEZERO_PASSWORD,
  otpLongTermToken: res.longTermTwoFactorAuthToken,
});
if (!login.success) {
  console.error('Login failed:', login.errorType, login.errorMessage ?? '');
  process.exit(1);
}
const token = scraper.idToken || login.persistentOtpToken;
let text = readFileSync(envPath, 'utf8');
text = /^ONEZERO_OTP_TOKEN=.*$/m.test(text)
  ? text.replace(/^ONEZERO_OTP_TOKEN=.*$/m, `ONEZERO_OTP_TOKEN=${token}`)
  : `${text.trimEnd()}\nONEZERO_OTP_TOKEN=${token}\n`;
writeFileSync(envPath, text, 'utf8');
console.log('Saved ONEZERO_OTP_TOKEN to .env. One Zero can now sync unattended.');
