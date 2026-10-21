# Invite codes: adding and removing people

The website's assistant only answers people who have an invite code. The Worker stores just the SHA-256 of each
code (Cloudflare secret `CODES`), so a code can't be recovered later. `worker/codes.json` is a local, gitignored copy
of that secret (hashes and limits only), because Cloudflare secrets can't be read back.

## Add a person

```sh
node tools/make-code.mjs anna --per-hour 10 --per-day 30     # prints the code once, updates worker/codes.json
cd worker && npx wrangler secret put CODES < codes.json      # upload; takes effect within seconds
```

- Send the printed code to the person privately (Signal, or in person, not a public channel). They enter it on
  https://rflepp.github.io/ETF_RAG/ under **Invite code**. It stays in their browser.
- Limits default to 10 questions per hour and 30 per day. `--paid` lets that code fall back to the paid Gemini model
  when the free ones are busy (capped at CHF 1/day for everyone together). It only takes effect once a
  `GEMINI_API_KEY` Worker secret is set, which it currently isn't.
- Want to keep the code yourself? Add it to `.env` (gitignored), e.g. `INVITE_CODE_ANNA=...`.

## Remove a person (or a leaked code)

```sh
node tools/make-code.mjs --remove anna
cd worker && npx wrangler secret put CODES < codes.json
```

Their next request gets "Unknown or revoked invite code". To replace a code, remove the old one and create a new one.

## See who has access

```sh
node tools/make-code.mjs --list                                         # name, limits, hash prefix
curl -s -H "Authorization: Bearer <code>" https://etf-advisor.rflepp.workers.dev/usage   # test one code
```

## If `worker/codes.json` is lost

You can't read the secret back. Create new codes for everyone you still want, which writes a fresh file, then upload it.
Uploading replaces the whole list, so every code that isn't in the file stops working.
