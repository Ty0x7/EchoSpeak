"""Actual left sidebar, model assets and Code composer: deterministic browser verification."""
from pathlib import Path
import json, os, shutil, socket, subprocess, time, urllib.request, uuid
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/"apps/web"
MEDIA=ROOT/os.environ.get("ECHOSPEAK_UI_MEDIA", "artifacts/media/sidebar-11.13.1")

def fixture():
    def inbox(state, check="Not checked", triage=True):
        return {"state":state,"reason":"Fixture execution evidence","activity":uuid.uuid4().hex,"verification":check,"updated_at":time.time()-120,"can_triage":triage}
    def row(identity,title,state,check="Not checked"):
        return {"thread_id":identity,"title":title,"project_id":"p","surface":"code","agent_runtime":"echo","last_active_at":time.time(),"message_count":2,"inbox":inbox(state,check,state!="working")}
    return {"projects":[{"id":"p","name":"Example repository","workspace_root":"fixture","trust_state":"trusted","sessions":[
        row("working","Update type definitions","working"),row("failed","Repair parser edge case","needs_attention","Checks failed"),
        row("review","Fix settings path","ready_to_review","Checks pass"),row("settled","Review cleanup","settled","Verified"),row("snoozed","Explore cache strategy","snoozed")],
        "external":[{"id":"external","project_id":"p","runtime_id":"codex","state":"running","created_at":time.time(),"title":"Review runtime adapter","branch":"codex/runtime-adapter","inbox":inbox("needs_attention","Runtime-owned checks",False)}]}],"unorganized":[],"drafts":[]}

MODULE=r'''
import React, {useState} from "react";
import manifest from "./package.json";
import {createRoot} from "react-dom/client";
import {CodeTree} from "./src/code/CodeTree";
import {EchoCodeWorkspace} from "./src/code/EchoCodeWorkspace";
import {ProjectSidebar} from "./src/components/ProjectSidebar";
import {RuntimeMark} from "./src/components/RuntimeMark";
import {AgentRows,CollapsedRoster} from "./src/lean/Roster";
import {StopEverythingButton} from "./src/dashboard/StopEverything";
import {WorkbenchIcon} from "./src/workbench/icons";
import "./src/workbench/workbench.css";
import "./src/theme/tokens.css";import "./src/lean/lean.css";import "./src/code/code.css";import "./src/projects/workspace.css";import "./src/theme/workspace.css";
const noop=()=>{};
const projects=[{id:"p",name:"Example repository",workspace_root:"fixture",trust_state:"trusted"}];
const info:any={provider:"lmstudio",model:"fixture-local",local:true,binding_revision:1,available_providers:[{id:"lmstudio",name:"LM Studio",local:true},{id:"openai",name:"OpenAI",local:false}],model_profile:{thinking_controls:{supported:true,effort:true,toggle:true}}};
function Preview(){
 const [pid,setPid]=useState("p");
 const [env,setEnv]=useState<any>("code");
 const [codeQuery,setCodeQuery]=useState("");
 const [collapsed,setCollapsed]=useState(false);
 const contacts:any[]=[{id:"echo",name:"Echo",initials:"E",title:"Your personal assistant"},{id:"scout",name:"Jarvis",initials:"J",title:"Research and investigation"}];
 const conversations=[{id:"chat1",name:"Planning the next release",at:Date.now(),projectId:"p"},{id:"chat2",name:"Ideas for the dashboard",at:Date.now()-86400000}];
 const sidebar=<CodeTree searchQuery={codeQuery} apiBase="/fixture-api" revision={0} activeThreadId="failed" activeExternalId="" workingSessionIds={new Set(["working"])}
 onOpenNative={s=>{document.body.dataset.opened=s.thread_id}} onOpenExternal={(_p,_r,id)=>{document.body.dataset.opened=id}}
 onNewSession={id=>setPid(id||"p")} onAddFolder={noop}/>;
 return <div className="echo-root fixture-layout" style={collapsed?{gridTemplateColumns:"56px minmax(0,1fr)"}:undefined}><div className="fixture-sidebar"><ProjectSidebar collapsed={collapsed} projects={projects} sessions={conversations} activeProjectId={pid} activeSessionId="failed" activeView="code"
 onToggleCollapsed={()=>setCollapsed(v=>!v)} onNewSession={noop} onAddFolder={noop} onSelectSession={noop} onRenameSession={noop} onDeleteSession={noop} onDeleteProject={noop} onView={noop} onSettings={noop}
 footerExtra={<><div className="wb-sidebar-actions"><button aria-label="Pull requests"><WorkbenchIcon kind="pull_requests"/></button><button aria-label="Usage and limits"><WorkbenchIcon kind="usage"/></button></div><StopEverythingButton apiBase="/fixture-api"/></>}
 environment={env} onEnvironment={setEnv} onPage={noop} collapsedRoster={<CollapsedRoster agents={contacts} onOpenAgent={a=>{document.body.dataset.agent=a.id}}/>} onSearchChats={async q=>conversations.filter(c=>c.name.toLowerCase().includes(q.toLowerCase())).map(c=>({session_id:c.id,title:c.name,snippet:"Fixture conversation",role:"user",agent:"",created_at:c.at,matches:1}))} agents={{count:2,onNew:noop,list:<AgentRows agents={contacts} rooms={[]} activeThreadId="" onOpenAgent={a=>{document.body.dataset.agent=a.id}} onEditAgent={noop}/>}} codeTree={sidebar} codeQuery={codeQuery} onCodeQuery={setCodeQuery}/></div><main className="fixture-main">
 <div className="fixture-caption">EchoSpeak {manifest.version} · component preview · no model connected</div>
 <EchoCodeWorkspace apiBase="/fixture-api" projects={projects} projectId={pid} onProject={setPid} onAddFolder={noop} onSession={s=>{document.body.dataset.started=s.thread_id}} nativeInfo={info} nativeLocked={false} onConfigureProviders={noop}/>
 <div className="fixture-assets" aria-label="Provider image checks"><RuntimeMark runtime="codex"/><RuntimeMark runtime="claude_cli"/></div>
 </main></div>;
}
createRoot(document.getElementById("root")!).render(<Preview/>);
'''
STYLE="""html,body,#root{margin:0;height:100%;background:var(--es-bg);color:var(--es-text);font-family:Segoe UI,sans-serif}
.fixture-layout{display:grid;grid-template-columns:288px minmax(0,1fr);height:100vh}.fixture-sidebar{height:100vh;min-width:0}.fixture-main{padding:18px;min-width:0;overflow:auto}.fixture-caption{font-size:11px;color:var(--es-text-3);margin-bottom:24px}.fixture-assets{display:flex;gap:12px;padding:18px}.ec-checkout-bar{flex-wrap:wrap}.es-code-tree-host{overflow:auto;min-height:0;flex:1}
@media(max-width:600px){.fixture-layout{grid-template-columns:288px}.fixture-main{display:none}}
"""

def main():
    MEDIA.mkdir(parents=True,exist_ok=True)
    scratch=ROOT/".test-state";scratch.mkdir(exist_ok=True)
    name=".code-inbox-fixture-"+uuid.uuid4().hex
    entry,module=WEB/(name+".html"),WEB/(name+".tsx")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    origin=f"http://127.0.0.1:{port}"
    entry.write_text('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+STYLE+'</style></head><body><div id="root"></div><script type="module" src="/'+name+'.tsx"></script></body></html>',encoding="utf-8")
    module.write_text(MODULE,encoding="utf-8")
    log=(scratch/"code-inbox-vite.log").open("w",encoding="utf-8")
    server=subprocess.Popen([shutil.which("node"),str(WEB/"node_modules/vite/bin/vite.js"),"--host","127.0.0.1","--port",str(port),"--strictPort"],cwd=WEB,stdout=log,stderr=log,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    try:
        for _ in range(120):
            try: urllib.request.urlopen(origin+"/"+entry.name,timeout=1).close();break
            except OSError:
                if server.poll() is not None: raise RuntimeError("Vite failed; inspect fixture log")
                time.sleep(.25)
        data,requests,failures=fixture(),[],[]
        workspace_git=[False]
        external_sessions=[]
        clients=[]
        native={"provider":"lmstudio","model":"fixture-local","local":True,"binding_revision":1,"model_profile":{"thinking_controls":{"supported":True,"effort":True,"toggle":True}}}
        runtime=lambda identity,name:{"id":identity,"name":name,"implemented":True,"installed":True,"can_start":True,"authentication":"fixture","protocol":"native","blockers":[],"description":"Fixture"}
        with sync_playwright() as p:
            browser=p.chromium.launch()
            page=browser.new_page(viewport={"width":1280,"height":900},device_scale_factor=1)
            page.emulate_media(reduced_motion="reduce")
            page.on("pageerror",lambda e:failures.append(str(e)))
            def navigation_check(labels, search_label):
                expect(page.locator('.es-sidebar-identity')).to_have_count(0)
                search_box=page.get_by_label(search_label,exact=True).bounding_box()
                assert search_box['y']<page.get_by_role('tab',name='Chat',exact=True).bounding_box()['y']
                nav=page.get_by_role('navigation',name='Pages',exact=True).get_by_role('button')
                assert nav.all_inner_texts()==labels
                boxes=[button.bounding_box() for button in nav.all()]
                assert all(abs(box['x']-boxes[0]['x'])<1 for box in boxes)
                assert all(b['y']>=a['y']+a['height'] for a,b in zip(boxes,boxes[1:]))
                utilities=[page.get_by_role('button',name=n,exact=True).bounding_box() for n in ['Pull requests','Usage and limits','Stop everything','Settings']]
                assert all(abs(box['y']-utilities[0]['y'])<1 for box in utilities)
                assert all(b['x']>=a['x']+a['width'] for a,b in zip(utilities,utilities[1:]))
            def api(route):
                path=route.request.url.split("/fixture-api",1)[1].split("?")[0]
                body=route.request.post_data_json if route.request.method=="POST" else None
                requests.append({"path":path,"method":route.request.method,"body":body})
                if path=="/echo-code/overview": route.fulfill(json=data)
                elif path=="/echo-code/inbox/sync":route.fulfill(json={"settled":[]})
                elif path=="/echo-code/runtimes":route.fulfill(json={"schema_version":1,"privacy_mode":"standard","runtimes":[runtime("echo","Echo native"),runtime("codex","Codex"),runtime("claude_cli","Claude Code")]})
                elif path.endswith("/account") or path.endswith("/check"):
                    is_codex = "/codex/" in path
                    model = {"model":"fixture-codex-1","displayName":"Fixture Codex","isDefault":True,"efforts":["low","high"]} if is_codex else {"model":"claude-opus-5-5","displayName":"Opus 5.5","isDefault":True,"efforts":["low","high"]}
                    route.fulfill(json={"signed_in":True,"method":"chatgpt" if is_codex else "claude.ai","plan":"fixture","models":[model],"subscription_usage":{"observed_at":time.time(),"source":"Scripted account fixture","windows":[{"label":"Fixture plan window","used_percent":25}]}})
                elif path.endswith("/external-sessions") and body is not None:
                    mode=body.get('workspace_mode','folder')
                    row={'id':uuid.uuid4().hex,'runtime_id':body['runtime_id'],'project_id':'p','workspace_mode':mode,'worktree':'fixture/project' if mode=='folder' else 'fixture/isolated/tree','workspace_root':'fixture/project','created_at':time.time(),'state':'stopped','account':{'type':'fixture','connected':True},'connected':False,'models':[],'native_id':None,'approvals':[],'events':[],'cursor':0,'replay_gap':False,'rollback_available':False}
                    external_sessions.append(row);route.fulfill(json=row)
                elif path.endswith("/external-sessions"):route.fulfill(json=external_sessions)
                elif path.startswith('/echo-code/external-sessions/'):
                    row=next(r for r in external_sessions if r['id']==path.split('/')[-1])
                    cursor=int(parse_qs(urlsplit(route.request.url).query).get('cursor',['0'])[0])
                    route.fulfill(json={**row,'events':[e for e in row['events'] if e['seq']>cursor]})
                elif path=="/echo-code/projects/p":route.fulfill(json={"schema_version":1,"project":data["projects"][0],"git_summary":"git: branch feature/fixture" if workspace_git[0] else "","sessions":[],"can_start_native":True,"native_blocker":"","external_blocker":"","worktree_available":workspace_git[0],"worktree_unavailable":"Optional worktree requires a committed Git repository" if not workspace_git[0] else ""})
                elif path=="/provider/models":
                    provider=parse_qs(urlsplit(route.request.url).query)['provider'][0]
                    route.fulfill(json={"provider":provider,"reachable":True,"models":["fixture-api-model" if provider=="openai" else "fixture-local"]})
                elif path.startswith("/provider"):route.fulfill(json=native)
                elif path=="/echo-code/projects/p/sessions":
                    item={"thread_id":uuid.uuid4().hex,"title":"Background fixture task","project_id":"p","surface":"code","agent_runtime":"echo","last_active_at":time.time(),"message_count":0,"inbox":{**data["projects"][0]["sessions"][2]["inbox"],"state":"settled"}}
                    data["projects"][0]["sessions"].insert(0,item);route.fulfill(json=item)
                elif path=="/echo-code/background":
                    item=next(s for s in data["projects"][0]["sessions"] if s["thread_id"]==body["thread_id"]);item["inbox"]={**item["inbox"],"state":"working","can_triage":False};route.fulfill(json={"status":"accepted"})
                elif path=="/echo-code/drafts" and body is not None:
                    images=[".echospeak/attachments/fixture.png"] if body.get("images") else []
                    row={"id":uuid.uuid4().hex,"project_id":"p","project_name":"Example repository","title":body["text"] or "Image draft","text":body["text"]+("\nAttached project images:\n"+images[0] if images else ""),"images":images}
                    data["drafts"].insert(0,row);route.fulfill(json=row)
                elif path.startswith("/echo-code/drafts/"):
                    row=next(r for r in data["drafts"] if r["id"]==path.split("/")[-1]);route.fulfill(json=row)
                elif path=="/echo-code/inbox/batch":
                    results=[]
                    for t in body["targets"]:
                        row=next((r for r in data["projects"][0]["sessions"] if r["thread_id"]==t["id"]),None)
                        if row is None or row["inbox"]["activity"]!=t["activity"]:results.append({"ok":False,"error":"Session changed; refresh before triage"});continue
                        row["inbox"]["state"]={"settled":"settled","snoozed":"snoozed","reopen":"ready_to_review"}[body["action"]];results.append({"ok":True})
                    route.fulfill(json={"results":results})
                elif path=="/echo-code/control/clients" and body is not None:
                    row={"id":"fixture-client","name":body["name"],"control":body.get("control",False),"expires_at":time.time()+7*86400}
                    clients.append(row);route.fulfill(json={"client":row,"token":"fixture-token-for-ui-test"})
                elif path=="/echo-code/control/clients":route.fulfill(json={"clients":clients})
                elif path.startswith("/echo-code/control/clients/"):
                    clients.clear();route.fulfill(json={"revoked":True})
                elif path=="/echo-code/control/bridge":route.fulfill(json={"command":"fixture-backend.exe","args":["--control-mcp"]})
                elif path=="/lean/agents":route.fulfill(json={"items":[{"id":"echo","name":"Echo"}]})
                else:route.fulfill(json={})
            page.route("**/fixture-api/**",api)
            page.goto(origin+"/"+entry.name)
            search=page.get_by_label("Search coding sessions")
            expect(search).to_be_visible()
            expect(page.get_by_text("Repair parser edge case",exact=True)).to_be_visible()
            expect(page.get_by_text("Update type definitions",exact=True)).to_have_count(0)
            expect(page.get_by_role("button",name="Working (1)",exact=True)).to_be_visible()
            expect(page.get_by_role("button",name="Snoozed (1)",exact=True)).to_be_visible()
            expect(page.get_by_role("button",name="Settled (1)",exact=True)).to_be_visible()
            assert page.locator(".fixture-sidebar").bounding_box()["x"]==0
            assert page.locator(".fixture-main").bounding_box()["x"]>=288
            page.wait_for_function("Array.from(document.querySelectorAll('.fixture-assets img')).every(i=>i.complete&&i.naturalWidth>0)")
            page.screenshot(path=str(MEDIA/"left-sidebar-workspace.png"))
            page.locator(".echo-sidebar").screenshot(path=str(MEDIA/"inbox-dark.png"))
            assert page.locator(".echo-sidebar").bounding_box()["width"]==288
            # Native Code lists configured API models; subscription runtimes show
            # a separate actual-model catalog and quota observation.
            page.get_by_role("button",name="Agent runtime",exact=True).click()
            page.get_by_role("button",name="fixture-api-model ChatGPT · API",exact=True).click()
            expect(page.get_by_role("button",name="Agent runtime",exact=True)).to_contain_text("fixture-api-model")
            page.get_by_role("button",name="Agent runtime",exact=True).click()
            page.get_by_role("button",name="Claude Code models",exact=True).click()
            expect(page.locator(".es-model-choice code").filter(has_text="claude-opus-5-5")).to_be_visible()
            expect(page.get_by_text("25% used",exact=True).first).to_be_visible()
            page.locator(".es-model-popover").screenshot(path=str(MEDIA/"runtime-model-picker.png"))
            page.get_by_role("button",name="Opus 5.5 claude-opus-5-5",exact=False).first.click()
            expect(page.get_by_role("button",name="Agent runtime",exact=True)).to_contain_text("Opus 5.5")
            page.get_by_role("button",name="Coding agent connection",exact=True).click()
            expect(page.get_by_label('Coding workspace mode')).to_have_value('folder')
            expect(page.get_by_text('Git and GitHub are optional.',exact=False)).to_be_visible()
            page.locator('.ec-connection-popover').screenshot(path=str(MEDIA/'claude-folder-mode-fixture.png'))
            page.keyboard.press('Escape')
            page.get_by_role("button",name="Agent runtime",exact=True).click()
            page.get_by_role("button",name="Codex models",exact=True).click()
            page.get_by_role("button",name="Fixture Codex fixture-codex-1",exact=False).first.click()
            page.get_by_role("button",name="Coding agent connection",exact=True).click()
            expect(page.get_by_text("Limits refresh automatically every 5 minutes and when Codex reports a change.",exact=True)).to_be_visible()
            checks_before = sum(r["path"] == "/echo-code/runtimes/codex/check" for r in requests)
            page.get_by_role("button",name="Refresh limits",exact=True).click()
            page.wait_for_function("() => document.querySelector('.ec-external .ec-plan-usage')?.textContent.includes('25% used')")
            page.wait_for_timeout(300)
            assert sum(r["path"] == "/echo-code/runtimes/codex/check" for r in requests) == checks_before + 1
            page.locator(".ec-connection-popover").screenshot(path=str(MEDIA/"codex-usage-refresh-fixture.png"))
            expect(page.get_by_label('Coding workspace mode')).to_have_value('folder')
            assert page.get_by_role('option',name='Isolated Git worktree',exact=True).evaluate('(option)=>option.disabled')
            expect(page.get_by_role('checkbox',name='Start from committed HEAD',exact=False)).to_have_count(0)
            page.get_by_label('I trust Codex with this project.',exact=False).check()
            page.get_by_role('button',name='Create session',exact=True).click()
            expect(page.get_by_text('Project folder · stopped',exact=False)).to_be_visible()
            assert external_sessions[-1]['workspace_mode']=='folder'
            expect(page.get_by_role('button',name='Roll back last turn',exact=True)).to_have_count(0)
            page.locator('.ec-connection-popover').screenshot(path=str(MEDIA/'codex-folder-session-fixture.png'))
            # A runtime completion refreshes Git capabilities without a manual UI reload.
            workspace_git[0]=True
            external_sessions[-1].update(cursor=1,events=[{'seq':1,'kind':'turn/finished','data':{'state':'completed'}}])
            expect(page.get_by_text('git: branch feature/fixture',exact=True)).to_be_visible()
            page.get_by_label('External coding session').select_option('')
            expect(page.get_by_label('Coding workspace mode')).to_have_value('folder')
            page.wait_for_function("() => { const option=document.querySelector('option[value=worktree]'); return option && !option.disabled; }")
            page.get_by_label('Coding workspace mode').select_option('worktree')
            expect(page.get_by_role('checkbox',name='Start from committed HEAD',exact=False)).to_be_visible()
            page.get_by_label('I trust Codex with this project.',exact=False).check()
            page.locator('.ec-connection-popover').screenshot(path=str(MEDIA/'optional-worktree-mode-fixture.png'))
            page.get_by_role('button',name='Create isolated session',exact=True).click()
            expect(page.get_by_text('Isolated Git worktree · stopped',exact=False)).to_be_visible()
            assert external_sessions[-1]['workspace_mode']=='worktree'
            page.keyboard.press("Escape")
            page.get_by_role("button",name="Agent runtime",exact=True).click()
            page.get_by_role("button",name="Echo native models",exact=True).click()
            page.get_by_role("button",name="fixture-local LM Studio · On this PC",exact=False).first.click()
            # Each parked section grows above its bar. Switching shelves reserves
            # useful reading space even in a short window instead of three slivers.
            for height in (900, 560):
                page.set_viewport_size({"width":1280,"height":height})
                for name in ("Working (1)", "Snoozed (1)", "Settled (1)"):
                    page.get_by_role("button",name=name,exact=True).click()
                    expect(page.locator(".ec-inbox-shelf.is-expanded")).to_have_count(1)
                    shelf=page.locator(".ec-inbox-shelf.is-expanded")
                    bar=shelf.locator(".ec-inbox-divider").bounding_box()
                    contents=shelf.locator("ul").bounding_box()
                    assert contents["height"]>=60 and contents["y"]+contents["height"]<=bar["y"]+1
                    assert bar["y"]+bar["height"]<=height
                footer=page.get_by_role("button",name="MCP access & diagnostics",exact=True).bounding_box()
                settings=page.get_by_role("button",name="Settings",exact=True).bounding_box()
                assert footer["y"]+footer["height"]<=settings["y"]
                assert settings["y"]+settings["height"]<=height
                page.locator(".echo-sidebar").screenshot(path=str(MEDIA/("inbox-expanded-up.png" if height==900 else "inbox-expanded-short.png")))
                page.get_by_role("button",name="Settled (1)",exact=True).click()
            page.set_viewport_size({"width":1280,"height":900})
            page.get_by_role("button",name="Inbox filters",exact=True).click()
            expect(page.get_by_label("Filter coding projects")).to_be_visible()
            page.get_by_role("checkbox",name="Working shelf",exact=True).uncheck()
            expect(page.get_by_text("Update type definitions",exact=True)).to_be_visible()
            page.get_by_role("checkbox",name="Working shelf",exact=True).check()
            page.get_by_role("button",name="Inbox filters",exact=True).press("Enter")
            expect(page.get_by_label("Filter coding projects")).to_have_count(0)
            search.fill("parser");expect(page.get_by_text("Fix settings path",exact=True)).to_have_count(0);search.fill("")
            # Range selection uses the visible list, never collapsed shelves.
            page.get_by_text("Repair parser edge case",exact=True).click(modifiers=["Control"])
            page.get_by_text("Fix settings path",exact=True).click(modifiers=["Shift"])
            expect(page.get_by_text("2 selected",exact=True)).to_be_visible()
            page.get_by_label("Bulk inbox action").select_option("snoozed:900")
            expect(page.get_by_role("button",name="Snoozed (3)",exact=True)).to_be_visible()
            expect(page.get_by_text("Repair parser edge case",exact=True)).to_have_count(0)
            page.get_by_role("button",name="Snoozed (3)",exact=True).click()
            page.get_by_role("button",name="Inbox actions for Fix settings path",exact=True).click()
            # A newer activity arrives after opening the menu.
            row=next(r for r in data["projects"][0]["sessions"] if r["thread_id"]=="review");row["inbox"]["activity"]=uuid.uuid4().hex
            page.get_by_label("Inbox state for Fix settings path").select_option("settled")
            expect(page.get_by_role("alert")).to_contain_text("Session changed")
            page.reload();expect(search).to_be_visible()
            page.locator("html").evaluate("el=>el.setAttribute('data-es-theme','light')")
            page.locator(".echo-sidebar").screenshot(path=str(MEDIA/"inbox-light.png"))
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            task=page.get_by_label("Coding task draft");expect(task).to_be_visible()
            task.fill("Save the layout investigation");task.press("Control+s")
            expect(page.get_by_text("Save the layout investigation",exact=True)).to_be_visible()
            # Finish the stash's own state update before activating the new row.
            # Target the button: clicking its newly inserted text can straddle
            # React's async overview refresh between pointer-down and pointer-up.
            expect(task).to_have_value("")
            expect(task).to_be_enabled()
            with page.expect_response(lambda response:"/echo-code/drafts/" in response.url and response.request.method=="GET"):
                page.locator(".ec-inbox-drafts .ec-inbox-open").filter(has_text="Save the layout investigation").click()
            try:
                expect(task).to_have_value("Save the layout investigation")
            except AssertionError:
                print(json.dumps({"recent_requests":requests[-8:],"page_errors":failures,"restore_storage":page.evaluate("sessionStorage.getItem('echospeak.code.restoreDraft')"),"body":page.locator("body").inner_text()[-2000:]},indent=2))
                page.screenshot(path=str(scratch/"code-inbox-restore-failure.png"))
                raise
            task.fill("Run this in the background");task.press("Control+Enter")
            expect(page.get_by_text("Sent in the background · open it from Working",exact=True)).to_be_visible()
            expect(task).to_have_value("")
            assert any(r["path"]=="/echo-code/background" for r in requests)
            # Image paste/upload and stashing remain owner actions.
            page.locator('input[type=file]').set_input_files(str(WEB/"public/logo.png"))
            task.fill("Inspect the attached image");task.press("Control+s")
            expect(page.get_by_text("Inspect the attached image",exact=True)).to_be_visible()
            assert any(r["path"]=="/echo-code/drafts" and r["body"].get("images") for r in requests)
            page.get_by_role("button",name="MCP access & diagnostics",exact=True).click()
            page.get_by_label("MCP client name",exact=True).fill("Fixture reader")
            page.get_by_role("checkbox",name="Example repository",exact=True).check()
            expect(page.get_by_role("checkbox",name="Allow create, send, stop and inbox changes",exact=True)).not_to_be_checked()
            page.get_by_role("button",name="Create client access",exact=True).click()
            expect(page.get_by_label("New MCP client token",exact=True)).to_have_value("fixture-token-for-ui-test")
            assert next(r for r in requests if r["path"]=="/echo-code/control/clients" and r["method"]=="POST")["body"]["control"] is False
            page.get_by_role("button",name="Hide token",exact=True).click()
            expect(page.get_by_label("New MCP client token",exact=True)).to_have_count(0)
            page.get_by_role("button",name="Revoke",exact=True).click()
            expect(page.get_by_text("Fixture reader · Read only",exact=True)).to_have_count(0)
            # Exercise actual shared navigation and actual AgentRows, not a mock layout.
            page.get_by_role("tab",name="Chat",exact=True).click()
            expect(page.get_by_label("Search chats",exact=True)).to_be_visible()
            navigation_check(['New chat','Projects','Creations','Artifacts'],'Search chats')
            expect(page.get_by_role("button",name="Session: Planning the next release",exact=True)).to_be_visible()
            page.locator(".echo-sidebar").screenshot(path=str(MEDIA/"sidebar-chat-light.png"))
            page.get_by_label("Search chats",exact=True).fill("dashboard")
            expect(page.get_by_role("button",name="Ideas for the dashboard You: Fixture conversation")).to_be_visible()
            page.get_by_label("Search chats",exact=True).fill("")
            page.get_by_role("tab",name="Agents",exact=True).click()
            navigation_check(['New agent','Group chats','Routines'],'Search messages')
            expect(page.get_by_text("Your personal assistant",exact=True)).to_be_visible()
            page.get_by_role("button",name="Echo Your personal assistant",exact=True).click()
            assert page.locator("body").get_attribute("data-agent")=="echo"
            page.locator(".echo-sidebar").screenshot(path=str(MEDIA/"sidebar-agents-light.png"))
            page.locator("html").evaluate("el=>el.removeAttribute('data-es-theme')")
            page.locator(".echo-sidebar").screenshot(path=str(MEDIA/"sidebar-agents-dark.png"))
            page.get_by_role("button",name="Collapse sidebar",exact=True).click()
            expect(page.get_by_role("button",name="Expand sidebar",exact=True)).to_be_visible()
            assert page.locator(".echo-sidebar").bounding_box()["width"]==56
            page.locator(".echo-sidebar").screenshot(path=str(MEDIA/"sidebar-rail.png"))
            page.get_by_role("button",name="Expand sidebar",exact=True).click()
            page.get_by_role("tab",name="Chat",exact=True).click()
            page.locator(".echo-sidebar").screenshot(path=str(MEDIA/"sidebar-chat-dark.png"))
            page.get_by_role("tab",name="Code",exact=True).click()
            page.set_viewport_size({"width":390,"height":900});expect(search).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert not failures,failures
            report=browser.new_context(java_script_enabled=False)
            rp=report.new_page()
            for document,label in [("artifacts/research/native-code.html","code-inbox-report"),("artifacts/releases/v11.13.1.html","release-11.13.1"),("artifacts/releases/v11.13.0.html","release-11.13.0"),("artifacts/releases/index.html","release-atlas")]:
                for width,theme in [(1400,"dark"),(390,"light")]:
                    rp.set_viewport_size({"width":width,"height":1100});rp.emulate_media(color_scheme=theme)
                    rp.goto((ROOT/document).as_uri())
                    assert rp.evaluate("document.documentElement.scrollWidth<=innerWidth"),document
                    assert rp.locator("img").evaluate_all("items=>items.every(i=>i.complete&&i.naturalWidth>0)"),document
                    rp.screenshot(path=str(scratch/f"{label}-{width}.png"))
            report.close();browser.close()
        result={"version":json.loads((WEB/"package.json").read_text())["version"],"checks":["shared Chat/Agents/Code navigation, search and agent callback","left sidebar placement","flat active rows","collapsed Working/Snoozed/Settled shelves","each shelf expands upward with at least 60px reading space at 900px and 560px heights; settings remain visible","native API model selection; exact Claude model ID and reported usage; switch back to local model","local provider image assets","search","visible Shift range selection","bulk timed snooze","stale action rejection","errors survive refresh","dark/light themes","Ctrl+S stash and restore","Ctrl+Enter background submit","image stash","MCP panel read-only create, hide token and revoke","390px without overflow","no page errors","research report, versioned release report and atlas wide/narrow with scripts disabled"],"model_calls":0,"request_count":len(requests)}
        (scratch/"code-inbox-browser-result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result,indent=2))
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
        log.close();entry.unlink(missing_ok=True);module.unlink(missing_ok=True)

if __name__=="__main__":main()
