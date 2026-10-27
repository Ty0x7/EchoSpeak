"""Render the static research view and Markdown catalog, without network/model IO."""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/research/model-ecosystem-catalog.json"
MARKDOWN = ROOT / "docs/research/model-ecosystem.md"
OUTPUT = ROOT / "artifacts/research/model-ecosystem.html"


def render():
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    priorities = json.loads((ROOT / "docs/research/model-ecosystem-priorities.json").read_text(encoding="utf-8"))
    rows = data["families"]
    esc = lambda value: html.escape(str(value), quote=True)
    md_escape = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
    header = "\n| Family / model inventory | Capabilities / limits | Deployment / Echo condition |\n| --- | --- | --- |\n"
    table = header + "\n".join(
        f"| **{md_escape(r['family'])}** — {md_escape(r['models'])} | {md_escape(r['capabilities'])} {md_escape(r['limits'])} | {md_escape(r['deployment'])}. **{md_escape(r['echo'])}**: {md_escape(r['condition'])} [Primary source]({r['source']}) |"
        for r in rows
    ) + "\n"
    text = MARKDOWN.read_text(encoding="utf-8")
    start, end = "<!-- CATALOG:START -->", "<!-- CATALOG:END -->"
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    text = before + start + table + end + after
    priority_md = "\n### Ten local priorities\n\n| Model / exact ID / release | Protocol and deployment | Download / memory / workload |\n| --- | --- | --- |\n"
    for row in priorities["local"]:
        file_link = f" [File metadata]({row['download_source']})" if row.get("download_source") else ""
        priority_md += f"| **{md_escape(row['name'])}** — `{row['id']}`. {md_escape(row['release'])}. [Source]({row['source']}) | {md_escape(row['behavior'])} Context: {md_escape(row['context'])}. {md_escape(row['runtimes'])} **Echo:** {md_escape(row['echo'])} | {md_escape(row['download'])}{file_link} {md_escape(row['hardware'])} Workload: {md_escape(row['workload'])} |\n"
    priority_md += "\n### Ten cloud priorities\n\n| Model / exact identifier | Official availability / contract | Echo / intended workload |\n| --- | --- | --- |\n"
    for row in priorities["cloud"]:
        priority_md += f"| **{md_escape(row['name'])}** — `{row['id']}`. [Source]({row['source']}) | {md_escape(row['availability'])}. {md_escape(row['limits'])}. {md_escape(row['behavior'])} | {md_escape(row['echo'])}. {md_escape(row['use'])}. |\n"
    start, end = "<!-- PRIORITIES:START -->", "<!-- PRIORITIES:END -->"
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    MARKDOWN.write_text(before + start + priority_md + end + after, encoding="utf-8")
    priority_html = '<section id="priorities"><h2>Every requested model, individually checked</h2><p class="muted">Ten local and ten cloud priorities. Official releases, exact IDs and usable Echo contracts are separate evidence. No live-model certification.</p><h3>Local deployment &amp; sizing</h3><div class="matrix" tabindex="0" aria-label="Local model priorities"><table><thead><tr><th>Model / exact ID</th><th>Download &amp; allocation</th><th>Protocol / Echo status</th></tr></thead><tbody>'
    for row in priorities["local"]:
        priority_html += f'<tr><th scope="row">{esc(row["name"])}<small>{esc(row["id"])}</small></th><td>{esc(row["download"])}<small>{esc(row["hardware"])}</small></td><td>{esc(row["echo"])}<details><summary>Behavior, context &amp; source</summary><p>{esc(row["behavior"])} Context: {esc(row["context"])}.</p><p>{esc(row["runtimes"])}</p><a href="{esc(row["source"])}">Official reference</a></details></td></tr>'
    priority_html += '</tbody></table></div><p><small>File sizes are decimal GB, excluding KV cache and other runtime allocations. GPT-OSS figures are analytical floors; other samples use public file-size metadata. Hardware tiers are estimates, not measured fit.</small></p><h3>Cloud IDs &amp; actual access</h3><div class="matrix" tabindex="0" aria-label="Cloud model priorities"><table><thead><tr><th>Model / exact ID</th><th>Official contract</th><th>Echo status</th></tr></thead><tbody>'
    for row in priorities["cloud"]:
        priority_html += f'<tr><th scope="row">{esc(row["name"])}<small>{esc(row["id"])}</small></th><td>{esc(row["limits"])}<small>{esc(row["behavior"])}</small></td><td>{esc(row["echo"])}<details><summary>Availability &amp; source</summary><p>{esc(row["availability"])}</p><a href="{esc(row["source"])}">Official reference</a></details></td></tr>'
    priority_html += '</tbody></table></div><div class="boundary"><b>Access is product-specific:</b> Kimi general API uses kimi-k3.1-pro; its Coding Plan uses k3.1-pro. DeepSeek Flash 1017 uses deepseek-flash. Consumer subscriptions and direct API accounts are separate.</div></section>'
    cards = "\n".join(
        f'<tr><th scope="row">{esc(r["family"])}<small>{esc(r["models"])}</small></th>'
        f'<td>{esc(r["capabilities"])}<small>{esc(r["limits"])}</small></td>'
        f'<td><span class="status {"unsupported" if r["echo"].startswith("Unsupported") else "conditional" if r["echo"] == "Conditional" else "unverified"}">{esc(r["echo"])}</span>'
        f'<p>{esc(r["condition"])}</p><details><summary>Deployment &amp; source</summary><p>{esc(r["deployment"])}</p>'
        f'<a href="{esc(r["source"])}">Official reference</a></details></td></tr>'
        for r in rows
    )
    source = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>EchoSpeak — model ecosystem and harness compatibility</title><style>
:root{color-scheme:dark;--bg:#0a0a0b;--panel:#111112;--soft:#18181b;--line:#29292e;--ink:#e8e8ec;--muted:#a5a5af;--accent:#8da8ff;--good:#8acda5;--warn:#e2be84}
@media(prefers-color-scheme:light){:root{color-scheme:light;--bg:#fafafa;--panel:#fff;--soft:#f0f0f4;--line:#dcdce2;--ink:#25252c;--muted:#62626f;--accent:#3556b5;--good:#22623c;--warn:#795416}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 "Segoe UI Variable","Segoe UI",system-ui,sans-serif}main{max-width:1180px;margin:auto;padding:22px clamp(16px,4vw,42px) 48px}header{border-bottom:1px solid var(--line);padding-bottom:16px}h1{font-size:20px;letter-spacing:-.02em;margin:0;font-weight:550}h2{font-size:18px;font-weight:550;letter-spacing:-.02em;margin:0 0 14px}h3{font-size:14px;font-weight:600;margin:0 0 6px}p{margin:6px 0 14px}small,.muted{color:var(--muted)}header p{font-size:12px;margin:5px 0 0}a{color:var(--accent);text-underline-offset:3px}nav{display:flex;gap:18px;flex-wrap:wrap;margin:16px 0 24px;font-size:12px}section{margin:34px 0;scroll-margin-top:16px}.lead{font-size:21px;line-height:1.45;max-width:760px;letter-spacing:-.02em}.note{border-left:2px solid var(--accent);padding:10px 15px;background:var(--panel);color:var(--muted);font-size:13px}.note strong{color:var(--ink)}.two,.three{display:grid;gap:16px}.two{grid-template-columns:repeat(2,minmax(0,1fr))}.three{grid-template-columns:repeat(3,minmax(0,1fr))}.card{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:17px}.card p{color:var(--muted);margin-bottom:0;font-size:13px}.number{display:block;color:var(--accent);font-size:11px;letter-spacing:.1em;margin-bottom:6px}.flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;counter-reset:flow}.flow .card:before{counter-increment:flow;content:counter(flow,decimal-leading-zero);display:block;color:var(--accent);font-size:11px;margin-bottom:6px}.boundary{margin:12px 0;padding:13px 16px;background:var(--soft);border-radius:8px;font-size:13px;color:var(--muted)}.boundary b{color:var(--ink)}.matrix{border:1px solid var(--line);border-radius:12px;overflow:auto}.matrix:focus-visible{outline:2px solid var(--accent)}table{border-collapse:collapse;width:100%;min-width:850px;font-size:13px}th,td{text-align:left;vertical-align:top;padding:15px 17px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}thead{background:var(--soft)}thead th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}tbody th{font-weight:550;width:29%}td:nth-child(2){width:38%}tbody tr:last-child>*{border-bottom:0}td p{margin:10px 0}small{display:block;font-size:12px;line-height:1.6;margin-top:6px;font-weight:400}.status{font-size:11px;padding:3px 8px;border:1px solid var(--line);border-radius:6px;display:inline-block;white-space:normal}.conditional{color:var(--accent)}.unsupported{color:var(--warn)}.unverified{color:var(--muted)}.confirmed{color:var(--good)}details{font-size:12px;color:var(--muted)}summary{cursor:pointer;color:var(--accent)}.legend{display:flex;gap:9px;flex-wrap:wrap;margin:12px 0}ul,ol{padding-left:21px}li{margin:8px 0}code{font-family:Consolas,monospace;font-size:12px;background:var(--soft);padding:2px 4px;border-radius:4px;overflow-wrap:anywhere}pre{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;overflow:auto}pre code{background:none;padding:0;white-space:pre}footer{font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:16px} @media(max-width:720px){.two,.three,.flow{grid-template-columns:1fr}.lead{font-size:19px}main{padding-top:16px}nav{gap:12px}section{margin:27px 0}h1{font-size:18px}}
</style></head><body><main>
<header><h1>EchoSpeak / Model ecosystem &amp; harness compatibility</h1><p>Research snapshot · __DATE__ UTC · Native harness audit · No live inference</p></header>
<nav aria-label="Report sections"><a href="#changes">What changed</a><a href="#priorities">20 exact priorities</a><a href="#architecture">Execution path</a><a href="#matrix">Family matrix</a><a href="#glm">GLM</a><a href="#deployment">Deployment</a><a href="#acceptance">Your testing</a><a href="../../docs/research/model-ecosystem.md">Technical record</a></nav>
<p class="lead">Adapt the protocol to the model.<br>Keep execution policy inside Echo.</p>
<p class="muted">The native harness now handles GPT-6 tool calls through Responses, current Claude effort controls, Gemini Argon thinking, and the configured model’s context window.</p>
<div class="note"><strong>Evidence boundary:</strong> source inspection and offline mocked contracts. No model downloads, live requests, credits or performance measurements. The existing 11.10.6 installer predates these source changes; rebuild to include them.</div>
<section id="changes"><h2>Concrete compatibility repairs</h2><div class="three">
<article class="card"><span class="number">PROTOCOL</span><h3>GPT-6 → Responses</h3><p>Stateless requests, streamed text and scoped opaque state. Tools return only after a completed response. Echo still owns every execution decision.</p></article>
<article class="card"><span class="number">MODEL CONTROLS</span><h3>Effort reflects the family</h3><p>Documented Claude output_config; Gemini 4 Argon; OpenAI xhigh/max and supported off settings. Non-thinking Coder-Next no longer inherits the Qwen3 heuristic.</p></article>
<article class="card"><span class="number">CONTEXT</span><h3>A known window is a bound</h3><p>A configured 4K local model no longer inherits a 64K minimum. Global trim settings can reduce known windows. Output reserves and usage use the resolved budget.</p></article>
</div><div class="boundary"><b>Expanded local compatibility:</b> literal-only Liquid tool-call batches, opt-in scoped reasoning history, explicit Boolean template thinking control, and refusal of unfinished native tool streams. No account, permission or privacy bypass.</div><p><small>Verified contract evidence: __TESTS__. Live model behavior remains unverified. Details and source hashes are recorded in the technical evidence file.</small></p></section>
<section id="architecture"><h2>One execution loop, explicit transport boundaries</h2><p class="muted">Architecture diagram based on inspected source; this is not an application screenshot.</p>
<div class="flow" aria-label="Model to tool execution path">
<div class="card"><h3>Selection &amp; identity</h3><p>Session binding → persona assignment → exact provider/model. Favorites organize candidates; they do not grant capability.</p></div>
<div class="card"><h3>Context &amp; privacy</h3><p>Project instructions, soul, memory and images. Resolve configured context. Check privacy before network traffic.</p></div>
<div class="card"><h3>Provider adapter</h3><p>Responses / Claude Messages / Gemini Live / compatible Chat. Translate protocol, keep account and model affinity.</p></div>
<div class="card"><h3>Canonical model turn</h3><p>Content, reasoning, tool IDs, arguments and usage. Opaque provider state remains internal to the active turn.</p></div>
<div class="card"><h3>Echo policy &amp; Toolbox</h3><p>Validate arguments, project scope, caller role, approvals, taint and Stop. Native and recovered calls share this gate.</p></div>
<div class="card"><h3>Feedback &amp; continuation</h3><p>Return results and errors to the selected model. Trim/compact when needed. Stream visible events into the existing timeline.</p></div>
</div><div class="boundary"><b>Separate Code runtimes:</b> Codex app-server, Claude Agent SDK and ACP keep their own accounts, sessions, tools and permissions. They are not extra native model providers.</div></section>
<section><h2>What “supported” actually establishes</h2><div class="two"><div class="card"><h3 class="confirmed">Confirmed contracts</h3><p>Mocked request formats, terminal Responses tool calls, token usage, opaque-state isolation, thinking mappings and configured context bounds. Existing privacy/tool-loop regressions pass.</p></div><div class="card"><h3>Still dependent on your setup</h3><p>Real reasoning behavior, tool quality, exact server parser, image projector, latency, memory fit, account entitlement and long-context performance.</p></div></div></section>
__PRIORITIES__
<section id="matrix"><h2>Model families &amp; native Echo compatibility</h2><p class="muted">__COUNT__ family groups across __DEVELOPERS__ developers. Current public cards and meaningful earlier weights; model authors and API hosts are separate. Scroll horizontally on small screens.</p><div class="legend"><span class="status conditional">Conditional · adapter exists</span><span class="status unsupported">Unsupported · missing route</span><span class="status unverified">Unverified · insufficient contract evidence</span></div>
<div class="matrix" tabindex="0" aria-label="Scrollable model compatibility matrix"><table><thead><tr><th>Family / identifiers</th><th>Model capabilities &amp; limits</th><th>Echo compatibility &amp; source</th></tr></thead><tbody>__ROWS__</tbody></table></div>
<p><small>Published weights, source-verified API behavior and live acceptance are different states. No row in this matrix certifies end-to-end execution.</small></p></section>
<section id="deployment"><h2>The server is part of the compatibility contract</h2><div class="two"><article class="card"><h3>Local runtimes</h3><p>LM Studio and Ollama offer compatible HTTP. llama.cpp, vLLM and SGLang require matching architecture, template, tool/reasoning parser and multimodal support. Echo’s in-process llama_cpp choice is not a lean HTTP endpoint.</p></article><article class="card"><h3>Cloud hosting</h3><p>Together, Fireworks, Groq, Cerebras, HF and OpenRouter have separate credentials, IDs and policies. Echo has no first-class account adapters for these. Bedrock/Vertex/Foundry add platform authentication and deployment contracts.</p></article></div><div class="boundary"><b>Memory planning:</b> total parameters × bits ÷ 8 is only the weight floor. KV cache, projectors, buffers and parallel requests add memory. MoE active parameters are not total memory requirements.</div></section>
<section id="glm"><h2>GLM variants have different contracts</h2><div class="two"><article class="card"><h3>5.3 is not 5.3 Flash</h3><p>GLM-5.3 is text-only and requires thinking with low/high/max. Flash is a separate 320B/18B multimodal model; FlashX has different access from the Coding Plan. None is a small quant of another.</p></article><article class="card"><h3>Smaller local GLM</h3><p>GLM-4.7-Flash is a distinct 30B-A3B local candidate; 4.5-Air is much larger at 106B total. Match the checkpoint, parser, history and context allocation. Echo has no native Z.ai account adapter.</p></article></div><p><small>The technical record includes current API parameters, direct-versus-plan differences and official sources.</small></p></section>
<section><h2>Known gaps remain visible</h2><ul><li>Local reasoning_content replay is now explicitly configurable and scoped. Direct Kimi/DeepSeek account integration and cross-user-turn opaque persistence remain absent.</li><li>Grok’s newer Responses/effort path and authenticated local endpoints need further integration.</li><li>Provider-reported catalog limits are retained, but are not automatically applied to every runtime profile.</li><li>Unmeasured family defaults remain diagnostic assumptions, not negotiated capability proof. Generic audio/video/strict schema support is not inferred from a model name.</li></ul></section>
<section id="acceptance"><h2>A focused owner acceptance pass</h2><ol><li>One GPT-6 model: harmless read → tool result → answer; Stop mid-stream and switch models between turns.</li><li>One current Claude model: low/high effort, permitted tool cycle and a denied action.</li><li>Gemini Argon or supported Flash: multiple tool IDs/signatures and a recoverable error; Live separately.</li><li>One familiar local Gemma/Qwen instruct model: native tool, malformed arguments and a small configured context.</li><li>Private/Offline mode: remote model blocked; local work keeps project and tool restrictions.</li></ol><p class="muted">Choose only the models you use. This is not a paid benchmark campaign or a request to download a catalog.</p></section>
<section><h2>Build these source changes into an installer</h2><pre><code># From the EchoSpeak repository root
git switch codex/model-compatibility-audit
git pull --ff-only
cd apps\\desktop
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-windows.ps1</code></pre><p><small>Local test build only. Version 11.10.6 is already public; a new public update needs a newer version and signed release preparation. No installer was built by this task. Do not replace public assets or reuse older bundles with -SkipBuild.</small></p></section>
<footer><a href="../../docs/research/model-ecosystem.md">Technical record &amp; primary sources</a> · <a href="../../docs/research/model-ecosystem-catalog.json">Structured catalog</a> · <a href="../../docs/research/model-ecosystem-verification.json">Verification evidence</a><p>Static local report. No scripts, remote assets or model requests. Maintain exact-model metadata with source, date, endpoint and verification level.</p></footer>
</main></body></html>'''
    verification = ROOT / "docs/research/model-ecosystem-verification.json"
    evidence = json.loads(verification.read_text(encoding="utf-8")) if verification.exists() else {}
    tests = evidence.get("tests", {}).get("summary", "targeted suite recorded in verification evidence")
    source = source.replace("__DATE__", esc(data["as_of"])).replace("__COUNT__", str(len(rows))).replace("__ROWS__", cards).replace("__TESTS__", esc(tests)).replace("__DEVELOPERS__", str(data["developers"])).replace("__PRIORITIES__", priority_html)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(source, encoding="utf-8")
    print(f"Rendered {len(rows)} family groups: {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    render()
