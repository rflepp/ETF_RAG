// Minimal headless-Chrome driver over the DevTools protocol (Node 24 has WebSocket built in, no dependencies).
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launch({ width = 390, height = 844, port = 9333, mobile = true } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'etf-chrome-'));
  const proc = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${dir}`, '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
  let targets;
  for (let i = 0; i < 50; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); if (targets.some((t) => t.type === 'page')) break; } catch (e) { /* not up yet */ }
    await sleep(150);
  }
  const page = targets && targets.find((t) => t.type === 'page');
  if (!page) { proc.kill(); throw new Error('could not reach Chrome'); }
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const listeners = [];
  const logs = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(m.error.message)) : p.res(m.result); }
    else if (m.method) {
      if (m.method === 'Runtime.consoleAPICalled') logs.push(`[${m.params.type}] ` + m.params.args.map((a) => a.value ?? a.description ?? '').join(' '));
      if (m.method === 'Runtime.exceptionThrown') logs.push('[exception] ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
      listeners.forEach((l) => l(m));
    }
  };
  const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
  await send('Page.enable'); await send('Runtime.enable'); await send('DOM.enable');
  const device = async (w, h, isMobile = mobile) => {
    await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 2, mobile: isMobile });
    if (isMobile) await send('Emulation.setTouchEmulationEnabled', { enabled: true });
  };
  await device(width, height);

  const api = {
    send, logs, device,
    async goto(url) {
      await send('Page.navigate', { url });
      await sleep(300);
      for (let i = 0; i < 100; i++) { const r = await api.eval('document.readyState'); if (r === 'complete' || r === 'interactive') break; await sleep(100); }
    },
    async eval(expr) {
      const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
      if (r.exceptionDetails) throw new Error('eval failed: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
      return r.result.value;
    },
    async waitFor(expr, timeout = 8000) {
      const t0 = Date.now();
      while (Date.now() - t0 < timeout) { try { if (await api.eval(expr)) return true; } catch (e) { /* page changing */ } await sleep(100); }
      throw new Error('timeout waiting for: ' + expr);
    },
    async shot(file, { full = false } = {}) {
      const r = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full });
      writeFileSync(file, Buffer.from(r.data, 'base64'));
    },
    async tap(selector) {
      const pt = await api.eval(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return null; e.scrollIntoView({block:'center'}); const b = e.getBoundingClientRect(); return {x: b.x + b.width/2, y: b.y + b.height/2}; })()`);
      if (!pt) throw new Error('no element: ' + selector);
      await sleep(60);
      const p2 = await api.eval(`(() => { const b = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return {x: b.x + b.width/2, y: b.y + b.height/2}; })()`);
      for (const type of ['mousePressed', 'mouseReleased']) await send('Input.dispatchMouseEvent', { type, x: p2.x, y: p2.y, button: 'left', clickCount: 1 });
    },
    async type(selector, text) {
      await api.tap(selector);
      await send('Input.insertText', { text });
    },
    async setFiles(selector, files) {
      const doc = await send('DOM.getDocument', { depth: 1 });
      const { nodeId } = await send('DOM.querySelector', { nodeId: doc.root.nodeId, selector });
      await send('DOM.setFileInputFiles', { files, nodeId });
      // setFileInputFiles fires input+change events itself
    },
    onEvent(fn) { listeners.push(fn); },
    async close() { try { ws.close(); } catch (e) { /* ignore */ } proc.kill(); }
  };
  return api;
}
