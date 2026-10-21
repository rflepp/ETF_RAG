// Manage invite codes for the Worker's CODES secret.
//   node tools/make-code.mjs <name> [--per-hour 10] [--per-day 30] [--paid]   create a code for <name>
//   node tools/make-code.mjs --remove <name>                                  revoke every code of <name>
//   node tools/make-code.mjs --list
// Cloudflare secrets cannot be read back, so worker/codes.json (gitignored) mirrors the secret. It holds only the
// SHA-256 of each code; the code itself is printed once. Upload after every change:
//   cd worker && npx wrangler secret put CODES < codes.json
import { createHash, randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const FILE = new URL('../worker/codes.json', import.meta.url);
const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? Number(args[i + 1]) : d; };
const codes = existsSync(FILE) ? JSON.parse(readFileSync(FILE, 'utf8')) : {};
const save = () => {
  writeFileSync(FILE, JSON.stringify(codes, null, 1) + '\n', { mode: 0o600 });
  console.log('\nupload:  cd worker && npx wrangler secret put CODES < codes.json');
};

if (args.includes('--list')) {
  for (const [hash, e] of Object.entries(codes)) console.log(`${e.name.padEnd(16)} ${e.perHour}/h ${e.perDay}/day${e.paidFallback ? ' paid' : ''}  ${hash.slice(0, 12)}…`);
  process.exit(0);
}

if (args.includes('--remove')) {
  const name = args[args.indexOf('--remove') + 1];
  const hit = Object.keys(codes).filter((h) => codes[h].name === name);
  if (!hit.length) { console.error(`no code for "${name}"`); process.exit(1); }
  for (const h of hit) delete codes[h];
  console.log(`removed ${hit.length} code(s) of ${name}`);
  save();
  process.exit(0);
}

const name = args.find((a, i) => !a.startsWith('--') && !String(args[i - 1]).startsWith('--per'));
if (!name) { console.error('usage: node tools/make-code.mjs <name> [--per-hour N] [--per-day N] [--paid] | --remove <name> | --list'); process.exit(1); }
const words = ['alp', 'firn', 'grat', 'joch', 'kamm', 'lawi', 'moos', 'piz', 'sust', 'tobel', 'wang', 'zinne'];
const code = `${words[randomBytes(1)[0] % words.length]}-${randomBytes(9).toString('base64url')}`;
codes[createHash('sha256').update(code).digest('hex')] = { name, perHour: opt('--per-hour', 10), perDay: opt('--per-day', 30), paidFallback: args.includes('--paid') };
console.log(`code for ${name}: ${code}   (shown once; send it privately)`);
save();
