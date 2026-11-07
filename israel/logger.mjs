// Shared Node logger — appends to the same logs/finance.log as the Python side,
// and echoes to stderr. Simple log-to-file for now.
//   import { log } from './logger.mjs';  log('message');

import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const LOG_DIR = resolve(here, '..', 'logs');
const LOG_FILE = resolve(LOG_DIR, 'finance.log');

export function log(...args) {
  const msg = args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
  const line = `${new Date().toISOString().replace('T', ' ').slice(0, 19)} [node] INFO ${msg}\n`;
  try {
    mkdirSync(LOG_DIR, { recursive: true });
    appendFileSync(LOG_FILE, line);
  } catch {
    // never let logging break a scrape
  }
  process.stderr.write(line);
}
