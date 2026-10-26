// Actual production components with deterministic local fixtures, frozen Object.prototype and no model/account calls.
// Requires an existing Playwright module/browser, as in check-startup-browser.cjs.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.ECHOSPEAK_PLAYWRIGHT_MODULE || 'playwright');
const repo = path.resolve(__dirname, '../../..');
const build = path.join(repo, 'apps/web/dist');
const out = process.env.ECHOSPEAK_WORKBENCH_ARTIFACT_DIR || path.join(repo, '.test-state/workbench-browser');
fs.mkdirSync(out, { recursive: true });
const mime = { '.html':'text/html', '.js':'application/javascript', '.css':'text/css', '.png':'image/png', '.svg':'image/svg+xml', '.woff':'font/woff', '.woff2':'font/woff2', '.ttf':'font/ttf' };
const server = http.createServer((req,res) => {
  let file = path.resolve(build, '.' + decodeURIComponent(new URL(req.url, 'http://local').pathname));
  if (!file.startsWith(build + path.sep) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(build, 'index.html');
  res.writeHead(200, { 'Content-Type':mime[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
});
const project = { id:'fixture-project', name:'Echo Code sample', workspace_root:'C:\\Projects\\sample', trust_state:'trusted', archived:false };
const runtimes = {schema_version:1,privacy_mode:'standard',runtimes:[
  {id:'echo',name:'Echo native',protocol:'native',implemented:true,installed:true,authentication:'existing_model_settings',can_start:true,blockers:[],description:"Echo's own tools, skills, memory, learning, and approval policies. Choose a local or cloud model in the conversation."},
  {id:'codex',name:'Codex',protocol:'codex-app-server',implemented:true,installed:true,authentication:'unverified',can_start:false,blockers:['Fixture account is not connected.'],description:'Use the official app-server and Codex-owned sign-in. ChatGPT access depends on your account and usage limits.'},
  {id:'claude_code',name:'Claude Code',protocol:'claude-agent-sdk',implemented:true,installed:true,authentication:'unverified',can_start:false,blockers:['Fixture account is not connected.','Fixture API authentication is unavailable.'],description:'Run the original Claude Code binary with its own sign-in.'},
  {id:'acp',name:'Other ACP agents',protocol:'acp',implemented:true,installed:null,authentication:'unverified',can_start:false,blockers:['Fixture account is not connected.'],description:'Connect compatible agents through their own ACP sessions and capability negotiation.'}
]};
let terminalCounter=0;const terminalBuffers={};const writes=[];const providerSwitches=[];const harnessTurns=[];let reportedModel="fixture-gpt-a";let applied=0;let privacyMode='standard';
const summary={name:'Echo Code sample',root:'C:/Fixture/sample',source_root:'C:/Fixture/sample',runtime:'echo',computer:'FIXTURE-PC',system:'Windows',git:{is_git:true,branch:'codex/workbench',head:'1234567890abcdef',repository:'fixture/sample',branches:['main','codex/workbench'],changes:[{path:'src/app.ts',status:' M'}]},scripts:[{id:'dev',name:'Dev server',command:'npm run dev'}]};
(async()=>{
  await new Promise(resolve=>server.listen(5193,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,...(process.env.ECHOSPEAK_CHROMIUM_PATH ? {executablePath:process.env.ECHOSPEAK_CHROMIUM_PATH}:{}),args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']});
  const report = {fixture_data:true,real_models:false,checks:[],page_errors:[],unexpected_network:[]};
  try {
    const context = await browser.newContext({viewport:{width:1400,height:960},reducedMotion:'reduce'});
    const page = await context.newPage();
    page.on('pageerror',error=>report.page_errors.push(error.message));
    let sessions = [{thread_id:'ordinary-chat',title:'General chat',last_active_at:1,created_at:1,message_count:2,project_id:'',surface:'chat',agent_runtime:'echo'}];
    let submissions = []; let currentProvider="ollama", currentModel="fixture-model";
    await page.addInitScript(()=>{if(window.top!==window)return;localStorage.setItem('echospeak.theme','dark');localStorage.setItem('echospeak.active_thread_id','ordinary-chat');});
    await page.addInitScript(()=>{
      if(window.top!==window)return;window.__TAURI__={core:{invoke:async(command,args)=>{
        if(command==='desktop_window_label') return 'main';
        if(command==='desktop_runtime') return {environment:'desktop',api_base:'http://localhost:8000',api_session_key:'fixture-only',backend_phase:'ready',backend_detail:'Ready',connection_generation:1,instance_id:'fixture',restart_count:0,max_automatic_restarts:3,data_dir:'C:\\Fixture',log_dir:'C:\\Fixture'};
        if(command==='check_for_update') return {configured:false,available:false};
        return null;
      }},event:{listen:async()=>()=>{},emit:async()=>{}}};
    });
    await page.routeWebSocket('ws://localhost:8000/**', ws=>{
      if(!ws.url().includes('/workbench/')) return ws.send(JSON.stringify({type:'gateway_ready',at:1}));
      const id=new URL(ws.url()).pathname.split('/').at(-2);const send=rows=>ws.send(JSON.stringify({id,state:'running',cursor:terminalBuffers[id].length,gap:false,output:rows}));
      send(terminalBuffers[id]||[]);ws.onMessage(raw=>{const data=JSON.parse(raw);if(data.operation==='write'){writes.push({...data,project_id:'fixture-project'});const row={seq:terminalBuffers[id].length+1,text:data.text};terminalBuffers[id].push(row);send([row]);}});
    });
    await page.route('**/*',async route=>{
      const request = route.request(); const url = new URL(request.url());
      if (url.hostname==='127.0.0.1'&&url.port==='3000') return route.fulfill({contentType:'text/html',body:'<html><body>Fixture dev preview<script>try {window.parent.__TAURI__;document.body.dataset.blocked="false"} catch {document.body.dataset.blocked="true"}</script></body></html>'});
      if (url.port === '5193' && url.hostname === '127.0.0.1') return route.continue();
      if (url.hostname !== 'localhost' || url.port !== '8000') { report.unexpected_network.push(url.origin); return route.fulfill({status:200,body:''}); }
      const p = url.pathname; let data = {};
      if (request.method()==='OPTIONS') return route.fulfill({status:204,headers:{'access-control-allow-origin':'*','access-control-allow-headers':'*','access-control-allow-methods':'*'}});
      if (p === '/privacy/status') data={mode:privacyMode};
      else if(p==='/workbench/fixture-project/summary') data={...summary,runtime:url.searchParams.get('external_session_id')?'codex':'echo'};
      else if(p==='/echo-code/projects/fixture-project/external-sessions') data=[{id:'fixture-owned-session',runtime_id:'codex',created_at:1}];
      else if(p==='/echo-code/external-sessions/fixture-owned-session') data={id:'fixture-owned-session',state:'completed',connected:true,model:reportedModel,events:harnessTurns.length && Number(url.searchParams.get('cursor')||0)<1 ? [{seq:1,kind:'item/agentMessage/delta',data:{delta:'Fixture streamed coding reply'}}]:[],approvals:[],models:[{id:'a',model:'fixture-gpt-a',displayName:'Fixture GPT A',isDefault:true},{id:'b',model:'fixture-gpt-b',displayName:'Fixture GPT B'}],cursor:harnessTurns.length?1:0,account:{connected:true,type:'fixture'}};
      else if(p==='/echo-code/external-sessions/fixture-owned-session/turns') {const body=request.postDataJSON();harnessTurns.push({path:p,...body});reportedModel=body.model;data={accepted:true};}
      else if(p==='/workbench/fixture-project/files') data={items:url.searchParams.get('path')==='src'?[{name:'app.ts',path:'src/app.ts',directory:false}]:[{name:'src',path:'src',directory:true},{name:'docs',path:'docs',directory:true},{name:'README.md',path:'README.md',directory:false}]};
      else if(p==='/workbench/fixture-project/file') data={path:'src/app.ts',text:'export const welcome = "Hello, EchoSpeak";\n// Fixture source preview. No repository was modified.'};
      else if(p==='/workbench/fixture-project/diff') data={files:summary.git.changes,diff:'diff --git a/src/app.ts b/src/app.ts\n@@ -1 +1 @@\n-export const welcome = "Hello";\n+export const welcome = "Hello, EchoSpeak";',fingerprint:'fixture',head:summary.git.head};
      else if(p==='/workbench/fixture-project/pull-requests') data={available:true,items:[{number:96,title:'Integrate native coding runtimes',url:'https://github.com/fixture/sample/pull/96',state:'OPEN',isDraft:true,headRefName:'codex/workbench',baseRefName:'main'}]};
      else if(p==='/workbench/fixture-project/usage') data={runtimes:[{runtime:'codex',session_id:'fixture-session',limits:{rateLimits:{primary:{usedPercent:22,resetsAt:1793000000},secondary:{usedPercent:45,resetsAt:1793300000}}}}],note:'Fixture usage values; no connected account was queried.'};
      else if(p==='/workbench/terminals'&&request.method()==='POST') {const id='term-'+(++terminalCounter);terminalBuffers[id]=[{seq:1,text:'Windows PowerShell\r\nPS C:\\Fixture\\sample> '}];data={id,state:'running',cursor:0,output:[]};}
      else if(p.startsWith('/workbench/terminals/')) {const body=request.postDataJSON();const id=p.split('/').pop();if(body.operation==='write'){writes.push(body);terminalBuffers[id].push({seq:terminalBuffers[id].length+1,text:body.text});} data={id,state:body.operation==='stop'?'stopped':'running'};}
      else if(p.startsWith('/workbench/fixture-project/terminals/')) {const id=p.split('/').pop();const rows=terminalBuffers[id]||[];data={id,state:'running',cursor:rows.length,gap:false,output:rows.filter(row=>row.seq>Number(url.searchParams.get('cursor')||0))};}
      else if(p==='/workbench/git/review') {const body=request.postDataJSON();data={...body,review_id:'fixture-review',workspace:summary.root,head:summary.git.head,remote:''};}
      else if(p==='/workbench/git/apply') {applied++;data={output:'Fixture commit complete'};}
      else if(p==='/workbench/editor') data={opened:true};
      else if (p === '/projects') data={items:[project]};
      else if (p === '/threads' && request.method()==='GET') data=sessions;
      else if (p === '/echo-code/runtimes') data=runtimes;
      else if (p === '/echo-code/projects/fixture-project') data={schema_version:1,project,git_summary:'git: branch feature/compiler · 2 changed files',sessions:sessions.filter(s=>s.surface==='code'),can_start_native:true,native_blocker:''};
      else if (p === '/echo-code/projects/fixture-project/sessions') {
        assert.equal(request.postDataJSON().runtime_id,'echo');
        data={thread_id:'native-code',title:'Code · Echo Code sample',created_at:2,last_active_at:2,message_count:0,project_id:project.id,surface:'code',agent_runtime:'echo'};
        if (!sessions.some(s=>s.thread_id===data.thread_id)) sessions=[data,...sessions];
      }
      else if (p.endsWith('/workspace/files')) data={items:url.searchParams.get('path') ? [{name:'main.py',path:'src/main.py',directory:false}] : [{name:'src',path:'src',directory:true},{name:'README.md',path:'README.md',directory:false}]};
      else if (p.endsWith('/workspace/file')) data={path:url.searchParams.get('path'),text:'# Sample compiler\n\nDeterministic UI fixture. No live model was invoked.'};
      else if (p === '/startup/readiness') data={protocol_version:'1',runtime_schema_version:1,core_ready:true,status:'Ready',completed_steps:1,total_steps:1,data_root:'fixture',active_project_id:'',active_session_id:'ordinary-chat',components:[],runtime_kind:'desktop',instance_id:'fixture'};
      else if (p === '/provider') data={provider:currentProvider,model:currentModel,ready:true,base_url:'http://localhost:11434',context_window:32768,available_providers:[{id:'ollama',name:'Ollama',local:true},{id:'lmstudio',name:'LM Studio',local:true},{id:'openai',name:'OpenAI',local:false},{id:'anthropic',name:'Anthropic',local:false},{id:'llama_cpp',name:'Built-in local',local:true}],model_profile:{thinking_controls:{supported:true,effort:true,toggle:true}}};
      else if (p === '/provider/models') { const provider=url.searchParams.get('provider'); data={provider,reachable:!['anthropic','llama_cpp'].includes(provider),models:provider==='llama_cpp'?[]:provider==='lmstudio'?['google/gemma-4-e4b']:provider==='openai'?['gpt-fixture-api']:provider==='anthropic'?['disconnected-model-must-not-appear']:['fixture-model','fixture-coder'],saved_model:provider===currentProvider?currentModel:''}; }
      else if (p === '/provider/switch') { const body=request.postDataJSON();providerSwitches.push(body);currentProvider=body.provider;currentModel=body.model;data={success:true}; }
      else if (p === '/onboarding') data={show:false,step:5};
      else if (p === '/health/capabilities') data={status:'ok',checks:[],issues:[],ready:true};
      else if (p === '/query/stream') {
        submissions.push(request.postDataJSON());
        return route.fulfill({status:200,contentType:'application/x-ndjson',headers:{'access-control-allow-origin':'*'},body:JSON.stringify({type:'final',success:true,response:'Fixture response. No model invoked.',runtime:'lean',messages:[],memory_count:0,thread_state:{thread_id:'native-code',active_project_id:project.id,mode:'coding',updated_at:2}})+'\n'});
      }
      else if (p.match(/^\/threads\/[^/]+\/state$/)) data={thread_id:p.split('/')[2],active_project_id:p.includes('native-code')?project.id:'',workspace_root:p.includes('native-code')?project.workspace_root:'',updated_at:2};
      else if (p === '/history') data={history:[],turns:[],session:{}};
      else if (p === '/lean/agents') data={items:[{id:'echo',name:'Echo',title:'Personal agent',description:'Native Echo',toolsets:[]}]};
      else if (p === '/lean/rooms') data={items:[]};
      else if (p === '/lean/toolsets') data={toolsets:[]};
      else if (p === '/query/queue') data={queue:[],paused:false};
      else if (p === '/health') data={status:'healthy'};
      else data={items:[],notifications:[],checks:[],profiles:[],settings:{},paused:false,capabilities:[],turns:[]};
      return route.fulfill({status:200,contentType:'application/json',headers:{'access-control-allow-origin':'*'},body:JSON.stringify(data)});
    });
    await page.addInitScript(()=>{if(window.top===window)Object.freeze(Object.prototype);});
    await page.goto('http://127.0.0.1:5193/app');
    await page.getByRole('heading',{name:'What can I help you with?',exact:true}).waitFor();
    await page.locator('.desktop-boot-state').waitFor({state:'detached'});
    for(const name of ['Workspace details','Toggle bottom terminal','Toggle workspace sidebar']) assert.equal(await page.getByRole('button',{name,exact:true}).count(),1);
    const alignment=[];
    for(const size of [{width:1354,height:801},{width:1400,height:960}]) {
      await page.setViewportSize(size);
      const chat=await page.evaluate(()=>({heading:document.querySelector('.conversation-welcome h1').getBoundingClientRect().y,composer:document.querySelector('.composer-card').getBoundingClientRect().y}));
      await page.getByRole('switch',{name:'Echo Code',exact:true}).click();
      await page.getByRole('heading',{name:/What should we build/}).waitFor();
      const code=await page.evaluate(()=>({heading:document.querySelector('.ec-welcome h2').getBoundingClientRect().y,composer:document.querySelector('.ec-draft-card').getBoundingClientRect().y}));
      assert.ok(Math.abs(chat.heading-code.heading)<2,JSON.stringify({size,chat,code}));
      assert.ok(Math.abs(chat.composer-code.composer)<2,JSON.stringify({size,chat,code}));
      alignment.push({size,heading_delta:chat.heading-code.heading,composer_delta:chat.composer-code.composer});
      await page.getByRole('switch',{name:'Echo Code',exact:true}).click();
      await page.getByRole('heading',{name:'What can I help you with?',exact:true}).waitFor();
    }
    report.chat_code_alignment=alignment;
    await page.screenshot({path:path.join(out,'chat-centered-desktop.png'),fullPage:true});
    const chatMetrics=await page.evaluate(()=>{const r=document.querySelector('.input-bar').getBoundingClientRect(),card=document.querySelector('.composer-card').getBoundingClientRect(),c=document.querySelector('.desktop-chat-workspace').getBoundingClientRect();return {offset:Math.abs((r.left+r.right)/2-(c.left+c.right)/2),width:card.width,centerY:(r.top+r.bottom)/2,columnCenterY:(c.top+c.bottom)/2}});
    assert.ok(chatMetrics.offset<3,JSON.stringify(chatMetrics));assert.ok(chatMetrics.width<=766,JSON.stringify(chatMetrics));
    await page.getByRole('switch',{name:'Echo Code',exact:true}).click();
    await page.getByLabel('Coding task draft',{exact:true}).fill('Keep this draft while switching agents');
    const starter=await page.locator('.ec-welcome').boundingBox();
    for(const [name,id] of [['Codex','codex'],['Claude Code','claude_code'],['Other ACP agents','acp']]) {
      await page.getByRole('button',{name:'Agent runtime',exact:true}).click();
      await page.getByRole('button',{name:`${name} models`,exact:true}).click();
      await page.keyboard.press('Escape');
      assert.equal(await page.getByLabel('Coding task draft',{exact:true}).inputValue(),'Keep this draft while switching agents');
      assert.ok(await page.getByLabel('Coding task draft',{exact:true}).isVisible());
      assert.ok(Math.abs((await page.locator('.ec-welcome').boundingBox()).y-starter.y)<2);
      assert.equal(await page.getByRole('button',{name:'Coding agent connection',exact:true}).isDisabled(),true);
      await page.screenshot({path:path.join(out,`${id}-disconnected-composer.png`),fullPage:true});
    }
    await page.getByRole('button',{name:'Agent runtime',exact:true}).click();
    await page.getByRole('button',{name:'Echo native models',exact:true}).click();
    await page.getByRole('button',{name:/^fixture-coder/}).waitFor();
    await page.screenshot({path:path.join(out,'code-model-picker-native.png'),fullPage:true});
    await page.getByRole('button',{name:/^fixture-coder/}).click();
    assert.equal(providerSwitches.length,0);
    assert.ok((await page.getByRole('button',{name:'Agent runtime',exact:true}).getAttribute('title')).includes('fixture-coder'));
    await page.getByLabel('Coding task draft',{exact:true}).fill('');
    await page.getByLabel('Coding project',{exact:true}).selectOption('fixture-project');
    await page.getByRole('heading',{name:/What should we build/}).waitFor();
    await page.waitForTimeout(200);
    await page.screenshot({path:path.join(out,'code-centered-desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'Agent runtime',exact:true}).click();
    await page.getByRole('button',{name:'Codex models',exact:true}).click();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Coding agent connection',exact:true}).click();
    await page.getByLabel('External coding session',{exact:true}).selectOption('fixture-owned-session');
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Workspace details',exact:true}).click();
    await page.getByRole('dialog',{name:'Workspace details'}).getByText('Codex',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Agent runtime',exact:true}).click();
    await page.getByRole('button',{name:/^Fixture GPT B/}).waitFor();
    await page.screenshot({path:path.join(out,'code-model-picker-codex.png'),fullPage:true});
    await page.getByRole('button',{name:/^Fixture GPT B/}).click();
    await page.getByLabel('Coding task draft',{exact:true}).fill('A scoped fixture task');
    await page.getByRole('button',{name:'Send to Codex',exact:true}).click();
    await page.waitForTimeout(100);
    assert.equal(harnessTurns.length,1);
    assert.equal(harnessTurns[0].model,'fixture-gpt-b');
    assert.equal(harnessTurns[0].text,'A scoped fixture task');
    await page.getByRole('log',{name:'Coding conversation',exact:true}).getByText('Fixture streamed coding reply',{exact:true}).waitFor();
    await page.screenshot({path:path.join(out,'code-harness-conversation.png'),fullPage:true});

    await page.getByRole('button',{name:'Agent runtime',exact:true}).click();
    await page.getByRole('button',{name:'Echo native models',exact:true}).click();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Workspace details',exact:true}).click();
    await page.getByRole('dialog',{name:'Workspace details'}).getByText('Echo native',{exact:true}).waitFor();
    await page.getByRole('dialog',{name:'Workspace details'}).getByText('FIXTURE-PC').waitFor();
    await page.screenshot({path:path.join(out,'workspace-details.png'),fullPage:true});
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Toggle workspace sidebar',exact:true}).click();
    await page.getByRole('button',{name:'Files F',exact:false}).click();
    await page.getByRole('treeitem',{name:/src/}).click();
    await page.getByRole('treeitem',{name:/app.ts/}).waitFor();
    await page.screenshot({path:path.join(out,'files-tree-desktop.png'),fullPage:true});
    await page.getByRole('treeitem',{name:/app.ts/}).click();
    await page.getByText('export const welcome = "Hello, EchoSpeak";',{exact:false}).waitFor();
    await page.getByRole('button',{name:'Toggle bottom terminal',exact:true}).click();
    await page.getByRole('button',{name:'Open PowerShell',exact:true}).click();
    await page.locator('.xterm-helper-textarea').first().focus();await page.keyboard.type('Get-Location');await page.keyboard.press('Enter');
    await page.waitForTimeout(300);assert.ok(writes.some(row=>row.text==='G'));
    await page.getByRole('button',{name:'New terminal',exact:true}).click();
    await page.getByRole('button',{name:'Split terminals',exact:true}).click();
    report.layout_metrics=await page.evaluate(()=>({rows:getComputedStyle(document.querySelector('.app-shell')).gridTemplateRows,inline:document.querySelector('.app-shell').getAttribute('style'),bottom:document.querySelector('.wb-terminal-dock').getBoundingClientRect().toJSON(),tabs:[...document.querySelectorAll('.wb-tab')].map(el=>({text:el.textContent,rect:el.getBoundingClientRect().toJSON(),display:getComputedStyle(el).display,span:[...el.querySelectorAll('span')].map(n=>({text:n.textContent,width:n.getBoundingClientRect().width,font:getComputedStyle(n).fontSize,display:getComputedStyle(n).display}))}))}));
    await page.screenshot({path:path.join(out,'terminal-split-desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'Hide terminal',exact:true}).click();
    await page.getByRole('button',{name:'Agent runtime',exact:true}).click();
    await page.getByRole('button',{name:'Codex models',exact:true}).click();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Coding agent connection',exact:true}).click();
    await page.getByLabel('External coding session',{exact:true}).selectOption('fixture-owned-session');
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Workspace details',exact:true}).click();
    await page.getByRole('dialog',{name:'Workspace details'}).getByText('Codex',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Agent runtime',exact:true}).click();
    await page.getByRole('button',{name:'Echo native models',exact:true}).click();
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'Workspace details',exact:true}).click();
    await page.getByRole('dialog',{name:'Workspace details'}).getByText('Echo native',{exact:true}).waitFor();
    await page.getByRole('dialog',{name:'Workspace details'}).getByRole('button',{name:/Commit & push/}).click();await page.keyboard.press('Escape');
    await page.getByRole('checkbox').filter({visible:true}).first().check();
    await page.getByPlaceholder('Describe the selected changes').fill('Update welcome message');
    await page.getByRole('button',{name:'Review commit',exact:true}).click();
    await page.getByRole('group',{name:'Confirm Git action'}).waitFor();assert.equal(applied,0);
    await page.screenshot({path:path.join(out,'changes-review-desktop.png'),fullPage:true});
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    const surface=async name=>{await page.getByRole('button',{name:'Open a surface',exact:true}).click();await page.getByRole('dialog',{name:'Open a surface',exact:true}).getByRole('button',{name}).click();};
    await surface('Pull requests P');await page.getByRole('link',{name:/#96 Integrate native coding runtimes/}).waitFor();
    await page.screenshot({path:path.join(out,'pull-requests-desktop.png'),fullPage:true});
    await surface('Usage U');await page.getByText('78% left',{exact:true}).waitFor();
    await page.screenshot({path:path.join(out,'usage-fixture-desktop.png'),fullPage:true});
    await surface('Browser B');await page.getByRole('button',{name:'Open',exact:true}).waitFor();
    await page.getByRole('button',{name:'Open',exact:true}).click();
    const iframe=page.getByTitle('Isolated project preview');await iframe.waitFor();
    await page.waitForTimeout(150);const frame=await (await iframe.elementHandle()).contentFrame();
    assert.equal(await frame.evaluate(()=>document.body.dataset.blocked),'true');
    assert.equal(await frame.evaluate(()=>typeof window.__TAURI__),'undefined');
    assert.equal(await iframe.getAttribute('sandbox'),'allow-scripts');
    privacyMode='private';await iframe.waitFor({state:'detached'});privacyMode='standard';
    await page.getByRole('button',{name:'New native coding session',exact:true}).click();
    await page.getByLabel('Message',{exact:true}).waitFor();
    assert.equal(providerSwitches.length,1);
    assert.equal(providerSwitches[0].session_id,'native-code');
    assert.equal(providerSwitches[0].model,'fixture-coder');
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(out,'workbench-narrow.png'),fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.equal(submissions.length,0);
    assert.deepEqual(report.page_errors,[]);assert.deepEqual(report.unexpected_network,[]);
    report.checks=['Three titlebar controls render','Chat composer centers in its actual content column (same bounded width as Code at desktop scale)','Code welcome and composer share the centered layout','Details show selected workspace and Git branch','File tree expands and file tabs preview source as text','Interactive terminal input is sent only to its scoped terminal API','Two terminal panes split and survive hiding','Git mutation requires a separate review and confirmation','PR list renders branch and draft state','Usage renders runtime values rather than invented zero limits','Loopback preview is opaque, cannot access parent bridge and clears on privacy update','Narrow page has no horizontal overflow','Workbench never invokes native model query transport','New Chat title and composer align with unchanged Code at both desktop sizes','Returning from an external session resets details and resources to the native source workspace','All disconnected agents retain the same centered welcome and unsent draft','Code model picker uses Chat rows and only reported or connected models','Model selection does not mutate another Chat session','Native Code selection binds only the newly created coding session','An explicit send reaches only the chosen original harness with its selected model','Original harness replies remain visible in the main coding conversation'];
    report.chat_metrics=chatMetrics;
    fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});

