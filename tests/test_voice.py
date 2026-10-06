"""Voice tests (ElevenLabs). Run from the repo root: python -m tests.test_voice

Plain asserts, no network and no key: the HTTP call is replaced with a fake.
"""
import io
import json
import os
import urllib.error
from unittest import mock

from dispatch import voice


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def with_key(fn):
    def run():
        with mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}):
            fn()
    run.__name__ = fn.__name__
    return run


def test_street_form_spoken_addresses():
    cases = {
        "There is a big sinkhole at Eighth Avenue Southwest and Fourth Street Southwest":
            "There is a big sinkhole at 8 Ave SW and 4 St SW",
        "pothole on Twenty-Fourth Avenue North West": "pothole on 24 Ave NW",
        "stop sign down at 17 Avenue SW and 14 Street, South West": "stop sign down at 17 Ave SW and 14 St SW",
        "debris on thirtieth street northeast": "debris on 30 St NE",
        "Eleventh Avenue SE": "11 Ave SE",
        "Twenty-First Street NW": "21 St NW",
        "big sinkhole at 17th Avenue and 14th Street southwest": "big sinkhole at 17 Ave SW and 14 St SW",
        "8th Ave SW and 4th St SW": "8 Ave SW and 4 St SW",
        "pothole on 21st St NE": "pothole on 21 St NE",
    }
    for said, written in cases.items():
        assert voice.street_form(said) == written, (said, voice.street_form(said))


def test_street_form_leaves_crew_numbers_alone():
    for text in ("Hey crew. Four here. We're all out sick today.", "crew two is short-handed",
                 "first thing this morning crew 4 is out", "the second crew is fine"):
        assert voice.street_form(text) == text, text


@with_key
def test_transcribe_returns_written_form():
    reply = FakeResponse(json.dumps({"text": "Sinkhole at Eighth Avenue Southwest"}).encode())
    with mock.patch("urllib.request.urlopen", return_value=reply) as call:
        assert voice.transcribe(b"RIFF....") == "Sinkhole at 8 Ave SW"
    req = call.call_args[0][0]
    assert req.full_url.endswith("/speech-to-text")
    assert req.get_header("Xi-api-key") == "test-key"
    assert b'name="model_id"' in req.data and voice.STT_MODEL.encode() in req.data


@with_key
def test_transcribe_failures_return_none_with_a_reason():
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"missing_permissions"}'))
    with mock.patch("urllib.request.urlopen", side_effect=err):
        assert voice.transcribe(b"audio") is None
    assert "401" in voice.last_error
    with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b'{"text": "  "}')):
        assert voice.transcribe(b"audio") is None
    assert "no speech" in voice.last_error
    assert voice.transcribe(b"") is None and "empty" in voice.last_error


def test_no_key_means_no_call():
    with mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}), \
            mock.patch.object(voice, "load_env"), mock.patch("urllib.request.urlopen") as call:
        assert not voice.has_voice_key()
        assert voice.transcribe(b"audio") is None and voice.speak("hello") is None
    call.assert_not_called()


@with_key
def test_speak_strips_markdown_and_caches():
    voice._spoken.clear()
    with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: FakeResponse(b"MP3")) as call:
        assert voice.speak("Crew 4 is **out for the day**.") == b"MP3"
        assert voice.speak("Crew 4 is **out for the day**.") == b"MP3"
    assert call.call_count == 1                                  # the second time comes from the cache
    assert json.loads(call.call_args[0][0].data)["text"] == "Crew 4 is out for the day."


@with_key
def test_failed_speech_is_not_cached():
    voice._spoken.clear()
    with mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
        assert voice.speak("Replanned.") is None
    assert "Replanned." not in voice._spoken and "offline" in voice.last_error


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)
