"""ElevenLabs voice for the voice call in the Report an update box: speech-to-text in, spoken replies out.

Optional. Put ELEVENLABS_API_KEY=... in the repo's .env (ELEVENLABS_VOICE_ID picks the voice).
Without a key the dashboard hides the mic and works as before. Stdlib HTTP only; every function
returns None on failure and leaves the reason in last_error for the UI.
"""
import http.server
import json
import logging
import os
import re
import secrets
import threading
import urllib.error
import urllib.request
import uuid

from dispatch.llm import load_env

API = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # same default voice as the simulation's /tts proxy
TTS_MODEL = "eleven_v4"  # ElevenLabs' highest-quality voice (first audio in about 0.6 s when streamed)
TTS_FORMAT = "mp3_44100_128"  # the best MP3 quality the Creator plan allows
STT_MODEL = "scribe_v2"  # the newest transcription model this workspace can use

log = logging.getLogger(__name__)
last_error: str | None = None  # why the last call returned None, for the UI
_spoken: dict[str, bytes] = {}  # text -> MP3, so a reply heard twice is only paid for once


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


def transcribe(audio: bytes, filename: str = "update.wav", mime: str = "audio/wav") -> str | None:
    """What was said in the recording, in written street form ("8th Ave SW"), or None."""
    global last_error
    if not audio:
        last_error = "the recording was empty"
        return None
    b = uuid.uuid4().hex
    fields = b"".join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                      for k, v in (("model_id", STT_MODEL), ("language_code", "en")))
    body = (fields + f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode() + audio + f"\r\n--{b}--\r\n".encode())
    raw = _post(f"{API}/speech-to-text", body, f"multipart/form-data; boundary={b}")
    if raw is None:
        return None
    try:
        text = str(json.loads(raw).get("text") or "").strip()
    except ValueError:
        last_error = "ElevenLabs sent back something that isn't JSON"
        return None
    if not text:
        last_error = "no speech heard in the recording"
        return None
    return street_form(text)


def _plain(text: str) -> str:
    return re.sub(r"[*_`#]", "", text or "").strip()  # markdown marks would be read out


def _tts_request(text: str, stream: bool) -> urllib.request.Request:
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or DEFAULT_VOICE
    return urllib.request.Request(
        f"{API}/text-to-speech/{voice}{'/stream' if stream else ''}?output_format={TTS_FORMAT}",
        data=json.dumps({"text": text, "model_id": TTS_MODEL}).encode(), method="POST",
        headers={"xi-api-key": os.environ.get("ELEVENLABS_API_KEY", "").strip(), "Content-Type": "application/json"})


def speak(text: str) -> bytes | None:
    """MP3 bytes of the agent saying `text`, or None."""
    text = _plain(text)
    if not text:
        return None
    if text not in _spoken:
        req = _tts_request(text, stream=False)
        audio = _post(req.full_url, req.data, "application/json")
        if audio is None:
            return None
        _spoken[text] = audio
    return _spoken[text]


# --- streaming: the browser starts playing while ElevenLabs is still speaking ---------------
# Eleven v4 takes about 2 s to finish a reply but sends its first audio in about 0.6 s. A tiny
# server on 127.0.0.1 relays that stream to the call widget's <audio> element, so the reply starts
# playing at once. The key stays here; the browser only gets a one-off random token.

_texts: dict[str, str] = {}  # token -> text to speak
_relay: dict = {}            # {"port": int} once the relay is running
_relay_lock = threading.Lock()


class _Relay(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server's naming)
        global last_error
        text = _texts.get(self.path.removeprefix("/tts/").removesuffix(".mp3"))
        if text is None:
            self.send_error(404)
            return
        cached = _spoken.get(text)
        try:
            if cached:
                self._start(len(cached))
                self.wfile.write(cached)
                return
            with urllib.request.urlopen(_tts_request(text, stream=True), timeout=20) as upstream:
                self._start(None)
                parts = []
                while chunk := upstream.read1(8192):
                    parts.append(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
            _spoken[text] = b"".join(parts)
            last_error = None
        except urllib.error.HTTPError as e:
            last_error = f"ElevenLabs HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}"
            self.send_error(502)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the call was hung up mid-sentence
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            try:
                self.send_error(502)
            except Exception:
                pass

    def _start(self, length: int | None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Cache-Control", "no-store")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def log_message(self, *args):  # keep the Streamlit terminal quiet
        pass


def warm(lines: list[str]) -> None:
    """Make the lines every call uses (greeting, goodbye…) once in the background, so they play instantly."""
    if not has_voice_key() or _relay.get("warming"):
        return
    _relay["warming"] = True
    threading.Thread(target=lambda: [speak(line) for line in lines], daemon=True, name="voice-warm").start()


def stream_token(text: str) -> tuple[int, str] | None:
    """(port, token): the call widget plays http://127.0.0.1:<port>/tts/<token>.mp3. None without a key."""
    text = _plain(text)
    if not text or not has_voice_key():
        return None
    with _relay_lock:
        if "port" not in _relay:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Relay)
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, daemon=True, name="voice-relay").start()
            _relay["port"] = server.server_address[1]
        token = secrets.token_urlsafe(16)
        _texts[token] = text
        while len(_texts) > 200:  # old replies are never asked for again
            _texts.pop(next(iter(_texts)))
    return _relay["port"], token


# --- spoken addresses -> the written form the address lookup finds -----------------------
# Speech comes back as "Eighth Avenue Southwest"; the lookup only finds "8th Ave SW".

_UNITS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
          "eleventh", "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth",
          "eighteenth", "nineteenth"]
_TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_TENTHS = ["twentieth", "thirtieth", "fortieth", "fiftieth", "sixtieth", "seventieth", "eightieth", "ninetieth"]
_STREETS = {"avenue": "Ave", "street": "St", "road": "Rd", "drive": "Dr", "boulevard": "Blvd", "trail": "Tr",
            "crescent": "Cres", "place": "Pl"}
_STREET = rf"(?:{'|'.join(_STREETS)}|ave|st|rd|dr|blvd|tr)\b"
_ORDINAL_WORD = re.compile(rf"\b(?:(?:({'|'.join(_TENS)})[\s-])?({'|'.join(_UNITS)})|({'|'.join(_TENTHS)}))\b"
                           rf"(?=,?\s+{_STREET})", re.I)


def _ordinal(m: re.Match) -> str:
    if m.group(3):
        n = 20 + 10 * _TENTHS.index(m.group(3).lower())
    else:
        n = _UNITS.index(m.group(2).lower()) + 1
        if m.group(1):
            n += 20 + 10 * _TENS.index(m.group(1).lower())
    return str(n)


def street_form(text: str) -> str:
    """'Eighth Avenue Southwest' -> '8 Ave SW', Calgary's own style and the one the address lookup
    finds most often. Only words right before a street name change, so "crew four" and "first
    thing" stay as they are."""
    text = _ORDINAL_WORD.sub(_ordinal, text)
    text = re.sub(rf"\b(\d+)(?:st|nd|rd|th)?,?\s+({'|'.join(_STREETS)}|ave|st|rd|dr|blvd|tr)\b",
                  lambda m: f"{m.group(1)} {_STREETS.get(m.group(2).lower(), m.group(2).title())}", text, flags=re.I)
    text = re.sub(r"\b(Ave|St|Rd|Dr|Blvd|Tr|Cres|Pl),?\s+(north|south)[\s-]?(east|west)\b",
                  lambda m: f"{m.group(1)} {m.group(2)[0].upper()}{m.group(3)[0].upper()}", text, flags=re.I)
    # "17 Ave and 14 St SW": people say the quadrant once, but both streets are in it
    street = r"\d+ (?:Ave|St|Rd|Dr|Blvd|Tr)"
    return re.sub(rf"\b({street})(\s+(?:and|&)\s+{street}\s+(SW|SE|NW|NE))\b", r"\1 \3\2", text)
