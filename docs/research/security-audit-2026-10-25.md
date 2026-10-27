# Security audit and patching, October 2026 (11.10.3)

This audit's baseline was 11.10.3. The combined Echo Code/security source now targets **11.10.5**; see [release notes](../releases/v11.10.5.md). Historical package hashes and original test runs are retained as baseline evidence. The new signed build requires the owner's local signing-key password and is not yet claimed as built or published.

An authorized, defensive audit of EchoSpeak and the machine it's built and run on: every
dependency the installer ships, the build and release pipeline, the local service, and the
agent's own security boundaries. Confirmed problems were patched where that was safe to do
in the repo. Changes to the system or its other software are listed for the owner to approve.

**Method.**
- **Inventory:** 1,181 packages: the build Python, both npm lockfiles and `Cargo.lock`.
- **Vulnerability data:** checked against [OSV](https://osv.dev), which aggregates GitHub Security Advisories, PyPA, RustSec, npm and malicious-package reports, then against vendor advisories (Tauri's GitHub advisories, python.org, Git for Windows, Docker, Microsoft) and CISA KEV.
- **What actually ships:** read from PyInstaller's module table of the last build. Whether a crate compiles for Windows was checked with `cargo tree --target x86_64-pc-windows-msvc`.
- **Code review:** of the agent's attack surface, with attention to what changed in 11.10.x.
- **Verification:** each claimed fix was tested; nothing is reported fixed without a check.

## 1. Architecture and trust boundaries

```
 web pages, email, MCP servers, screenshots, repos  ──(untrusted text: data, never instructions)──┐
                                                                                                   ▼
 ┌─ Tauri desktop shell (Rust, WebView2) ─────────────┐   per-launch key    ┌─ Python backend (127.0.0.1 only) ─┐
 │ renderer: CSP script-src 'self'; artifact & MCP App │ ─X-EchoSpeak-Key──▶ │ auth + DNS-rebinding + Origin guard │
 │ frames sandboxed (opaque origin); updater: minisign │                     │ policy: Rule of Two, approvals,     │
 │ + requireSignedVersion                              │                     │ Blocked apps, secrets check, audit  │
 └─────────────────────────────────────────────────────┘                     └───────┬──────────────┬──────────────┘
                                                                                     ▼              ▼
                                             child processes (terminal, MCP, dev servers):   Docker sandbox (Linux)
                                             no EchoSpeak secrets, git bundle URIs off,      or this PC (asks first
                                             npx install scripts off                         when the sandbox is down)
```

The same diagram, drawn with each boundary, is in the visual report
([security-audit-2026-10-25.html](../../artifacts/research/security-audit-2026-10-25.html)).

## 2. Confirmed vulnerabilities, patched

### Desktop shell (Rust / Tauri)

| Package | Was | Now | Advisory | Exposure in EchoSpeak |
|---|---|---|---|---|
| `tauri` | 2.11.5 | **2.11.6** | [GHSA-w28w-mhc8-qvjv](https://github.com/tauri-apps/tauri/security/advisories/GHSA-w28w-mhc8-qvjv) (high): the `__TAURI_CHANNEL__` fetch command bypassed the ACL, so a WebView could drain another's IPC responses | 2.11.5 was the last affected version, and EchoSpeak has four windows |
| `tauri-plugin-updater` config | downgrade possible | **`requireSignedVersion: true`** | CVE-2026-95625: the update manifest isn't signed, so an older, validly signed release could be served as "newer" | Turned on the 2.12.0 protection (details below) |
| `@tauri-apps/cli` | 2.11.4 | **2.12.4** | 2.12.0 is the first CLI to write the version into updater signatures | Required for `requireSignedVersion` |
| `rustls` | 0.23.45 | 0.23.46 | RUSTSEC-2026-0347/0348/0349: ECH data leak, key-exchange downgrade, 1 GiB pre-auth allocation | The updater's HTTPS client |
| `rustls-webpki` | 0.103.15 | 0.103.16 | RUSTSEC-2026-0382: distrusted (emSign) intermediates accepted | The updater's HTTPS client |
| `tokio` | 1.52.3 | 1.54.1 | AsyncFd I/O safety (Unix only) | Semver-compatible hygiene update |
| `webpki-root-certs`, `event-listener` | 1.0.9, 5.4.1 | 1.0.11, 5.4.2 | emSign roots; `!Send` soundness | Not compiled for Windows; lockfile hygiene |

**Updater downgrade protection.** Releases before this were signed with only a timestamp and a
file name in the trusted comment (checked on the 11.10.3 build), so the app couldn't tell which
version a signature belonged to. Three changes close it:
- **CLI 2.12.4** adds `version:<x>` to every bundle signature.
- **The app** sets `requireSignedVersion`, so it rejects a manifest whose version doesn't match the signed one.
- **`verify-updater-signature.py --version`** makes `release-windows.ps1` refuse to publish a release whose signature lacks its version or carries another one.

This was checked end to end with a throwaway key: signed with a version, accepted; without one, refused; wrong version, refused.

### Backend the installer ships (Python)

The installer bundles whatever the build Python has installed. That was your shared global
Python 3.12.10, so 11.10.3 as built shipped these versions:

| Package | Shipped | Now | Main advisories |
|---|---|---|---|
| Pillow | 12.2.0 | **13.0.0** | 67 advisories, critical: decoder memory disclosure and crashes (TIFF, EPS, JPEG2000, PDF) on crafted images |
| PyJWT | 2.13.0 | **2.15.1** | 29, critical: key confusion (JWK as HMAC secret), BOM bypass, claim checks (CVE-2026-101917 and others) |
| brotli | 1.2.0 | 1.2.1 | Critical decoder out-of-bounds write (CVE-2026-111497) |
| certifi | 2026.5.20 | 2026.10.23 | Critical: bundled emSign roots whose intermediate CA keys were compromised |
| pypdf | 6.13.2 | **6.21.2** | 48, high: crafted-PDF infinite loops and memory exhaustion (document uploads, PDF reading) |
| cryptography | 49.0.0 | 50.0.2 | PKCS#7 decryption oracle (CVE-2026-69247) |
| aiohttp | 3.14.1 | 3.14.5 | 14, high: request smuggling, cookie scope, pool exhaustion (via discord.py) |
| anyio, h2, hpack, multidict, filelock, fsspec, yarl | various | patched | TLS hostname encoding, request smuggling, decoder DoS, Windows lock races, template injection |
| setuptools | 81.0.0 | 84.2.1 | Bundled `pkg_resources`; legacy-install path escape |
| httplib2, oauthlib, pyasn1 | 0.31.2, 3.3.1, 0.6.3 | 0.32.0, 4.0.0, 0.6.4 | Google integrations: decompression bomb, PKCE timing, ASN.1 DoS |

**The structural fix is a hermetic, hash-checked build.**
- **Inputs:** `apps/backend/requirements-build.txt` is the app, pytest, PyInstaller 6.21.0 and pip 26.2.2 (pip has its own advisory, CVE-2026-13346).
- **Lock:** `requirements.lock.txt` pins all 157 packages with SHA-256 hashes. It was generated preferring the versions already in use, so only the packages with fixes moved.
- **Build environment:** `apps/backend/.venv` is created from the lock with `pip install --require-hashes`, and the build scripts already pick it first.
- **Gate:** `check-build-env.py`, run by `python-env.ps1`, refuses to build from any Python whose packages differ from the lock. `ECHOSPEAK_ALLOW_UNLOCKED_BUILD=1` downgrades the refusal to a warning.
- **Minimum versions:** `requirements.txt` now has a security floor for every package above, with its CVE.

A side effect: `py7zr`, which the local image-generation setup needs, was missing from the shared Python, so it had never shipped. The locked environment includes it.

### Web app (npm)

| Package | Was | Now | Advisory | Exposure |
|---|---|---|---|---|
| dompurify | 3.4.16 | **3.4.17** | GHSA-cg2g-xh64-v4q7 (critical), GHSA-mrv2-3pqr-j5jr (high): sanitizer bypasses | **Shipped**: Mermaid uses it (strict mode) to sanitize diagram SVG, and diagrams can come from model output after prompt injection. The CSP (`script-src 'self'`) blocked inline script anyway. |
| vite | 6.4.3 | 6.4.5 | 5 advisories: launch-editor command execution and `fs.allow` bypass | Dev server only |
| vitest / @vitest/mocker | 4.1.11 | 4.1.12 | Code injection via mock registration | Tests only |
| source-map-js | 1.2.1 | 1.2.2 | Event-loop DoS | Build only |

No `MAL-` (known-malicious) versions from the 2026 npm worm campaigns (Mini Shai-Hulud,
ChainDrop/keyv) are in either lockfile.

### CPython 3.12.10: HTML parser denial of service (mitigated in code)

`html.parser` is quadratic on broken tag starts (CVE-2025-6069, CVE-2026-15308). It's fixed in
CPython 3.12.14, but **3.12.10 is the last 3.12 release with Windows installers**.

This was measured on the build Python. A page of repeated `<a` took 8.8 s at 80 KB and roughly
quadrupled per doubling, so the 2 MB pages Echo's reader accepts would hang a worker for over an
hour. Any web page Echo reads could do this.

`safe_web_retrieval.defuse_html` now escapes each `<` that has no `>` before the next `<`. Those
are exactly the constructs that blow up. The pass is linear, and real pages parse identically
(the tests compare text, links, title and tables). 800 KB attack inputs now parse in well under a
second. Fully fixing this and the CPython `tarfile`/`expat` advisories needs a newer interpreter
(section 5).

### Git for Windows: NTLM hash leak (mitigated in code)

CVE-2026-62960 (CVSS 7.4) affects Git for Windows before 2.55.0.windows.4; this machine has
2.55.0.windows.2. A hostile server can use bundle URIs during clone or fetch to point Git at a
network share, which sends your Windows NTLM hash to it. Agents clone repositories from
strangers. `child_env` now passes `transfer.bundleURI=false` to every process EchoSpeak starts,
using `GIT_CONFIG_*` and appending after any the user sets. It never touches your git config,
and a test confirms git itself reads it. Updating Git is still recommended (section 5).

## 3. Hardening (no CVE, closes a real path)

- **Build supply chain:** the hermetic, hash-locked environment described above.
- **CI** (`.github/workflows/website.yml`):
  - Actions are pinned to commit SHAs, so a moved tag (the tj-actions attack) can't swap the code.
  - Pages write access is limited to the deploy job, and checkout doesn't keep its token.
  - `npm ci --ignore-scripts` blocks install-script worms. The site was rebuilt locally this way to confirm it still builds.
- **MCP servers started with `npx`** run with `npm_config_ignore_scripts=true`. The 2026 npm worms spread through install scripts of hijacked dependencies. Servers already launch at an exact version, and one that really needs install scripts can set the variable back in its own environment.
- **Project `AGENTS.md` / `CLAUDE.md`** (new in 11.10.3):
  - A cloned repo's file is labelled as the project author's and wrapped in `<project-instructions>`; any closing tag inside it is escaped.
  - Its conventions (build, test, style) are followed. Anything else it asks for is treated as a suggestion to check with you first. In 2026, repo and agent config files were an active injection channel (ChainDrop planted Claude Code config into repositories).
  - Your own `~/.echospeak/AGENTS.md` is still followed like your requests.
- **`propose_skill`** after reading outside content is now a Rule of Two ask, like memory writes: an approved skill is read in every future chat.
- **`apps/backend/scripts/security_audit.py`** re-runs this OSV check on the three lockfiles at any time. It exits 1 when anything is found, so it can gate a release.

## 4. Checked and not affected, or already mitigated

| Item | Why it doesn't apply |
|---|---|
| MCP Python SDK CVE-2026-109527, -52869, -52870, -59950 | Server-side and session bugs; EchoSpeak is a client on `mcp` 2.3.0, which is past the fixed lines |
| Tauri GHSA-7gmj-67g7-phm9 (origin confusion) | Fixed in 2.11.1 |
| tauri-plugin-shell CVE-2025-31477 | Fixed in 2.2.1; EchoSpeak has 2.3.5 |
| tauri-plugin-fs GHSA-9g54-6x48-9vpw | Plugin not used |
| `zip` 4.6.1 (RUSTSEC-2026-0367/8/9) | Unix permission and symlink bugs in extraction. On Windows the updater runs the signed NSIS installer, and signatures are checked before anything else. Fixing it needs an updater major version |
| glib, quick-xml 0.39, rkyv, zbus, zvariant | Linux-only (Wayland/GTK); not in the Windows build |
| `unic-*` (via `urlpattern`), `proc-macro-error` | Unmaintained, with no fix available and no known vulnerability; build-time or low risk |
| LangChain, LangGraph, langsmith, torch, sentence-transformers, diskcache | In the shared global Python only. Never shipped (EchoSpeak dropped LangChain in 11.8), and absent from the locked build |
| Docker Desktop CVE-2026-106581 and earlier | 4.93.0 is past every fixed version found; the daemon isn't exposed on TCP 2375 |
| Windows CVE-2026-96951 (MotW bypass, actively exploited, CISA KEV 10/13) | October cumulative updates installed 10/14 |
| Local API | Loopback only, with the per-launch key always required in the desktop app, DNS-rebinding Host check and Origin check on writes. Artifact and MCP App frames are sandboxed without `allow-same-origin` |
| Secrets | None in tracked files or the full git history (test fixtures only); `.env` files are examples |
| Repository agent config | Only the project-documentation skill, with no hooks; recent history is the owner's own commits |
| Machine | Defender real-time and tamper protection on, signatures current; firewall on for all profiles; LM Studio bound to 127.0.0.1 |

## 5. Needs your approval (not changed)

1. **Update Git for Windows** to 2.55.0.windows.4 or newer (`winget upgrade --id Git.Git`). This fixes CVE-2026-62960 for everything on the PC, not just EchoSpeak.
2. **Build with Python 3.13.17** (Windows installers exist; 3.12 has none past 3.12.10). This fixes the CPython html.parser, tarfile and expat advisories inside the installer. It needs a Python install, a fresh `.venv` and lock, and a full test run.
3. **LM Studio:** turn off `logSensitiveData` (Developer › Server settings). It writes every prompt Echo sends, including screenshots' descriptions and tool results, to LM Studio's log files.
4. **The shared global Python** (used by other projects) has vulnerable LangChain, LangGraph, langsmith, torch, oauthlib and pip versions. Upgrade or remove them if those projects allow; EchoSpeak no longer builds from it.
5. **GitHub:** turn on Dependabot alerts and secret scanning with push protection (repository settings). The `gh` login token has the broad `repo` scope; the 2026 worms stole such tokens. Consider a fine-grained token for scripts.
6. **Windows publisher code signing** is still not configured (SmartScreen warnings remain). It needs a certificate.
7. **Optional:** move the Tauri stack to 2.12.x, which brings newer notification and updater plugins (and later `zip` 9), and move GitHub Actions to their newest major versions.

## 6. Remaining risks

- **Prompt injection is not solved anywhere.** EchoSpeak's controls limit what injected text can do: spotlighting, a quarantined reader, Rule of Two, approvals, Blocked apps, and secrets never in tool arguments. In Autonomous mode, ordinary commands on the sandbox run without asking by design, and the never-grantable classes still ask.
- **This PC terminal without the sandbox** runs with your user rights. Approvals apply, but a command you approve can do what you can.
- **Other html.parser edge cases** in CPython 3.12.10 beyond broken tag starts are only fixed by the interpreter upgrade.

## Files changed

- **Desktop:** `apps/desktop/src-tauri/Cargo.toml`, `Cargo.lock`, `tauri.conf.json`, `apps/desktop/package.json` and its lock.
- **Desktop scripts:** `release-windows.ps1`, `verify-updater-signature.py`, `python-env.ps1`, `check-build-env.py` (new).
- **Backend dependencies:** `requirements.txt`, `requirements-build.txt` (new), `requirements.lock.txt` (new).
- **Backend code:** `agent/child_env.py`, `agent/mcp_client.py`, `agent/home.py`, `agent/safe_web_retrieval.py`, `agent/lean/policy.py`, `scripts/security_audit.py` (new), `tests/test_security_audit_1025.py` (new).
- **Web:** `apps/web/package.json` and its lock.
- **CI:** `.github/workflows/website.yml`.

## Combined PR verification (2026-10-25)

The owner authorized including this existing audit alongside Echo Code and the reference-directed UI changes. The following checks were run against the combined checkout; the original audit's broader machine observations and full-suite results are retained separately below.

| Check | Fresh result |
|---|---|
| Backend: security audit, home/instruction parity, Echo Code, project workspace, privacy, runtime ownership, security policy/controls/hardening | **168 passed, 1 skipped** (Windows symlink capability) |
| Web: `npm --prefix apps/web run check` | TypeScript and **180 tests across 37 files passed** |
| Desktop: `npm --prefix apps/desktop test` | **16 passed**; stale composer/Tauri assertions updated, signed-version setting now asserted |
| Rust: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml --lib --locked` | **4 passed** |
| Website: `npm --prefix apps/web run build:site` | Passed; all five pinned Actions commits also resolve in their official repositories |
| Locked Python build environment | Matches `requirements.lock.txt`; PyInstaller **6.21.0** |
| Fresh OSV scan of Python, npm and Cargo locks | **1,130 locked packages, 0 new advisories, 17 reviewed findings**. Reviewed means the documented exposure assessment applies, not that the dependency is advisory-free. |
| Fresh packaged backend | Import self-check passed; core-ready with empty-profile model/embedding/retrieval degradation. Authenticated Echo Code routes, native session persistence/idempotency, missing-key 401, bad write-Origin 403 and external-runtime 409 verified in isolated state; no model calls. |
| Full Windows build | Fresh NSIS EXE **162.92 MiB** and MSI **236.34 MiB** built successfully. Hashes recorded; NSIS publisher signature is **NotSigned**. Neither installer was installed or published. |

The build and packaged smoke results are recorded with the local testing instructions in [Echo Code](echo-code.md#local-windows-testing-and-combined-pr). No live provider evaluation, desktop installation, machine software upgrade, PR merge or GitHub release publication is implied by these checks.

## Original audit verification (historical)

The following results were supplied with the pre-existing security audit. They describe its earlier audit run, rather than the fresh combined checks above.

| Check | Result |
|---|---|
| Backend suite in the locked `.venv` | 1295 passed, 0 failed, 22 skipped (onnx and LangChain test extras, Windows developer mode) |
| New security tests | `test_security_audit_1025.py`, 15 tests |
| Web: `npm run check` (typecheck and 175 tests) on vite 6.4.5 and vitest 4.1.12 | Passes; the site also built with `--ignore-scripts` |
| Desktop shell on tauri 2.11.6 | `cargo test --lib`: 4 passed |
| Updater gate (throwaway key) | With version, accepted; without, refused; wrong version, refused |
| HTML parser | 800 KB attack inputs parse in under 5 s (the test bound) |
| Re-scan with `security_audit.py` | Python and npm: 0 advisories; Rust: only the not-affected items in section 4 |
| Backend bundle (PyInstaller, from the locked `.venv`) | Builds. The packaged exe was started like the desktop shell starts it: `/health` OK and `/startup/readiness` core-ready (model and embeddings degraded only because the test data folder was empty). Both bundled skills are listed, a request without the session key gets 401, and `~/.echospeak` is created |
| Live installer test | Owner testing |

## Sources

- [OSV](https://osv.dev) and [GitHub Advisory Database](https://github.com/advisories)
- [Tauri security advisories](https://github.com/tauri-apps/tauri/security/advisories) and [plugins-workspace advisories](https://github.com/tauri-apps/plugins-workspace/security/advisories)
- [Tauri CLI changelog (2.12.0, version-bound signatures)](https://github.com/tauri-apps/tauri/blob/dev/packages/cli/CHANGELOG.md)
- [Python downloads for Windows](https://www.python.org/downloads/windows/) and [Python 3.12 changelog](https://docs.python.org/3.12/whatsnew/changelog.html)
- [CVE-2026-62960 (Git for Windows bundle URI)](https://cve.threatint.com/CVE/CVE-2026-62960)
- [CISA adds known exploited vulnerabilities, 13 Oct 2026](https://www.cisa.gov/news-events/alerts/2026/10/13/cisa-adds-two-known-exploited-vulnerabilities-catalog) and [Microsoft October 2026 Patch Tuesday](https://www.bleepingcomputer.com/news/microsoft/microsoft-october-2026-patch-tuesday-fixes-1-411-flaws-3-zero-days/)
- [Docker Desktop CVE-2026-106581](https://borncity.com/news/cve-2026-106581-docker-desktop-fuer-windows-braucht-update-auf-4-92-0/)
- [Mini Shai-Hulud (Tenable)](https://www.tenable.com/blog/mini-shai-hulud-frequently-asked-questions), [ChainDrop (Microsoft)](https://www.microsoft.com/en-us/security/blog/2026/08/04/chaindrop-supply-chain-compromise-anatomy-self-propagating-worm/)
- [MCP SDK CVE-2026-109527](https://www.mend.io/vulnerability-database/CVE-2026-109527/), [CVE-2026-52869](https://cve.cert.hr/cve/CVE-2026-52869)
- [MCP tool poisoning (CSA)](https://labs.cloudsecurityalliance.org/research/csa-research-note-mcp-tool-poisoning-ai-agent-exfiltration-2/), [AI coding agent attack surface (CSA)](https://labs.cloudsecurityalliance.org/wp-content/uploads/2026/06/ai-coding-agent-attack-surface-mcp-repository-poisoning-v1.0-csa-styled.pdf)
