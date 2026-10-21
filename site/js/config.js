// Set after `wrangler deploy` (the workers.dev URL). Local development: tools/dev-worker.mjs on port 8787.
const host = typeof location === 'undefined' ? '' : location.hostname;
export const WORKER_URL = host === 'localhost' || host === '127.0.0.1'
  ? 'http://localhost:8787'
  : 'https://etf-advisor.rflepp.workers.dev';
