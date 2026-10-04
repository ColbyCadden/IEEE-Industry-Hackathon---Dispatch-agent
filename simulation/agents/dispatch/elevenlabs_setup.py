#!/usr/bin/env python
"""
Configure the ElevenLabs Conversational AI agent so it runs the simulator's
incoming 311 calls, then keep it in sync with the live data.

The agent talks to the simulator through two webhooks on the sim server:
  POST /api/calls/tool   - the agent's system tools (list open issues, record an update)
Both are already served by calgary3d/server/server.py. The tools are declared as
ElevenLabs "server" tools pointing at the PUBLIC tunnel URL, because ElevenLabs
must be able to reach them; if you run the sim on localhost, use a tunnel
(ngrok / cloudflared) and pass --public-url.

Agent id and key come from simulation/.env (ELEVENLABS_AGENT_ID, ELEVENLABS_API_KEY)
or your Hermes settings.

    python -m agents.dispatch.elevenlabs_setup --public-url https://xxxx.ngrok.app
    python -m agents.dispatch.elevenlabs_setup --public-url ... --check-only
"""
import argparse, json, os, sys, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

API = "https://api.elevenlabs.io"

VOICE = "21m00Tcm4TlvDq8ikWAM"          # Rachel - warm, clear, low latency
PROMPT = """You are the 311 dispatch agent for the City of Calgary, working with the Roads and \
Waste crews. You are calm, warm, efficient and to the point - like a good dispatcher who has \
handled a hundred calls. You never rush and you never waffle.

Your job on every call:
1. Find out WHICH issue the caller means. Ask for the street and the house number if you do \
not have it. If several requests could match, read the short list back to the caller and ask \
which one. Never guess between two candidates.
2. Find out WHAT happened. Ask whether it is still there, or has been fixed. For a signal ask \
whether it is still broken; for a pothole whether it has been filled; for debris whether it has \
been picked up.
3. If the caller says it is still there AND it is dangerous, blocking traffic, or someone could \
be hurt, ask one short follow-up about the danger, then escalate it.
4. Record the outcome with your tools, then tell the caller what you did in one sentence - \
for example "I've marked that as cleared and the crews have been rerouted."

Style:
- Speak in short sentences. One question at a time. Wait for the answer.
- Numbers and street names: say them naturally, e.g. "7 2 8 6 Street Southwest".
- If you do not understand, say so plainly and ask again. Do not invent a location.
- If the caller sounds distressed or reports an immediate safety risk, tell them to call 911 \
first, then keep going.
- Never mention that you are an AI. You are the dispatch agent.
- Do not give legal or medical advice. Route those to the right team and move on.
"""
FIRST_MESSAGE = "Calgary 311 dispatch, this line is for road and waste issues. \
What can I help you with today?"

TOOLS = [
    {
        "name": "find_open_requests",
        "description": "Search currently open and overdue 311 road/waste requests in the \
simulated downtown Calgary area. Returns id, category, street address and status. Use this to \
confirm which issue the caller means before you record anything.",
        "response_timeout_secs": 20,
        "api_schema": {"request_query_properties": [
            {"name": "query", "type": "string", "required": True,
             "description": "Street, number or description, e.g. 'debris 6 street southwest'"},
            {"name": "limit", "type": "number", "required": False,
             "description": "Max results (default 5)"}]},
        "dynamic_variables": {},
    },
    {
        "name": "record_311_update",
        "description": "Record the caller's outcome against a specific 311 request. This updates \
the map pin, the priority list and every crew's route immediately. action must be one of \
resolved (fixed/picked up/cleared), reopened (still there), escalate (dangerous/blocking/urgent), \
downgrade (not urgent). Call this exactly once per call, after you have confirmed the id.",
        "response_timeout_secs": 20,
        "api_schema": {"request_query_properties": [
            {"name": "request_id", "type": "string", "required": True,
             "description": "The 311 service request id, e.g. 26-00733545"},
            {"name": "action", "type": "string", "required": True,
             "description": "resolved | reopened | escalate | downgrade"},
            {"name": "note", "type": "string", "required": False,
             "description": "Short detail from the caller, e.g. 'blocking the bike lane'"}]},
        "dynamic_variables": {},
    },
]


def _key():
    for p in (os.path.join(ROOT, ".env"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", ".env")):
        try:
            for line in open(p, encoding="utf-8"):
                if line.startswith("ELEVENLABS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return os.environ.get("ELEVENLABS_API_KEY")


def agent_id():
    for p in (os.path.join(ROOT, ".env"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", ".env")):
        try:
            for line in open(p, encoding="utf-8"):
                if line.startswith("ELEVENLABS_AGENT_ID="):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return os.environ.get("ELEVENLABS_AGENT_ID")


def api(method, path, body=None, key=None):
    key = key or _key()
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY not found in simulation/.env or Hermes settings")
    req = urllib.request.Request(API + path, method=method,
                                 headers={"xi-api-key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", "replace")
        raise SystemExit(f"{method} {path} -> {e.code}: {detail}")


def tool_def(name, schema, url):
    return {"type": "function", "function": {
        "name": name,
        "description": schema["description"],
        "parameters": {"type": "object", "properties": {
            p["name"]: {"type": p["type"], "description": p["description"]}
            for p in schema["api_schema"]["request_query_properties"]},
            "required": [p["name"] for p in schema["api_schema"]["request_query_properties"]
                         if p.get("required")]}}}


def build_config(public_url, base_prompt=None, first_message=None):
    base = (public_url or "").rstrip("/")
    tools = []
    for t in TOOLS:
        d = dict(t)
        d["url"] = f"{base}/api/calls/tool"
        tools.append(d)
    cc = {
        "agent": {
            "first_message": first_message or FIRST_MESSAGE,
            "prompt": {"prompt": (base_prompt or PROMPT).strip(), "llm": "gpt-4o-mini"},
        },
        "turn": {"turn_eagerness": "eager", "turn_timeout": 6.0},
        "asr": {"quality": "high", "provider": "scribe_realtime", "user_input_audio_format": "pcm_16000"},
        # English ConvAI agents are REJECTED by ElevenLabs unless the TTS model is a
        # turbo/flash variant (validated by probing the API, not from docs).
        "tts": {"voice_id": VOICE, "model_id": "eleven_flash_v2",
                "expressive_mode": False,
                "speed": 1.05, "temperature": 0.6, "optimize_streaming_latency": 3,
                "first_phrase_triggerer": {"type": "phrase", "phrase": "Calgary 311",
                                            "trigger_timeout_secs": 2.0,
                                            "min_words_to_trigger": 1}},
        "language": "en",
        "max_turns": 30,
        "allow_agent_to_be_responded_to": True,
    }
# attach server tools only when a public URL is known (ElevenLabs must be able to
    # reach the sim server, so localhost will not do - use a tunnel)
    if base:
        cc["tools"] = [dict(t, url=f"{base}/api/calls/tool") for t in TOOLS]
        cc["agent"]["tools"] = [t["name"] for t in TOOLS]
    return {"conversation_config": cc,
            "platform_settings": {
                "widget": {"variant": "compact", "placement": "bottom-right",
                           "avatar": {"type": "orb", "color_1": "#2792dc", "color_2": "#9ce6e6"},
                           "feedback_mode": "during", "bg_color": "#ffffff",
                           "text_color": "#111111", "btn_color": "#111111",
                           "btn_text_color": "#ffffff", "border_color": "#e1e1e1"},
                # data_collection / privacy use ElevenLabs' AnalysisProperty schema; sending
                # booleans is rejected, so leave them at their defaults rather than guess.
                "privacy": {"recording_consent": "enabled"},
            }}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-url", default=os.environ.get("SIM_PUBLIC_URL"),
                    help="public URL the sim server is reachable at (for ElevenLabs webhooks)")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--agent-id", default=agent_id())
    a = ap.parse_args()

    key = _key()
    aid = a.agent_id
    print("api key :", "found" if key else "MISSING")
    print("agent id:", aid or "MISSING (set ELEVENLABS_AGENT_ID)")
    if not aid:
        return 2
    if a.check_only:
        cur = api("GET", f"/v1/convai/agents/{aid}", key=key)
        cc = cur.get("conversation_config", {})
        ag = cc.get("agent", {})
        print("name            :", cur.get("name"))
        print("first_message   :", (ag.get("first_message") or "")[:80])
        print("prompt chars    :", len(ag.get("prompt", {}).get("prompt") or ""))
        print("voice           :", (cc.get("tts") or {}).get("voice_id"))
        print("tools           :", [t.get("name") for t in (ag.get("tools") or [])])
        print("tool urls       :", [t.get("url") for t in (ag.get("tools") or [])])
        print("widget variant  :", (cur.get("platform_settings", {}).get("widget", {}) or {}).get("variant"))
        return 0

    cfg = build_config(a.public_url)
    api("PATCH", f"/v1/convai/agents/{aid}", cfg, key=key)
    print("agent configured:", aid)
    if a.public_url:
        print("tool webhook    :", a.public_url.rstrip("/") + "/api/calls/tool")
    else:
        print("NOTE: no --public-url, so server tools were NOT attached. "
              "The agent will talk but cannot update the map until you pass a public URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())