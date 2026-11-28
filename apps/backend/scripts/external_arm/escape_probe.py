"""Runs INSIDE the sandbox. Every check except the model endpoint must be blocked. Prints one JSON line."""
import json, os, socket, urllib.request
r = {}
def attempt(name, fn):
    try:
        r[name] = {"ok": True, "detail": str(fn())[:120]}
    except Exception as exc:
        r[name] = {"ok": False, "detail": f"{type(exc).__name__}: {str(exc)[:100]}"}
real_home = os.environ.get("PROBE_REAL_HOME", "/home/dev")
attempt("read_real_home_ssh", lambda: os.listdir(real_home + "/.ssh"))
attempt("read_real_home", lambda: os.listdir(real_home))
attempt("read_echospeak_data", lambda: os.listdir(real_home + "/.local/share/ai.echospeak.desktop"))
attempt("read_run_user", lambda: os.listdir("/run/user"))
attempt("write_outside_workspace", lambda: open(real_home + "/sandbox-escape-test.txt", "w").write("x"))
attempt("dns_lookup", lambda: socket.getaddrinfo("github.com", 443))
attempt("tcp_internet", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
attempt("tcp_host_ollama", lambda: socket.create_connection(("127.0.0.1", 11434), timeout=3))
attempt("model_endpoint", lambda: json.load(urllib.request.urlopen("http://127.0.0.1:8095/v1/models", timeout=10))["data"][0]["id"])
attempt("write_workspace", lambda: open("/workspace/ok.txt", "w").write("ok"))
expected = {"model_endpoint": True, "write_workspace": True}
r["_verdict"] = all(r[k]["ok"] == expected.get(k, False) for k in r if not k.startswith("_"))
print(json.dumps(r))
