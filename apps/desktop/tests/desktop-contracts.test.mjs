import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const config = JSON.parse(await readFile(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8"));
const capability = JSON.parse(await readFile(new URL("../src-tauri/capabilities/desktop-main.json", import.meta.url), "utf8"));
const rust = await readFile(new URL("../src-tauri/src/backend.rs", import.meta.url), "utf8");
const host = await readFile(new URL("../src-tauri/src/lib.rs", import.meta.url), "utf8");
const entry = await readFile(new URL("../backend/echospeak_backend.py", import.meta.url), "utf8");
const cargo = await readFile(new URL("../src-tauri/Cargo.toml", import.meta.url), "utf8");
const desktopApp = await readFile(new URL("../../web/src/desktop/DesktopApp.tsx", import.meta.url), "utf8");
const desktopCss = await readFile(new URL("../../web/src/desktop/desktop.css", import.meta.url), "utf8");
const dashboard = await readFile(new URL("../../web/src/index.tsx", import.meta.url), "utf8");
const composerInput = await readFile(new URL("../../web/src/dashboard/ComposerInput.tsx", import.meta.url), "utf8");
const sidebar = await readFile(new URL("../../web/src/components/ProjectSidebar.tsx", import.meta.url), "utf8");

test("desktop window is a bounded native shell over the shared frontend", () => {
  assert.equal(config.build.frontendDist, "../../web/dist");
  assert.equal(config.app.windows[0].decorations, false);
  assert.equal(config.app.windows[0].resizable, true);
  assert.ok(config.app.windows[0].minWidth >= 900);
  assert.ok(config.bundle.targets.includes("nsis"));
  assert.ok(config.bundle.targets.includes("msi"));
});

test("first-run setup opens in its own window, not over another one", () => {
  const setup = config.app.windows.find((window) => window.label === "setup");
  assert.ok(setup, "a setup window is declared");
  assert.equal(setup.visible, false);
  assert.equal(setup.decorations, false);
  assert.ok(host.includes("fn open_setup_window"));
  assert.ok(host.includes("open_setup_window,"), "the command is registered");
  assert.match(host, /"settings" \| "setup" \| "companion"\) => window\.hide\(\)/);
  assert.ok(desktopApp.includes('"setup"'), "the renderer knows the setup window");
});

test("windows follow the chosen theme instead of forcing dark", () => {
  for (const window of config.app.windows) assert.equal(window.theme, undefined, `${window.label} must not force a theme`);
});

test("renderer capability cannot spawn arbitrary shell commands", () => {
  assert.deepEqual(capability.windows, ["main", "settings", "setup", "companion"]);
  assert.ok(!capability.permissions.some((permission) => String(permission).startsWith("shell:")));
  assert.ok(config.app.security.csp.includes("http://127.0.0.1:*"));
  assert.ok(!config.app.security.csp.includes("http://0.0.0.0"));
});

test("custom chrome can drag while controls and composer remain interactive", () => {
  assert.ok(capability.permissions.includes("core:window:allow-start-dragging"));
  assert.ok(desktopApp.includes('className="desktop-titlebar" data-tauri-drag-region'));
  assert.ok(!desktopApp.includes('className="desktop-window-controls" data-tauri-drag-region'));
  const composer = composerInput.match(/<textarea\s+ref=\{textareaRef\}[\s\S]{0,4000}?aria-label="Message"/i)?.[0] || "";
  assert.ok(composer, "canonical composer textarea was not found");
  assert.ok(composer.includes("disabled={!activeThreadId}"), "composer must require an explicitly created Session");
  assert.ok(desktopCss.includes("pointer-events: auto"));
  assert.ok(desktopCss.includes("user-select: text"));
});

test("desktop composer submits only into an explicitly selected Session", () => {
  assert.ok(dashboard.includes("Session creation has one explicit owner: the + controls in the sidebar."));
  assert.ok(dashboard.includes('const streamThreadId = String(recovery?.session || activeThreadIdRef.current || activeThreadId || "").trim()'));
  assert.ok(dashboard.includes("if (!streamThreadId) return"));
  assert.ok(composerInput.includes('e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing'));
  assert.ok(composerInput.includes("void onSend()"));
  assert.ok(composerInput.includes("disabled={!activeThreadId || !input.trim()}"));
  assert.ok(composerInput.includes("disabled={!activeThreadId}"));
  assert.ok(dashboard.includes("onSend={() => void sendText()}"));
});

test("desktop startup and sidebar use one monochrome Echo identity", () => {
  // Echo's face spins on the boot screen (same face as the splash).
  assert.ok(desktopApp.includes('className="desktop-boot-face"'));
  assert.ok(desktopApp.includes("desktop-boot-progress"));
  assert.ok(!desktopApp.includes("desktop-boot-mark"));
  assert.ok(!desktopApp.includes("desktop-boot-orbit"));
  assert.ok(desktopCss.includes("@keyframes echo-face-spin"));
  assert.ok(desktopCss.includes("@keyframes desktop-progress"));
  assert.ok(sidebar.includes('className="sidebar-navigation"'));
  assert.ok(sidebar.includes('onClick={props.onToggleCollapsed}'));
  // Collapse sits beside New chat; the collapsed rail keeps a small expand button.
  assert.ok(sidebar.includes('className="es-side-collapse" onClick={props.onToggleCollapsed} aria-label="Collapse sidebar"'));
  assert.ok(sidebar.includes('aria-label="Expand sidebar"'));
  assert.ok(!sidebar.includes("<span>Navigation</span>"));
});

test("production startup cannot run the retired shortcut-repair shell", async () => {
  assert.doesNotMatch(host, /repair-shortcuts|ExecutionPolicy|powershell\.exe/i);
  assert.ok(!Object.keys(config.bundle.resources).some((resource) => /\.ps1$/i.test(resource)));
  const hooks = await readFile(new URL("../src-tauri/windows/hooks.nsh", import.meta.url), "utf8");
  assert.doesNotMatch(hooks, /nsExec|ExecWait|powershell|ExecutionPolicy/i);
});

test("each install starts from a clean backend folder, never the user's data", async () => {
  const hooks = await readFile(new URL("../src-tauri/windows/hooks.nsh", import.meta.url), "utf8");
  const preinstall = hooks.match(/!macro NSIS_HOOK_PREINSTALL([\s\S]*?)!macroend/)?.[1] || "";
  // Files left by an older version broke 11.1.0's backend (a stale backports/zstd module).
  assert.match(preinstall, /RMDir \/r "\$INSTDIR\\backend"/);
  assert.match(preinstall, /\$INSTDIR != ""/, "never runs with an empty install path");
  assert.equal(config.bundle.resources["backend-dist/"], "backend/", "the folder it clears is the bundled backend");
  assert.doesNotMatch(preinstall, /ai\.echospeak\.desktop|LOCALAPPDATA|APPDATA/, "user data is never touched");
});

test("desktop opens only after the authenticated product-readiness contract", () => {
  assert.ok(desktopApp.includes("readDesktopReadiness"));
  assert.ok(desktopApp.includes("startup_timeout"));
  assert.ok(desktopApp.includes("boot.hasBeenReady"));
  assert.ok(desktopApp.includes("completed_steps"));
});

test("desktop hydration is retryable and Session navigation preserves request ownership", () => {
  assert.ok(sidebar.includes("Restoring chats…"));
  assert.ok(dashboard.includes("for (let attempt = 0; attempt < 4"));
  assert.ok(dashboard.includes('localStorage.getItem("echospeak.active_thread_id")'));
  assert.ok(dashboard.includes("streamControllersRef.current.has(id)"));
  assert.ok(dashboard.includes("Navigation changes only the projection"));
  assert.ok(dashboard.includes("await loadHistory(session)"));
  assert.ok(dashboard.includes("activeThreadIdRef.current !== session"));
  assert.ok(!dashboard.includes("Abort synchronously BEFORE clearing UI"));
});

test("desktop sidecar transport is loopback-only and authenticated per launch", () => {
  for (const invariant of [
    '.env("API_HOST", "127.0.0.1")',
    '.env("API_AUTH_ENABLED", "true")',
    '.env("API_AUTH_LOCALHOST_BYPASS", "false")',
    '.env("ADMIN_API_KEY", &api_session_key)',
    '.env("API_TRUST_PROXY_HEADERS", "false")',
  ]) {
    assert.ok(rust.includes(invariant), `missing Rust invariant: ${invariant}`);
  }
  assert.ok(entry.includes('args.host != "127.0.0.1"'));
  assert.ok(entry.includes('API_AUTH_LOCALHOST_BYPASS'));
  assert.ok(entry.includes('ECHOSPEAK_DATA_DIR'));
  assert.ok(entry.includes('ECHOSPEAK_LOGS_DIR'));
  assert.ok(entry.includes('_seed_mutable_defaults'));
});

test("sidecar lifetime has both graceful and crash recovery ownership", () => {
  assert.ok(rust.includes("MAX_AUTOMATIC_RESTARTS"));
  assert.ok(rust.includes("terminate_process_tree"));
  assert.ok(rust.includes("request_backend_exit"));
  assert.ok(rust.includes("process_is_alive"));
  assert.ok(rust.includes("PROCESS_SYNCHRONIZE"));
  assert.ok(rust.includes("take_failed_generation"));
  assert.ok(entry.includes("desktop-parent-watchdog"));
  assert.ok(entry.includes("desktop-bootloader-watchdog"));
  assert.ok(entry.includes("WaitForSingleObject"));
});

test("native contract is reproducible and supports disposable acceptance data", () => {
  for (const version of [
    'tauri-build = { version = "=2.6.3"',
    'tauri = { version = "=2.11.5"',
    'tauri-plugin-shell = "=2.3.5"',
    'tauri-plugin-single-instance = "=2.4.2"',
    'tauri-plugin-window-state = "=2.4.1"',
  ]) {
    assert.ok(cargo.includes(version), `missing pinned native dependency: ${version}`);
  }
  assert.ok(host.includes('var_os("ECHOSPEAK_DESKTOP_DATA_DIR")'));
  assert.ok(host.includes('var_os("ECHOSPEAK_DESKTOP_LOG_DIR")'));
  assert.ok(host.includes("TargetKind::Folder"));
  assert.ok(rust.includes('.env("ECHOSPEAK_DATA_DIR", &data_dir)'));
  assert.ok(rust.includes('.env("ECHOSPEAK_LOGS_DIR", &log_dir)'));
});

test("index.html has no inline style or script blocks (they would break the desktop CSP)", async () => {
  // Tauri hashes inline <style>/<script> tags into the CSP at build time. A
  // hash in style-src makes the webview ignore 'unsafe-inline', which blocks
  // every <style> the app injects at runtime and leaves it unstyled.
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.doesNotMatch(html, /<style[\s>]/i);
  assert.doesNotMatch(html, /<script(?![^>]*\bsrc=)[^>]*>/i);
  assert.doesNotMatch(html, /\sstyle="/i);
  assert.match(config.app.security.csp, /style-src[^;]*'unsafe-inline'/);
});

test("in-app updates verify signed GitHub releases and stop the service before installing", async () => {
  const updates = await readFile(new URL("../src-tauri/src/updates.rs", import.meta.url), "utf8");
  const permissions = await readFile(new URL("../src-tauri/permissions/desktop.toml", import.meta.url), "utf8");
  const updater = config.plugins.updater;
  assert.equal(typeof updater.pubkey, "string");
  assert.deepEqual(updater.endpoints, ["https://github.com/Ty0x7/EchoSpeak/releases/latest/download/latest.json"]);
  assert.equal(updater.windows.installMode, "passive");
  // Ordinary builds must not need the private signing key; only the release script turns this on.
  assert.notEqual(config.bundle.createUpdaterArtifacts, true);
  assert.ok(cargo.includes('tauri-plugin-updater = "=2.12.0"'));
  assert.ok(host.includes("tauri_plugin_updater::Builder::new().build()"));
  assert.ok(host.includes("updates::check_for_update") && host.includes("updates::install_update"));
  assert.ok(permissions.includes('"check_for_update"') && permissions.includes('"install_update"'));
  // The installer replaces the Python service's files, so it must be stopped first.
  assert.ok(updates.indexOf("shutdown_backend") < updates.indexOf("update.install("));
  // No updater: permissions for the renderer; it goes through the two commands above.
  assert.ok(!capability.permissions.some((permission) => String(permission).startsWith("updater:")));
});
