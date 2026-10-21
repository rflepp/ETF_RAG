# Security

ETF Compass is a public static site plus two small backends that spend LLM quota. What needs protecting is the API
keys, the LLM budget, and visitors' browsers (the invite code they store, and protection from injected HTML). The
investor profile and the chat live in the visitor's browser and are sent only to answer the current question. For
monitoring, each answered question (question, profile, retrieved ids, prompt, answer, the invite code's name and a
random per-visit session id) is traced to the private LangSmith project on the EU instance; no IP addresses or
invite codes are sent.

| Threat | Control |
|---|---|
| API keys leak through git | Keys live only in Cloudflare/GCP secrets and a gitignored `.env`; `tools/pre-commit` blocks `.env*`, `worker/codes.json` and key patterns; `.dockerignore` keeps them out of the image; full history scanned clean |
| Strangers use the LLM budget | Invite codes (at least 48 random bits, stored as SHA-256 only), per-code hourly/daily limits in a Durable Object, a per-IP request cap (Cloudflare rate limiting), a CHF/day cap on the paid fallback; the agent API needs bearer tokens and refuses to run open on Cloud Run |
| Oversized or malformed requests | Bodies over 256 kB → 413; messages, passages, funds and profile are clipped or rejected before any model call (`worker/src/prompt.js`, pydantic limits in `agent/api.py`) |
| Error messages leak internals | Provider errors (which can quote upstream responses) go to the Worker log only; clients get a generic message |
| XSS from model output or document text | Model replies are HTML-escaped before the minimal markdown renderer runs; citation chips escape every attribute; links from the data must be `https:`; a Content-Security-Policy allows only same-origin scripts/styles and network calls to the Worker |
| Clickjacking | The site refuses to run inside a frame (GitHub Pages can't send `frame-ancestors`); Worker responses send `frame-ancestors 'none'` |
| Responses cached or sniffed | Worker responses send `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` |
| Hallucinated or injected facts | The agent's verify step checks every citation, ISIN and percentage against tool results; the site drops citations and picks it didn't supply |
| Abuse of the dev server | `tools/dev-worker.mjs` listens on 127.0.0.1 only |
| SQL tool misuse | Read-only in-memory SQLite with an authorizer (SELECT on two tables only), a step limit and a row limit |
| Vulnerable dependencies | Dependabot (uv, npm, GitHub Actions); `npm audit` clean. Accepted: Pillow < 13 is pinned by fastembed, and only its text models run here, so Pillow never parses untrusted images |

Known limits: an invite-code holder controls the context the Worker sends to the model (the site does the retrieval),
so they can make the model say anything to themselves. That is acceptable for a small invited group. The invite code
is kept in the visitor's `localStorage`. Revoke a code with `node tools/make-code.mjs --remove <name>`
([docs/invite-codes.md](docs/invite-codes.md)).
