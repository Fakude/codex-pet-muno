"""
Phone and tablet access for Muno.

Muno's hands are on this PC: it opens local apps, reads local files, and drives
the local browser. None of that can run on a phone, so a phone cannot *be* Muno.
What it can be is a remote control. This module serves a small web app that talks
to the running desktop companion, which keeps doing the actual work.

Because it is a web app rather than a native one, it works on iPhone, iPad, and
Android with nothing to install. On iOS, Share then "Add to Home Screen" gives it
an icon and a full-screen shell like a normal app.

Security
--------
This endpoint can send mail as the user, change repositories, and drive the
desktop, so it is not something to leave open. Therefore:

* A token is always required. There is no unauthenticated mode.
* The token is compared in constant time, and repeated failures from an address
  are throttled.
* It binds to the local network only unless explicitly told otherwise.
* Actions that already required confirmation still require it. The prompt is
  relayed to whichever surface is watching, so approving from the phone is a
  deliberate tap rather than a bypass.
"""
import hmac
import json
import os
import queue
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONFIG_DIR = os.path.expanduser("~/.codex_pet")
TOKEN_FILE = os.path.join(CONFIG_DIR, "mobile_token.txt")

DEFAULT_PORT = int(os.getenv("MUNO_MOBILE_PORT", "8765"))
# Binding to every interface is opt-in: on an untrusted network that would offer
# desktop control to anything that can reach this machine.
BIND_ALL = (os.getenv("MUNO_MOBILE_BIND_ALL") or "0").strip().lower() in {"1", "true", "yes"}

MAX_FAILED_ATTEMPTS = 8
LOCKOUT_SECONDS = 300
MAX_MESSAGES = 200


def local_ip():
    """Best guess at the address a phone on the same network should use."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def load_or_create_token():
    """Return the pairing token, creating one on first use."""
    override = (os.getenv("MUNO_MOBILE_TOKEN") or "").strip()
    if override:
        return override
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as handle:
            existing = handle.read().strip()
        if existing:
            return existing
    except OSError:
        pass

    token = secrets.token_urlsafe(24)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as handle:
        handle.write(token)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


class MobileBridge:
    """Thread-safe hand-off between the web server and the Qt application.

    The server runs on its own threads and must never touch widgets directly, so
    requests are queued for the UI thread to drain and state is published here
    for the server to read.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.incoming = queue.Queue()
        self._messages = []
        self._sequence = 0
        self._status = "Ready"
        self._busy = False
        self._pending_approval = None
        self._approval_sequence = 0

    # -- published state (written by the app, read by the server) ----------

    def publish_message(self, role, text, kind="text"):
        """Record something the phone should see: a reply, a step, a summary."""
        with self._lock:
            self._sequence += 1
            self._messages.append({
                "id": self._sequence,
                "role": role,
                "kind": kind,
                "text": str(text or ""),
                "at": time.time(),
            })
            if len(self._messages) > MAX_MESSAGES:
                del self._messages[:-MAX_MESSAGES]

    def set_status(self, status, busy=None):
        with self._lock:
            self._status = str(status or "")
            if busy is not None:
                self._busy = bool(busy)

    def snapshot(self, since=0):
        with self._lock:
            messages = [m for m in self._messages if m["id"] > since]
            approval = dict(self._pending_approval) if self._pending_approval else None
            if approval:
                approval.pop("decision", None)
                approval.pop("event", None)
            return {
                "status": self._status,
                "busy": self._busy,
                "messages": messages,
                "latest": self._sequence,
                "approval": approval,
            }

    # -- approvals ---------------------------------------------------------

    def open_approval(self, title, detail, decision, event):
        """Publish a confirmation prompt and return its id."""
        with self._lock:
            self._approval_sequence += 1
            identifier = self._approval_sequence
            self._pending_approval = {
                "id": identifier,
                "title": str(title or "Allow this action?"),
                "detail": str(detail or ""),
                "decision": decision,
                "event": event,
            }
        return identifier

    def resolve_approval(self, identifier, approved):
        """Answer a pending prompt. Returns True when it matched."""
        with self._lock:
            pending = self._pending_approval
            if not pending or pending["id"] != int(identifier):
                return False
            self._pending_approval = None
        pending["decision"]["approved"] = bool(approved)
        pending["decision"]["source"] = "phone"
        try:
            pending["event"].set()
        except Exception:
            pass
        return True

    def clear_approval(self, identifier=None):
        """Drop a prompt that was answered somewhere else, such as on the desktop."""
        with self._lock:
            pending = self._pending_approval
            if not pending:
                return
            if identifier is None or pending["id"] == int(identifier):
                self._pending_approval = None

    # -- inbound requests --------------------------------------------------

    def submit(self, text):
        self.incoming.put(str(text or "").strip())

    def drain(self):
        items = []
        while True:
            try:
                items.append(self.incoming.get_nowait())
            except queue.Empty:
                break
        return items


BRIDGE = MobileBridge()


class _Handler(BaseHTTPRequestHandler):
    server_version = "Muno"
    sys_version = ""

    # Set by the factory below.
    token = ""
    bridge = None
    pet_name = "Muno"
    _failures = {}
    _failures_lock = threading.Lock()

    def log_message(self, format, *args):  # noqa: A002 - signature fixed by stdlib
        return  # Keep the console clean; the activity log is the record.

    # -- helpers -----------------------------------------------------------

    def _client(self):
        return self.client_address[0] if self.client_address else "?"

    def _locked_out(self):
        with self._failures_lock:
            record = _Handler._failures.get(self._client())
            if not record:
                return False
            count, first = record
            if time.time() - first > LOCKOUT_SECONDS:
                _Handler._failures.pop(self._client(), None)
                return False
            return count >= MAX_FAILED_ATTEMPTS

    def _note_failure(self):
        with self._failures_lock:
            count, first = _Handler._failures.get(self._client(), (0, time.time()))
            if time.time() - first > LOCKOUT_SECONDS:
                count, first = 0, time.time()
            _Handler._failures[self._client()] = (count + 1, first)

    def _supplied_token(self):
        header = self.headers.get("X-Muno-Token", "")
        if header:
            return header.strip()
        # A query parameter lets a phone open a single pairing link.
        _path, _, query = self.path.partition("?")
        for part in query.split("&"):
            key, _, value = part.partition("=")
            if key == "t":
                from urllib.parse import unquote

                return unquote(value).strip()
        return ""

    def _authorized(self):
        if self._locked_out():
            return False
        supplied = self._supplied_token()
        if supplied and hmac.compare_digest(supplied, self.token):
            return True
        if supplied:
            self._note_failure()
        return False

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        payload = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        # This is a control surface; never let another page frame or embed it.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status, data):
        self._send(status, json.dumps(data))

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 64_000:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            return {}

    # -- routes ------------------------------------------------------------

    def do_GET(self):
        route = self.path.split("?", 1)[0]

        if route == "/api/health":
            # Deliberately unauthenticated so pairing problems are diagnosable,
            # but it reveals nothing beyond the fact that Muno is listening.
            return self._json(200, {"ok": True, "name": self.pet_name})

        if route in ("/", "/index.html"):
            if not self._authorized():
                return self._send(401, _PAIRING_PAGE, "text/html; charset=utf-8")
            page = _APP_PAGE.replace("__TOKEN__", self._supplied_token()).replace(
                "__NAME__", self.pet_name
            )
            return self._send(200, page, "text/html; charset=utf-8")

        if route == "/manifest.webmanifest":
            return self._send(
                200, json.dumps(_MANIFEST), "application/manifest+json; charset=utf-8"
            )

        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})

        if route == "/api/state":
            since = 0
            _p, _, query = self.path.partition("?")
            for part in query.split("&"):
                key, _, value = part.partition("=")
                if key == "since":
                    try:
                        since = int(value)
                    except ValueError:
                        since = 0
            return self._json(200, self.bridge.snapshot(since))

        return self._json(404, {"error": "not found"})

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})

        if route == "/api/ask":
            payload = self._read_json()
            text = str(payload.get("text", "")).strip()
            if not text:
                return self._json(400, {"error": "empty"})
            if len(text) > 4000:
                text = text[:4000]
            self.bridge.publish_message("user", text)
            self.bridge.submit(text)
            return self._json(200, {"ok": True})

        if route == "/api/approve":
            payload = self._read_json()
            try:
                identifier = int(payload.get("id", 0))
            except (TypeError, ValueError):
                return self._json(400, {"error": "bad id"})
            approved = bool(payload.get("approved"))
            matched = self.bridge.resolve_approval(identifier, approved)
            return self._json(200 if matched else 409, {"ok": matched})

        return self._json(404, {"error": "not found"})


_MANIFEST = {
    "name": "Muno",
    "short_name": "Muno",
    "start_url": ".",
    "display": "standalone",
    # Matches the canvas so there is no flash of a different colour on launch.
    "background_color": "#000000",
    "theme_color": "#000000",
    "icons": [],
}


_PAIRING_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#000000">
<title>Muno</title>
<style>
 /* Same Apple tokens as the app: parchment canvas, one accent, generous space. */
 body{
   margin:0; min-height:100vh; padding:32px;
   background:#f5f5f7; color:#1d1d1f;
   font:400 17px/1.47 "SF Pro Text",system-ui,-apple-system,sans-serif;
   letter-spacing:-0.374px;
   display:flex; align-items:center; justify-content:center;
   -webkit-font-smoothing:antialiased;
 }
 .card{max-width:420px}
 h1{
   margin:0 0 17px;
   font:600 40px/1.1 "SF Pro Display",system-ui,-apple-system,sans-serif;
   letter-spacing:-0.28px;
 }
 p{margin:0 0 17px; color:#333333}
 code{
   display:inline-block; background:#ffffff; color:#0066cc;
   padding:8px 14px; border-radius:11px;
   font:400 14px/1.43 ui-monospace,SFMono-Regular,Menlo,monospace;
   letter-spacing:-0.224px;
 }
</style></head>
<body><div class="card">
<h1>Pairing needed</h1>
<p>Open Muno on your PC, choose <strong>Connect Phone</strong>, and scan the code
it shows you.</p>
<p>The link looks like this:</p>
<code>http://192.168.1.5:8765/?t=…</code>
</div></body></html>"""


_APP_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="__NAME__">
<meta name="theme-color" content="#000000">
<link rel="manifest" href="/manifest.webmanifest">
<title>__NAME__</title>
<style>
 /* Design tokens from the Apple DESIGN.md in VoltAgent/awesome-design-md (MIT).
    The dark treatment uses that spec's own on-dark values, so the single Action
    Blue accent, the SF Pro stack with negative letter-spacing, the pill controls
    and the hairline rules are all as specified. Chrome carries no shadows and no
    decorative gradients: only content is allowed to stand out. */
 :root{
   --action:#0066cc; --action-focus:#0071e3; --action-on-dark:#2997ff;
   --ink:#1d1d1f; --on-dark:#ffffff; --body-muted:#cccccc;
   --ink-muted-48:#7a7a7a;
   --canvas:#000000; --tile:#1c1c1e; --tile-2:#272729;
   --hairline:rgba(255,255,255,.10);
   --sf-display:"SF Pro Display",system-ui,-apple-system,BlinkMacSystemFont,sans-serif;
   --sf-text:"SF Pro Text",system-ui,-apple-system,BlinkMacSystemFont,sans-serif;
   --r-xs:5px; --r-sm:8px; --r-md:11px; --r-lg:18px; --r-pill:9999px;
   --s-xxs:4px; --s-xs:8px; --s-sm:12px; --s-md:17px; --s-lg:24px; --s-xl:32px;
 }
 *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
 html,body{margin:0;height:100%}
 body{
   background:var(--canvas); color:var(--on-dark);
   font:400 17px/1.47 var(--sf-text); letter-spacing:-0.374px;
   display:flex; flex-direction:column;
   padding:env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
   -webkit-font-smoothing:antialiased;
 }

 /* Global nav: 44px, black, hairline rule. No shadow. */
 header{
   flex:0 0 auto; height:44px; min-height:44px;
   padding:0 var(--s-md); display:flex; align-items:center; gap:var(--s-sm);
   background:var(--canvas); border-bottom:1px solid var(--hairline);
 }
 header b{
   font:600 21px/1.19 var(--sf-display); letter-spacing:0.231px;
 }
 #status{
   margin-left:auto; text-align:right; color:var(--ink-muted-48);
   font:400 12px/1.0 var(--sf-text); letter-spacing:-0.12px;
 }

 #feed{
   flex:1 1 auto; overflow-y:auto; -webkit-overflow-scrolling:touch;
   padding:var(--s-lg) var(--s-md) var(--s-xs);
 }
 .row{margin:0 0 var(--s-sm); display:flex}
 .row.user{justify-content:flex-end}

 /* Bubbles are the content, so they get the colour and the weight. */
 .bub{
   max-width:80%; padding:11px 17px; border-radius:var(--r-lg);
   white-space:pre-wrap; word-wrap:break-word;
   font:400 17px/1.47 var(--sf-text); letter-spacing:-0.374px;
 }
 .user .bub{background:var(--action); color:#fff; border-bottom-right-radius:var(--r-xs)}
 .muno .bub{background:var(--tile); color:var(--on-dark); border-bottom-left-radius:var(--r-xs)}

 /* Progress and findings recede: caption scale, muted, no container. */
 .step .bub,.note .bub{
   background:none; padding:var(--s-xxs) var(--s-xs); max-width:100%;
   font:400 14px/1.43 var(--sf-text); letter-spacing:-0.224px;
 }
 .step .bub{color:var(--ink-muted-48)}
 .note .bub{color:var(--action-on-dark)}

 footer{
   flex:0 0 auto; display:flex; gap:var(--s-xs); align-items:flex-end;
   padding:var(--s-sm) var(--s-sm) var(--s-md);
   border-top:1px solid var(--hairline); background:var(--canvas);
 }
 textarea{
   flex:1; resize:none; max-height:120px;
   background:var(--tile); color:var(--on-dark);
   border:1px solid var(--hairline); border-radius:var(--r-pill);
   padding:11px var(--s-md);
   font:400 17px/1.35 var(--sf-text); letter-spacing:-0.374px;
 }
 textarea::placeholder{color:var(--ink-muted-48)}
 textarea:focus{outline:none; border-color:var(--action-focus)}

 /* Pill primary, per the button-primary token. */
 button{
   background:var(--action); color:#fff; border:0;
   border-radius:var(--r-pill); padding:11px 22px; min-height:44px;
   font:400 17px/1.24 var(--sf-text); letter-spacing:-0.374px;
 }
 button:active{background:var(--action-focus)}
 button:disabled{opacity:.36}

 /* Approval sheet, presented as a card on a dimmed canvas. */
 #ask{
   position:fixed; inset:0; display:none; z-index:9;
   align-items:flex-end; justify-content:center;
   background:rgba(0,0,0,.62);
   -webkit-backdrop-filter:saturate(180%) blur(20px);
   backdrop-filter:saturate(180%) blur(20px);
   padding:var(--s-md);
 }
 #ask .box{
   background:var(--tile-2); border-radius:var(--r-lg);
   padding:var(--s-lg); max-width:460px; width:100%;
 }
 #ask h3{
   margin:0 0 var(--s-xs);
   font:600 21px/1.19 var(--sf-display); letter-spacing:0.231px;
 }
 #ask pre{
   white-space:pre-wrap; word-wrap:break-word; color:var(--body-muted);
   font:400 14px/1.43 var(--sf-text); letter-spacing:-0.224px;
   margin:0 0 var(--s-lg); max-height:42vh; overflow:auto;
 }
 .btns{display:flex; gap:var(--s-sm)}
 .btns button{flex:1}
 .no{background:transparent; color:var(--action-on-dark); box-shadow:inset 0 0 0 1px var(--hairline)}
 .no:active{background:rgba(255,255,255,.06)}
</style></head>
<body>
<header><b>__NAME__</b><span id="status">connecting…</span></header>
<div id="feed"></div>
<footer>
  <textarea id="text" rows="1" placeholder="Tell __NAME__ what to do…"
    autocapitalize="sentences" autocomplete="off"></textarea>
  <button id="send">Send</button>
</footer>

<div id="ask"><div class="box">
  <h3 id="askTitle">Allow this?</h3>
  <pre id="askDetail"></pre>
  <div class="btns">
    <button class="no" id="deny">No</button>
    <button id="allow">Allow</button>
  </div>
</div></div>

<script>
const TOKEN = "__TOKEN__";
const feed = document.getElementById('feed');
const statusEl = document.getElementById('status');
const textEl = document.getElementById('text');
const sendEl = document.getElementById('send');
const askEl = document.getElementById('ask');
let since = 0, currentApproval = null, failures = 0;

const headers = {'Content-Type':'application/json','X-Muno-Token':TOKEN};

function bubble(role, kind, text){
  const cls = role === 'user' ? 'user' : (kind === 'step' ? 'step' : (kind === 'note' ? 'note' : 'muno'));
  const row = document.createElement('div');
  row.className = 'row ' + cls;
  const b = document.createElement('div');
  b.className = 'bub';
  b.textContent = (kind === 'step' ? '• ' : '') + text;
  row.appendChild(b);
  feed.appendChild(row);
  feed.scrollTop = feed.scrollHeight;
}

async function poll(){
  try{
    const r = await fetch('/api/state?since=' + since, {headers});
    if(r.status === 401){ statusEl.textContent = 'not paired'; return; }
    const d = await r.json();
    failures = 0;
    statusEl.textContent = d.busy ? (d.status || 'working…') : (d.status || 'ready');
    (d.messages || []).forEach(m => { bubble(m.role, m.kind, m.text); since = Math.max(since, m.id); });
    if(d.approval && (!currentApproval || currentApproval.id !== d.approval.id)){
      currentApproval = d.approval;
      document.getElementById('askTitle').textContent = d.approval.title;
      document.getElementById('askDetail').textContent = d.approval.detail;
      askEl.style.display = 'flex';
    } else if(!d.approval && currentApproval){
      currentApproval = null; askEl.style.display = 'none';
    }
  }catch(e){
    failures++;
    statusEl.textContent = failures > 2 ? 'offline' : 'reconnecting…';
  }
}

async function send(){
  const text = textEl.value.trim();
  if(!text) return;
  textEl.value = ''; textEl.style.height = 'auto';
  sendEl.disabled = true;
  try{ await fetch('/api/ask', {method:'POST', headers, body: JSON.stringify({text})}); }
  catch(e){ statusEl.textContent = 'send failed'; }
  sendEl.disabled = false;
  poll();
}

async function answer(ok){
  if(!currentApproval) return;
  const id = currentApproval.id;
  askEl.style.display = 'none'; currentApproval = null;
  try{ await fetch('/api/approve', {method:'POST', headers, body: JSON.stringify({id, approved: ok})}); }
  catch(e){}
  poll();
}

sendEl.addEventListener('click', send);
document.getElementById('allow').addEventListener('click', () => answer(true));
document.getElementById('deny').addEventListener('click', () => answer(false));
textEl.addEventListener('input', () => {
  textEl.style.height = 'auto';
  textEl.style.height = Math.min(textEl.scrollHeight, 110) + 'px';
});
textEl.addEventListener('keydown', e => {
  if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); send(); }
});

poll();
setInterval(poll, 1500);
</script>
</body></html>"""


class MobileServer:
    """Owns the HTTP server thread."""

    def __init__(self, bridge=None, pet_name="Muno", port=DEFAULT_PORT):
        self.bridge = bridge or BRIDGE
        self.pet_name = pet_name
        self.port = port
        self.token = load_or_create_token()
        self.host = "0.0.0.0" if BIND_ALL else local_ip()
        self._server = None
        self._thread = None
        self.error = None

    def url(self):
        address = local_ip() if self.host == "0.0.0.0" else self.host
        return f"http://{address}:{self.port}/?t={self.token}"

    def start(self):
        """Start serving. Returns True when listening."""
        if self._server:
            return True

        handler = type(
            "MunoHandler",
            (_Handler,),
            {"token": self.token, "bridge": self.bridge, "pet_name": self.pet_name},
        )
        try:
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            # A bound port usually means a second Muno, or something else here.
            self.error = str(exc)
            self._server = None
            return False

        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="muno-mobile", daemon=True
        )
        self._thread.start()
        return True

    def stop(self):
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self._thread = None

    def is_running(self):
        return self._server is not None
