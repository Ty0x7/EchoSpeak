// Chat's rich answers in the real production build and a real browser: Mermaid, math, charts and
// tables while a reply streams, after it finishes, and after a reload from saved history, in dark
// and light themes and at desktop and phone widths. Real Mermaid and KaTeX draw everything;
// only the backend is a fixture (the same NDJSON events and history shape the backend sends).
// No model is called. Requires an existing Playwright module/browser, as in check-workbench-browser.cjs:
//   ECHOSPEAK_PLAYWRIGHT_MODULE=<path to playwright>  ECHOSPEAK_CHROMIUM_PATH=<chrome.exe>  (both optional)
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.ECHOSPEAK_PLAYWRIGHT_MODULE || 'playwright');
const repo = path.resolve(__dirname, '../../..');
const build = path.join(repo, 'apps/web/dist');
const out = process.env.ECHOSPEAK_CHAT_RENDER_ARTIFACT_DIR || path.join(repo, '.test-state/chat-render-browser');
fs.mkdirSync(out, { recursive: true });
if (!fs.existsSync(path.join(build, 'index.html'))) throw new Error('Build the web app first (apps/web: npx vite build)');

// What models really write: a diagram with the usual syntax slips (unquoted parentheses and colons,
// a unicode arrow), ChatGPT-style \( \) and \[ \] math, a chart block, a table, a diagram that is
// truly broken, and a price that must stay text.
const REPLY = [
  "Here's the flow, step by step.",
  '',
  '```mermaid',
  'graph LR',
  '  U[User (browser)] --> API{Route: /chat}',
  '  API --> DB[(Postgres)]',
  '  API --> C(Cache: redis)',
  '  C → U',
  '```',
  '',
  'While this paragraph is still streaming, the diagram above must already be drawn. ' + 'The rest of the answer keeps arriving in small pieces. '.repeat(40),
  '',
  'The cost grows as \\(O(n \\log n)\\), and the recurrence is:',
  '',
  '\\[',
  'T(n) = 2T\\left(\\frac{n}{2}\\right) + n',
  '\\]',
  '',
  '```chart',
  '{"kind": "bar", "title": "Latency (ms)", "labels": ["Cache", "DB"], "series": [{"name": "p50", "values": [4, 38]}]}',
  '```',
  '',
  '| Layer | Role |',
  '|---|---|',
  '| API | Routes requests |',
  '| Cache | Fast reads |',
  '',
  'This one is broken on purpose:',
  '',
  '```mermaid',
  'flowchart TD',
  '  A[[[ -->',
  '```',
  '',
  'That costs $5 a month.',
].join('\n');

const THREAD = 'chat-render';
const EXEC = 'exec-render';
const MESSAGE = 'msg_render';
// The backend tags every event with the client's request id; the app drops events for any other request.
function events(requestId) {
  const base = { request_id: requestId, agent_id: 'chat', message_id: MESSAGE };
  const rows = [{ type: 'run_start', execution_id: EXEC, room_id: '', autonomous: false, request_id: requestId },
    { type: 'agent_start', agent: { id: 'chat', name: 'fixture-model', title: 'Chat', initials: 'F', provider: 'lmstudio' }, ...base },
    { type: 'step_start', step: 1, ...base }];
  for (let i = 0; i < REPLY.length; i += 14) rows.push({ type: 'agent_token', step: 1, data: REPLY.slice(i, i + 14), ...base });
  rows.push({ type: 'agent_done', text: REPLY, success: true, ...base },
    { type: 'final', runtime: 'lean', response: REPLY, success: true, messages: [{ message_id: MESSAGE, agent_id: 'chat', text: REPLY, success: true }] },
    { type: 'journal_done', status: 'completed' });
  return rows.map((row, index) => ({ ...row, at: 1793200000 + index / 10, _replay_seq: index + 1, _recovered: false }));
}
const userMessage = { role: 'user', text: 'Explain the request path', at: 1793200000, item_id: 'item-user', execution_id: EXEC };
const assistantMessage = { role: 'assistant', text: REPLY, at: 1793200010, item_id: 'item-reply', execution_id: EXEC, backend_success: true, error: '',
  agent_id: 'chat', agent_name: 'fixture-model', provider: 'lmstudio', message_id: MESSAGE, timeline: [{ kind: 'text', step: 1, text: REPLY, at: 1793200001 }],
  delegated_by: null, agent_role: '', stop_reason: '' };
const savedTurn = { execution: { id: EXEC, request_id: 'req-render', thread_id: THREAD, status: 'completed', query: userMessage.text, metadata: { runtime: 'lean_v1', environment: 'chat' } },
  execution_id: EXEC, request_id: 'req-render', status: 'completed', terminal_status: 'complete', created_at: 1793200000, completed_at: 1793200010,
  messages: [userMessage, assistantMessage], items: [], tool_runs: [], research_runs: [], approvals: [], verification: {}, progress: [], files: [],
  terminal_runs: [], memory_records: [], execution_projection: {}, error: '', success: true };

const mime = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.json': 'application/json' };
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/__stream')) {
    // The reply in small timed pieces, as the backend streams it.
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Access-Control-Allow-Origin': '*' });
    const rows = events(new URL(req.url, 'http://local').searchParams.get('rid') || 'req-render');
    let index = 0;
    const timer = setInterval(() => {
      if (index >= rows.length) { clearInterval(timer); res.end(); return; }
      res.write(JSON.stringify(rows[index++]) + '\n');
    }, 40);
    return;
  }
  let file = path.resolve(build, '.' + decodeURIComponent(new URL(req.url, 'http://local').pathname));
  if (!file.startsWith(build + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(build, 'index.html');
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});

async function lastReply(page) {
  return page.evaluate(() => {
    const article = [...document.querySelectorAll('article.lm')].pop();
    if (!article) return null;
    const svgs = [...article.querySelectorAll('.wg-diagram-svg svg')];
    const box = svgs[0]?.getBoundingClientRect();
    const host = article.getBoundingClientRect();
    return {
      live: article.dataset.live,
      name: article.querySelector('.lm-name')?.textContent || '',
      modelMark: Boolean(article.querySelector('.lm-model-mark')),
      svgs: svgs.length,
      fill: (svgs[0]?.innerHTML.match(/\.node rect[^}]*fill:([^;]+)/) || [])[1] || '',
      repairedNote: article.querySelectorAll('.wg-diagram-note').length,
      failed: [...article.querySelectorAll('.wg-diagram-failed')].map((f) => ({ summary: f.querySelector('.wg-diagram-error strong')?.textContent || '', caret: (f.querySelector('.wg-diagram-error pre')?.textContent || '').includes('^'), source: Boolean(f.querySelector('pre, code')) })),
      waits: [...article.querySelectorAll('.wg-diagram-wait')].map((w) => w.textContent),
      katex: article.querySelectorAll('.katex').length,
      katexDisplay: article.querySelectorAll('.katex-display').length,
      rawLatex: article.textContent.includes('\\(') || article.textContent.includes('\\['),
      price: article.textContent.includes('$5 a month'),
      charts: article.querySelectorAll('.wg-chart').length,
      tables: article.querySelectorAll('table').length,
      svgWidth: box ? Math.round(box.width) : 0,
      scrolls: Boolean(article.querySelector('.wg-diagram-svg.is-scroll')),
      hostWidth: Math.round(article.querySelector('.wg-diagram-svg')?.getBoundingClientRect().width || 0),
      labelPx: Math.round(parseFloat(getComputedStyle(svgs[0]?.querySelector('.nodeLabel, text') || document.body).fontSize) * ((box?.width || 1) / (svgs[0]?.viewBox?.baseVal?.width || box?.width || 1))),
      svgHeight: box ? Math.round(box.height) : 0,
      articleWidth: Math.round(host.width),
      pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  });
}

function assertFinished(state, label) {
  assert.ok(state, `${label}: no reply rendered`);
  assert.equal(state.svgs, 1, `${label}: the repairable diagram must draw ${JSON.stringify(state)}`);
  assert.equal(state.repairedNote, 1, `${label}: the repaired diagram says so`);
  assert.equal(state.failed.length, 1, `${label}: the broken diagram shows diagnostics`);
  assert.ok(state.failed[0].summary.startsWith('Parse error'), `${label}: parser message shown ${JSON.stringify(state.failed)}`);
  assert.ok(state.failed[0].caret && state.failed[0].source, `${label}: caret line and source shown`);
  assert.deepEqual(state.waits, [], `${label}: nothing left waiting`);
  assert.ok(state.katex >= 2 && state.katexDisplay === 1, `${label}: \\( \\) and \\[ \\] math rendered ${JSON.stringify(state)}`);
  assert.equal(state.rawLatex, false, `${label}: no raw LaTeX delimiters left`);
  assert.equal(state.price, true, `${label}: a $ price stays text`);
  assert.equal(state.charts, 1, `${label}: chart block drawn`);
  assert.equal(state.tables, 1, `${label}: table drawn`);
  assert.equal(state.modelMark, true, `${label}: Chat shows the model's mark, not an agent face`);
  // Fits its column, or (too wide to shrink legibly) scrolls sideways inside its own card.
  assert.ok(state.svgWidth > 0 && (state.scrolls ? state.hostWidth <= state.articleWidth : state.svgWidth <= state.articleWidth), `${label}: diagram fits or scrolls in its card ${JSON.stringify(state)}`);
  if (!state.scrolls) assert.ok(state.svgHeight <= 470, `${label}: diagram height is capped ${JSON.stringify(state)}`);
  assert.ok(state.labelPx >= 10, `${label}: diagram labels stay readable (${state.labelPx}px) ${JSON.stringify(state)}`);
  assert.ok(state.pageOverflow <= 1, `${label}: no horizontal page overflow ${JSON.stringify(state)}`);
}

server.listen(0, '127.0.0.1', async () => {
  const port = server.address().port;
  const browser = await chromium.launch({ headless: true, ...(process.env.ECHOSPEAK_CHROMIUM_PATH ? { executablePath: process.env.ECHOSPEAK_CHROMIUM_PATH } : {}) });
  const report = { fixture_data: true, real_models: false, real_mermaid: true, checks: [], states: {}, page_errors: [], unexpected_network: [] };
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    page = await context.newPage();
    page.on('pageerror', (error) => report.page_errors.push(error.message));
    let streamed = false;
    await page.addInitScript((thread) => {
      if (window.top !== window) return;
      if (!localStorage.getItem('echospeak.theme')) localStorage.setItem('echospeak.theme', 'dark');
      localStorage.setItem('echospeak.active_thread_id', thread);
      localStorage.setItem('echospeak.environment', 'chat');
    }, THREAD);
    await page.route('**/*', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.hostname === '127.0.0.1' && url.port === String(port)) return route.continue();
      if (url.hostname !== 'localhost' || url.port !== '8000') { report.unexpected_network.push(url.origin); return route.fulfill({ status: 200, body: '' }); }
      const p = url.pathname;
      if (request.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: { 'access-control-allow-origin': '*', 'access-control-allow-headers': '*', 'access-control-allow-methods': '*' } });
      if (p === '/query/stream') {
        streamed = true;
        const rid = String((request.postDataJSON() || {}).client_request_id || '');
        return route.continue({ url: `http://127.0.0.1:${port}/__stream?rid=${encodeURIComponent(rid)}` });
      }
      let data;
      if (p === '/threads') data = [{ thread_id: THREAD, title: 'Rendering check', last_active_at: 1793200000, created_at: 1793200000, message_count: streamed ? 2 : 0, project_id: '', surface: 'chat', agent_runtime: 'echo', environment: 'chat', source: 'web' }];
      else if (p === '/history') data = { history: [], count: streamed ? 2 : 0, session: { thread_id: THREAD, mode: 'chat' }, session_id: THREAD, turns: streamed ? [savedTurn] : [] };
      else if (/^\/threads\/[^/]+\/state$/.test(p)) data = { thread_id: THREAD, active_project_id: '', updated_at: 2, mode: 'chat', execution_status: 'complete' };
      else if (p === '/startup/readiness') data = { protocol_version: '1', runtime_schema_version: 1, core_ready: true, status: 'Ready', completed_steps: 1, total_steps: 1, data_root: 'fixture', active_project_id: '', active_session_id: THREAD, components: [], runtime_kind: 'web', instance_id: 'fixture' };
      else if (p === '/provider') data = { provider: 'lmstudio', model: 'fixture-model', ready: true, local: true, base_url: 'http://localhost:1234', context_window: 32768, available_providers: [{ id: 'lmstudio', name: 'LM Studio', local: true }], binding_revision: 1 };
      else if (p === '/provider/models') data = { provider: 'lmstudio', reachable: true, models: ['fixture-model'] };
      else if (p === '/onboarding') data = { show: false, step: 5 };
      else if (p === '/health/capabilities') data = { status: 'ok', checks: [], issues: [], ready: true };
      else if (p === '/lean/agents') data = { items: [{ id: 'echo', name: 'Echo', title: 'Personal agent', description: 'Native Echo', toolsets: [] }] };
      else if (p === '/lean/rooms') data = { items: [] };
      else if (p === '/lean/toolsets') data = { toolsets: [] };
      else if (p === '/query/queue') data = { queue: [], paused: false };
      else if (p === '/health') data = { status: 'healthy' };
      else if (p === '/projects') data = { items: [] };
      else data = { items: [], notifications: [], checks: [], profiles: [], settings: {}, paused: false, capabilities: [], turns: [] };
      return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' }, body: JSON.stringify(data) });
    });

    await page.goto(`http://127.0.0.1:${port}/app`);
    const box = page.getByRole('textbox', { name: 'Message', exact: true });
    await box.waitFor();
    await box.fill('Explain the request path');
    await box.press('Enter');

    // While streaming: once the first diagram's fence has closed, it draws without waiting for the reply.
    await page.waitForFunction(() => {
      const article = [...document.querySelectorAll('article.lm')].pop();
      return article && article.dataset.live === 'true' && article.querySelector('.wg-diagram-svg svg');
    }, null, { timeout: 20000 });
    const midStream = await lastReply(page);
    assert.equal(midStream.live, 'true');
    assert.equal(midStream.svgs, 1);
    report.states.mid_stream = midStream;
    report.checks.push('A diagram draws while the reply is still streaming, as soon as its fence closes');
    await page.screenshot({ path: path.join(out, 'chat-render-streaming-dark.png') });

    await page.waitForFunction(() => {
      const article = [...document.querySelectorAll('article.lm')].pop();
      return article && article.dataset.live !== 'true' && article.querySelector('.wg-diagram-failed') && !article.querySelector('.wg-diagram-wait');
    }, null, { timeout: 30000 });
    const finished = await lastReply(page);
    assertFinished(finished, 'after streaming');
    assert.equal(finished.fill.toLowerCase(), '#1d1d20', 'dark theme palette');
    report.states.after_stream = finished;
    report.checks.push('After streaming: repaired diagram drawn and labelled, broken diagram shows parser line, caret and source, \\( \\) and \\[ \\] math, chart, table, $ price kept as text');
    await page.locator('.wg-diagram').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, 'chat-render-done-dark.png') });

    for (const theme of ['light', 'dark']) {
      await page.evaluate((value) => localStorage.setItem('echospeak.theme', value), theme);
      await page.reload();
      await page.waitForFunction(() => {
        const article = [...document.querySelectorAll('article.lm')].pop();
        return article && article.querySelector('.wg-diagram-svg svg') && article.querySelector('.wg-diagram-failed');
      }, null, { timeout: 30000 });
      const reloaded = await lastReply(page);
      assertFinished(reloaded, `after reload (${theme})`);
      assert.equal(reloaded.fill.toLowerCase(), theme === 'light' ? '#f4f4f6' : '#1d1d20', `${theme} palette after reload`);
      report.states[`reload_${theme}`] = reloaded;
      await page.locator('.wg-diagram').first().scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(out, `chat-render-reload-${theme}.png`) });
    }
    report.checks.push('After reload from saved history: the same rendering, in light and dark palettes');

    // Switching theme live redraws diagrams in the new palette.
    await page.evaluate(() => { document.documentElement.dataset.esTheme = 'light'; });
    await page.waitForFunction(() => ((document.querySelector('.wg-diagram-svg svg')?.innerHTML || '').match(/\.node rect[^}]*fill:([^;]+)/) || [])[1]?.toLowerCase() === '#f4f4f6', null, { timeout: 10000 });
    report.checks.push('Changing the theme redraws diagrams in the new palette');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(400);
    const phone = await lastReply(page);
    assertFinished(phone, 'phone width');
    report.states.phone = phone;
    await page.locator('.wg-diagram').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, 'chat-render-phone-light.png') });
    assert.equal(phone.scrolls, true, 'a wide diagram on a phone scrolls at a readable size instead of shrinking');
    report.checks.push('Phone width: a wide diagram keeps readable labels and scrolls inside its card; the page never scrolls sideways');

    assert.deepEqual(report.page_errors, []);
    assert.deepEqual(report.unexpected_network, []);
  } catch (error) {
    report.error = String(error && error.stack || error);
    process.exitCode = 1;
    if (page) {
      report.failure_state = await lastReply(page).catch(() => null);
      await page.screenshot({ path: path.join(out, 'failure.png') }).catch(() => undefined);
    }
  } finally {
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ checks: report.checks, page_errors: report.page_errors, unexpected_network: report.unexpected_network, error: report.error || '' }));
    await browser.close();
    server.close();
  }
});
