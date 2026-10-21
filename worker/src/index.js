// Worker entry point.
import { handle } from './handler.js';

export { Limiter } from './durable.js';

export default { fetch: (request, env) => handle(request, env) };
