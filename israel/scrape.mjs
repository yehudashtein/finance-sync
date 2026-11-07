// Runs one israeli-bank-scrapers company and writes normalized JSON.
//   node scrape.mjs <max|onezero> <out.json>
// Credentials come from ../.env. Exit code 0 = ok, 2 = needs setup/login, 1 = error.

import { writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { CompanyTypes, createScraper } from 'israeli-bank-scrapers';
// One Zero uses reviewed PR #1209 (commit 5af6496, vendor/onezero-pr): Android TLS
// fingerprint + app mTLS cert (valid to 2027-10-05). Switch back once it's released.
import { createScraper as createOneZeroScraper } from 'isb-onezero';
// Pepper uses the @sergienko4 fork (vendor/pepper-fork): API-direct app protocol
// against sa.pepper.co.il. Reviewed 2026-11-06. Its CompanyTypes.Pepper === 'pepper'.
import { createScraper as createPepperScraper } from 'isb-pepper';

const here = dirname(fileURLToPath(import.meta.url));
dotenv.config({ path: resolve(here, '..', '.env') });
const env = process.env;

const COMPANIES = {
  max: {
    factory: createScraper,
    companyId: CompanyTypes.max,
    accountType: 'credit_card',
    institution: 'Max',
    credentials: () => (env.MAX_USERNAME && env.MAX_PASSWORD)
      ? { username: env.MAX_USERNAME, password: env.MAX_PASSWORD } : null,
    missing: 'Set MAX_USERNAME and MAX_PASSWORD in .env',
  },
  isracard: {
    factory: createScraper,
    companyId: CompanyTypes.isracard,
    accountType: 'credit_card',
    institution: 'Isracard',
    credentials: () => (env.ISRACARD_ID && env.ISRACARD_CARD6 && env.ISRACARD_PASSWORD)
      ? { id: env.ISRACARD_ID, card6Digits: env.ISRACARD_CARD6, password: env.ISRACARD_PASSWORD }
      : null,
    missing: 'Set ISRACARD_ID, ISRACARD_CARD6 (6 digits) and ISRACARD_PASSWORD in .env',
  },
  onezero: {
    factory: createOneZeroScraper,
    companyId: CompanyTypes.oneZero,
    accountType: 'checking',
    institution: 'One Zero',
    credentials: () => (env.ONEZERO_EMAIL && env.ONEZERO_PASSWORD && env.ONEZERO_OTP_TOKEN)
      ? { email: env.ONEZERO_EMAIL, password: env.ONEZERO_PASSWORD, otpLongTermToken: env.ONEZERO_OTP_TOKEN }
      : null,
    missing: 'Set ONEZERO_EMAIL/PASSWORD/PHONE in .env, then run: npm --prefix israel run onezero-setup',
  },
  pepper: {
    factory: createPepperScraper,
    companyId: 'pepper',  // isb-pepper CompanyTypes.Pepper
    accountType: 'checking',  // Pepper is a bank account (its card posts here)
    institution: 'Pepper',
    credentials: () => {
      if (!env.PEPPER_PHONE || !env.PEPPER_PASSWORD) return null;
      const creds = { phoneNumber: (env.PEPPER_PHONE || '').replace(/\D/g, ''), password: env.PEPPER_PASSWORD };
      // Fresh 30-min token = fast path (no browser auth calls). Fork falls back
      // to device re-auth (login + password, no SMS) when the token is stale.
      if (env.PEPPER_OTP_TOKEN) creds.otpLongTermToken = env.PEPPER_OTP_TOKEN;
      if (env.PEPPER_DEVICE_ID && env.PEPPER_EC_KEY && env.PEPPER_RSA_KEY) {
        creds.deviceId = env.PEPPER_DEVICE_ID;
        creds.ecPrivateKeyPem = Buffer.from(env.PEPPER_EC_KEY, 'base64').toString('utf8');
        creds.rsaPrivateKeyPem = Buffer.from(env.PEPPER_RSA_KEY, 'base64').toString('utf8');
      }
      return creds.otpLongTermToken || creds.deviceId ? creds : null;
    },
    missing: 'Set PEPPER_PHONE/PASSWORD in .env, then run: npm --prefix israel run pepper-setup',
  },
};

// Library dates are ISO instants at Israeli midnight; keep the Israeli calendar date.
const ilDate = (iso) => iso
  ? new Date(iso).toLocaleDateString('sv-SE', { timeZone: 'Asia/Jerusalem' })
  : null;

const [, , name, outPath] = process.argv;
const company = COMPANIES[name];
if (!company || !outPath) {
  console.error(`usage: node scrape.mjs <${Object.keys(COMPANIES).join('|')}> <out.json>`);
  process.exit(1);
}
const credentials = company.credentials();
if (!credentials) {
  console.error(company.missing);
  process.exit(2);
}

const lookback = Number(env.ISRAEL_LOOKBACK_DAYS || 365);
const startDate = new Date(Date.now() - lookback * 86400000);

const scraper = company.factory({
  companyId: company.companyId,
  startDate,
  combineInstallments: false,
  showBrowser: env.SHOW_BROWSER === '1',
  includeRawTransaction: true,
  // On failure, save what the page looked like (git-ignored data/ dir).
  storeFailureScreenShotPath: resolve(here, '..', 'data', `${name}-failure.png`),
});
// Keep the event loop alive across the scrape: the Camoufox launch (Pepper) has a
// window with no active libuv handle, during which a bare top-level await would
// let Node exit before scrape() settles.
const keepAlive = setInterval(() => {}, 1 << 30);
const result = await scraper.scrape(credentials);
clearInterval(keepAlive);

if (!result.success) {
  console.error(`${name}: ${result.errorType} ${result.errorMessage ?? ''}`);
  const loginErrors = ['INVALID_PASSWORD', 'CHANGE_PASSWORD', 'ACCOUNT_BLOCKED', 'TWO_FACTOR_RETRIEVER_MISSING'];
  process.exit(loginErrors.includes(result.errorType) ? 2 : 1);
}

const out = {
  source: name,
  institution: company.institution,
  accountType: company.accountType,
  scrapedAt: new Date().toISOString(),
  accounts: result.accounts.map((a) => ({
    accountNumber: String(a.accountNumber),
    balance: a.balance ?? null,
    currency: a.currency ?? 'ILS',
    txns: a.txns.map((t) => ({
      ...t,
      date: ilDate(t.date),
      processedDate: ilDate(t.processedDate),
    })),
  })),
};
writeFileSync(outPath, JSON.stringify(out, null, 2), 'utf8');
const n = out.accounts.reduce((s, a) => s + a.txns.length, 0);
console.error(`${name}: ${out.accounts.length} account(s), ${n} transactions`);
