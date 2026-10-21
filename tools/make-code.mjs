// Create an invite code and the matching entry for the Worker's CODES secret.
// Usage: node tools/make-code.mjs <name> [--per-hour 10] [--per-day 30] [--paid]
// Only the SHA-256 of the code is stored in the Worker; give the printed code to the person.
import { randomBytes, createHash } from 'node:crypto';

const args = process.argv.slice(2);
const name = args.find((a) => !a.startsWith('--'));
if (!name) { console.error('usage: node tools/make-code.mjs <name> [--per-hour N] [--per-day N] [--paid]'); process.exit(1); }
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? Number(args[i + 1]) : d; };
const words = ['alp', 'firn', 'grat', 'joch', 'kamm', 'lawi', 'moos', 'piz', 'sust', 'tobel', 'wang', 'zinne'];
const code = `${words[randomBytes(1)[0] % words.length]}-${randomBytes(6).toString('base64url')}`;
const hash = createHash('sha256').update(code).digest('hex');
const entry = { name, perHour: opt('--per-hour', 10), perDay: opt('--per-day', 30), paidFallback: args.includes('--paid') };
console.log(`code for ${name}: ${code}\n`);
console.log('add to the CODES secret JSON:');
console.log(JSON.stringify({ [hash]: entry }));
