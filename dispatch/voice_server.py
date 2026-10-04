"""Local API behind the hands-free voice call, plus the shared store of caller conversations.

Streamlit can't serve custom routes, so the dashboard starts this tiny HTTP server once (127.0.0.1,
first free port from 8502). The voice widget in the browser talks to it:

  POST /turn {"call_id", "text"}  -> {"reply", "done", "source"}   one caller line in, agent reply out
  POST /tts  {"text"}             -> audio/mpeg (ElevenLabs), or 503 so the browser uses its own voice

Typed messages from the dashboard go through the same store (turn()), so one conversation can mix
voice and typing, and the page shows the same transcript, map and ticket either way.
"""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dispatch import intake, voice

_calls: dict[str, dict] = {}
_lock = threading.Lock()


def new_call(plan: dict, number: int) -> str:
    call_id = uuid.uuid4().hex[:12]
    with _lock:
        _calls[call_id] = {"history": [{"role": "assistant", "content": intake.OPENING}],
                           "state": intake.new_state(), "done": False, "ticket": None, "source": None,
                           "plan": plan, "n": number, "version": 0, "turn_lock": threading.Lock()}
    return call_id


def get(call_id: str) -> dict | None:
    """A snapshot of the call that the page can render (no locks, no plan)."""
    with _lock:
        call = _calls.get(call_id)
        if call is None:
            return None
        return {k: v for k, v in call.items() if k not in ("turn_lock", "plan")} | {
            "history": list(call["history"])}


def turn(call_id: str, text: str) -> dict:
    """One caller line -> the agent's reply. Turns on one call run one at a time."""
    with _lock:
        call = _calls.get(call_id)
    if call is None:
        raise KeyError("unknown call")
    with call["turn_lock"]:
        if call["done"]:
            return {"reply": "This report is already logged. Start a new call for another problem.",
                    "done": True, "source": call["source"]}
        call["history"].append({"role": "user", "content": text})
        out = intake.intake_turn(call["history"], call["state"])
        call["history"].append({"role": "assistant", "content": out["reply"]})
        call.update(done=out["done"], source=out["source"])
        if out["done"] and not call["ticket"]:
            call["ticket"] = intake.make_ticket(call["state"], call["plan"], call["n"])
        call["version"] += 1
        return out


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the Streamlit terminal quiet
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")  # the widget runs on the dashboard's origin
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._send(400, b'{"error": "bad json"}')
        text = str(body.get("text", "")).strip()[:2000]
        if self.path == "/turn":
            if not text:
                return self._send(400, b'{"error": "no text"}')
            try:
                out = turn(str(body.get("call_id", "")), text)
            except KeyError:
                return self._send(404, b'{"error": "unknown call"}')
            return self._send(200, json.dumps(out).encode())
        if self.path == "/tts":
            audio = voice.speak(text) if text else None
            if audio:
                return self._send(200, audio, "audio/mpeg")
            return self._send(503, json.dumps({"error": voice.last_error or "no text"}).encode())
        self._send(404, b'{"error": "not found"}')


def start(first_port: int = 8502, tries: int = 20) -> int:
    """Start the API on the first free port and return it (call once per process)."""
    for port in range(first_port, first_port + tries):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        except OSError:
            continue
        threading.Thread(target=server.serve_forever, daemon=True, name="voice-api").start()
        return port
    raise RuntimeError(f"no free port in {first_port}-{first_port + tries - 1}")
