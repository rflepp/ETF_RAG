// Worker entry point.
import { handle } from './handler.js';

export { Limiter } from './durable.js';

export default { fetch: (request, env, ctx) => handle(request, env, { waitUntil: (p) => ctx.waitUntil(p) }) };
