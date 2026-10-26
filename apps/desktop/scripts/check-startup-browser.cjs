// Production-asset startup regression. Supply a Playwright module/browser already
// installed by the test environment; this script does not install dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.ECHOSPEAK_PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../../..');
const dist = path.join(root, 'apps/web/dist');
const server = http.createServer((request, response) => {
  const relative = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  const file = path.resolve(dist, '.' + (relative === '/app' || relative === '/' ? '/index.html' : relative));
  if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    response.writeHead(404); return response.end();
  }
  const type = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.png': 'image/png' }[path.extname(file)];
  response.setHeader('Content-Type', type || 'application/octet-stream');
  response.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true,
    ...(process.env.ECHOSPEAK_CHROMIUM_PATH ? { executablePath: process.env.ECHOSPEAK_CHROMIUM_PATH } : {}) });
  try {
    for (const missingBundle of [false, true]) {
      const context = await browser.newContext({ viewport: { width: 1100, height: 760 } });
      await context.addInitScript(() => {
        // Exactly the protection injected by Tauri's freeze_prototype.js.
        Object.freeze(Object.prototype);
        window.__startupCommands = [];
        window.__TAURI__ = { core: { invoke: async command => {
          window.__startupCommands.push(command);
          if (command === 'desktop_window_label') return 'main';
          if (command === 'desktop_runtime') return { environment: 'desktop', api_base: 'http://127.0.0.1:1',
            api_session_key: 'fixture-only', backend_phase: 'failed', backend_detail: 'Fixture backend unavailable',
            instance_id: 'fixture', connection_generation: 1, restart_count: 0, max_automatic_restarts: 3,
            data_dir: 'Fixture', log_dir: 'Fixture' };
          if (command === 'check_for_update') return { configured: false, available: false };
          return null;
        } }, event: { listen: async () => () => {}, emit: async () => {} } };
      });
      const page = await context.newPage();
      const errors = [], network = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== `http://127.0.0.1:${server.address().port}`) {
          network.push(url.origin); return route.abort();
        }
        if (missingBundle && /\/DesktopApp-[^/]+\.js$/.test(url.pathname)) return route.abort();
        return route.continue();
      });
      await page.goto(`http://127.0.0.1:${server.address().port}/app`);
      if (missingBundle) {
        await page.getByRole('heading', { name: 'EchoSpeak couldn’t open' }).waitFor();
        await page.getByRole('button', { name: 'Retry', exact: true }).waitFor();
        await page.getByRole('button', { name: 'Open logs', exact: true }).waitFor();
      } else {
        await page.locator('.desktop-window').waitFor();
        assert.deepEqual(errors, []);
      }
      await page.waitForFunction(() => window.__startupCommands.includes('control_desktop_window'));
      assert.equal(await page.evaluate(() => Object.isFrozen(Object.prototype)), true);
      assert.deepEqual(network, []);
      if (process.env.ECHOSPEAK_STARTUP_ARTIFACT_DIR) {
        fs.mkdirSync(process.env.ECHOSPEAK_STARTUP_ARTIFACT_DIR, { recursive: true });
        await page.screenshot({ path: path.join(process.env.ECHOSPEAK_STARTUP_ARTIFACT_DIR,
          missingBundle ? 'startup-error-fixture.png' : 'startup-shell-fixture.png') });
      }
      await context.close();
    }
    console.log(JSON.stringify({ frozen_prototype_startup: 'passed', missing_bundle_recovery: 'passed',
      window_reveal_requested_in_both_cases: true, real_accounts_or_installed_app: false }));
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
