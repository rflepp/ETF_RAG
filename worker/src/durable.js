import { DurableObject } from 'cloudflare:workers';

import { LimiterCore } from './limiter.js';

// One global instance holds all counters (the group is small). Methods are called over DO RPC.
export class Limiter extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new LimiterCore(ctx.storage);
  }
  take(...a) { return this.core.take(...a); }
  refund(...a) { return this.core.refund(...a); }
  usage(...a) { return this.core.usage(...a); }
  spendToday() { return this.core.spendToday(); }
  reserve(...a) { return this.core.reserve(...a); }
  settle(...a) { return this.core.settle(...a); }
}
