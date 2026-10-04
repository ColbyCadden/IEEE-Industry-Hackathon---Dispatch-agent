"""ElevenLabs text-to-speech for the caller chat (the browser does the listening).

Optional. Put ELEVENLABS_API_KEY=... in the repo's .env (ELEVENLABS_VOICE_ID picks the voice).
Without a key speak() returns None and the browser's built-in voice is used. Stdlib HTTP only.
"""
import json
import logging
import os
import urllib.error
import urllib.request

from dispatch.llm import load_env

API = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # same default voice as the simulation's /tts proxy
TTS_MODEL = "eleven_flash_v2_5"  # low latency, good for back-and-forth

log = logging.getLogger(__name__)
last_error: str | None = None  # why the last call returned None, for the UI


def has_voice_key() -> bool:
    load_env()
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def _post(url: str, body: bytes, content_type: str, timeout: float = 20.0) -> bytes | None:
    global last_error
    if not has_voice_key():
        last_error = "no ELEVENLABS_API_KEY in .env"
        return None
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "xi-api-key": os.environ["ELEVENLABS_API_KEY"].strip(), "Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            last_error = None
            return r.read()
    except urllib.error.HTTPError as e:
        last_error = f"ElevenLabs HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
    except Exception as e:
        last_error = f"{type(e).__name__}: {e}"
    log.info("voice call failed: %s", last_error)
    return None


def speak(text: str) -> bytes | None:
    """MP3 bytes of the agent saying `text`, or None."""
    voice = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)
    body = json.dumps({"text": text, "model_id": TTS_MODEL}).encode()
    return _post(f"{API}/text-to-speech/{voice}?output_format=mp3_44100_64", body, "application/json")

