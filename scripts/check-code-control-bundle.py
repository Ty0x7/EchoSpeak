"""Exercise the compiled stdio entry against an owned loopback HTTP peer."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / 'apps/desktop/src-tauri/backend-dist/echospeak-backend.exe'
VERSION = json.loads((ROOT / 'apps/desktop/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))['version']
calls = []

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_POST(self):
        assert self.path == '/echo-code/control/invoke'
        assert self.headers.get('Authorization') == 'Bearer disposable-packaged-fixture'
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        calls.append(data['action'])
        status, payload = (200, {'projects':[{'id':'fixture','name':f'Packaged {VERSION} fixture'}], 'agents':[]}) if data['action'] == 'projects' else (403, {'detail':'Fixture is read only'})
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

async def run(port):
    params = StdioServerParameters(command=str(EXE), args=['--control-mcp'], env={
        **os.environ, 'ECHOSPEAK_CONTROL_URL':f'http://127.0.0.1:{port}',
        'ECHOSPEAK_CONTROL_TOKEN':'disposable-packaged-fixture',
        'ECHOSPEAK_DATA_DIR':str(ROOT/'.test-state/packaged-mcp-data'),
        'ECHOSPEAK_TESTING':'1', 'MCP_SERVERS':'{}',
    })
    async with stdio_client(params) as (reader,writer):
        async with ClientSession(reader,writer) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert len(names) == 8 and {'echo_projects','echo_thread_send','echo_thread_stop'} <= names, names
            result = await session.call_tool('echo_projects',{})
            assert not result.is_error and f'Packaged {VERSION} fixture' in str(result.content)
            denied = await session.call_tool('echo_thread_send',{'id':'fixture','message':'do not run','request_id':'fixture-packaged'})
            assert denied.is_error

server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
worker = threading.Thread(target=server.serve_forever,daemon=True); worker.start()
try:
    asyncio.run(asyncio.wait_for(run(server.server_port), timeout=75))
    assert calls == ['projects','send'], calls
    result={'version':VERSION,'status':'passed','transport':'compiled backend --control-mcp; real SDK stdio initialize/list/call; owned loopback peer',
            'tools':8,'calls':calls,'denied_mutation':True,'provider_calls':0,
            'scope':'Packaging/transport only. Source tests separately cover real grants and original harness dispatch.'}
    (ROOT/'.test-state/packaged-mcp-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
finally:
    server.shutdown(); server.server_close(); worker.join(2)
