// LangSmith tracing for /chat: one root run per request with a child llm run per provider attempt, posted in one
// batch after the response (waitUntil). Without LANGSMITH_API_KEY nothing is sent; a tracing failure never affects
// the answer.

const DEFAULT_ENDPOINT = 'https://api.smith.langchain.com';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const isUuid = (s) => typeof s === 'string' && UUID.test(s);

// LangSmith orders runs by dotted_order: <UTC time as YYYYMMDDTHHMMSSffffffZ><run id>, joined with '.' per level.
const stamp = (ms) => new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\.(\d{3})Z$/, '$1000Z');

export function tracer(env, fetchFn, { name, inputs, tags = [], metadata = {} }) {
  if (!env.LANGSMITH_API_KEY) return null;
  const endpoint = (env.LANGSMITH_ENDPOINT || DEFAULT_ENDPOINT).replace(/\/$/, '');
  const project = env.LANGSMITH_PROJECT || 'default';
  const t0 = Date.now();
  const id = crypto.randomUUID();
  const root = {
    id, trace_id: id, dotted_order: stamp(t0) + id, name, run_type: 'chain', session_name: project,
    start_time: new Date(t0).toISOString(), inputs, tags, extra: { metadata },
  };
  const runs = [root];
  return {
    id,
    child(childName, runType, childInputs, childMetadata = {}) {
      const t = Date.now();
      const cid = crypto.randomUUID();
      const run = {
        id: cid, trace_id: id, parent_run_id: id, dotted_order: `${root.dotted_order}.${stamp(t)}${cid}`,
        name: childName, run_type: runType, session_name: project, start_time: new Date(t).toISOString(),
        inputs: childInputs, extra: { metadata: childMetadata },
      };
      runs.push(run);
      return {
        end(outputs, error) {
          run.end_time = new Date().toISOString();
          if (outputs) run.outputs = outputs;
          if (error) run.error = error;
        },
      };
    },
    end(outputs, error, extraMetadata = {}) {
      root.end_time = new Date().toISOString();
      if (outputs) root.outputs = outputs;
      if (error) root.error = error;
      Object.assign(root.extra.metadata, extraMetadata);
    },
    async flush() {
      try {
        const r = await fetchFn(`${endpoint}/runs/batch`, {
          method: 'POST', headers: { 'x-api-key': env.LANGSMITH_API_KEY, 'Content-Type': 'application/json' },
          body: JSON.stringify({ post: runs }),
        });
        if (!r.ok) console.error('langsmith trace failed', r.status);
      } catch (e) {
        console.error('langsmith trace failed', e.message);
      }
    },
  };
}

export async function sendFeedback(env, fetchFn, { runId, score, comment }) {
  const endpoint = (env.LANGSMITH_ENDPOINT || DEFAULT_ENDPOINT).replace(/\/$/, '');
  const r = await fetchFn(`${endpoint}/feedback`, {
    method: 'POST', headers: { 'x-api-key': env.LANGSMITH_API_KEY, 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_id: runId, key: 'user_score', score, comment: comment || undefined }),
  });
  if (!r.ok) throw new Error(`feedback ${r.status}`);
}
