"""Streamlit demo: `streamlit run dispatch/app.py` (run `python -m dispatch.run` first)."""
import hashlib
import html
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `streamlit run` only puts dispatch/ on the path

try:  # llm.py is owned by a teammate; the dashboard must still load without it
    from dispatch import llm  # noqa: E402
except Exception:
    llm = None

try:  # the baseline / result / improved comparison is shared with `python -m dispatch.improvement`
    from dispatch import improvement  # noqa: E402
except Exception:
    improvement = None

try:  # caller voice call: intake agent, local voice API, optional ElevenLabs voice
    from dispatch import intake, voice, voice_server  # noqa: E402
except Exception:
    intake = voice = voice_server = None

try:  # Semir's 3D downtown simulation: our plan's jobs as pins in his scene
    from dispatch import sim3d  # noqa: E402
except Exception:
    sim3d = None

try:  # new urgent jobs and the day's running log of updates
    from dispatch import live as day_live  # noqa: E402
except Exception:
    day_live = None

try:  # the score formula and its 0-10 display scale live in one place
    from dispatch import scoring  # noqa: E402
except Exception:
    scoring = None

OUT = ROOT / "dispatch" / "outputs"
CREW_COLORS = [  # one per crew, readable on a light basemap
    [31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40],
    [148, 103, 189], [140, 86, 75], [227, 119, 194], [23, 190, 207],
]
OURS_COLOR = "rgb(44,160,44)"   # our agent, everywhere on the page
FIFO_COLOR = "rgb(130,130,130)"  # the oldest-first baseline
QUICK_FILLS = ["Crew 4 called in sick", "Crew 2 is down a guy",
               "Urgent pothole in Marlborough near a school",
               "Sinkhole at 8 Ave SW and 4 St SW blocking traffic",
               "Stop sign knocked down at 17 Ave SW and 14 St SW"]
NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight"}


def p10(p: float) -> float:
    """P on the 0-10 display scale (scoring.priority_10, or 2 x P if scoring.py doesn't provide it)."""
    scale = getattr(scoring, "priority_10", None)
    return scale(p) if scale else round(min(10.0, 2 * p), 1)


def crew_rgb(crew: int) -> list:
    return CREW_COLORS[(crew - 1) % len(CREW_COLORS)]


def dot(crew: int, size: str = "1.2em") -> str:
    r, g, b = crew_rgb(crew)
    return f"<span style='color:rgb({r},{g},{b});font-size:{size}'>●</span>"


# --- data ------------------------------------------------------------------

def _fake_outputs() -> dict:
    """Contract-shaped stand-in so the app still opens before `python -m dispatch.run`."""
    zones = [("E", 51.055, -114.01), ("NW", 51.118, -114.275), ("W", 51.042, -114.147), ("SE", 50.892, -113.941),
             ("S", 50.914, -114.078), ("NE", 51.098, -113.938), ("N", 51.152, -114.123), ("W", 51.043, -114.080)]
    kinds = [("Pothole", "Roads - Pothole Maintenance", True, 3),
             ("Damaged sign", "Roads - Signs - Missing - Damaged", True, 3),
             ("Debris", "Roads - Debris on Street/Sidewalk/Boulevard", False, 2)]

    def plan(fifo: bool) -> dict:
        crews = []
        for i, (zone, lat, lon) in enumerate(zones):
            jobs = []
            for k in range(5):
                t, name, safety, w = kinds[(i + k + fifo) % 3] if not fifo or k < 2 else kinds[2]
                jobs.append({"id": f"FAKE-{i + 1}{k + 1}", "type": t, "service_name": name, "community": "SAMPLE",
                             "P": w + 0.25 * k, "safety": safety, "lat": lat + 0.01 * (k - 2),
                             "lon": lon + 0.012 * ((k * 3) % 5 - 2), "reports": 1})
            crews.append({"crew": i + 1, "zone": zone, "centroid": [lat, lon], "jobs": jobs})
        return {"crews": crews}

    def compute(p: dict) -> dict:
        jobs = [j for c in p["crews"] for j in c["jobs"]]
        return {"P": round(sum(j["P"] for j in jobs), 2), "safety": sum(j["safety"] for j in jobs), "n": len(jobs)}

    p8, pf = plan(False), plan(True)
    return {"plan_8am": p8, "plan_fifo": pf, "metrics": {"8am": compute(p8), "fifo": compute(pf)}, "fake": True}


def outputs_stamp() -> tuple:
    """When each output file last changed: part of the cache key, so a new pipeline run shows up at once."""
    return tuple((OUT / f).stat().st_mtime_ns if (OUT / f).exists() else 0
                 for f in ("plan_8am.json", "plan_fifo.json", "metrics.json", "event.json"))


@st.cache_data
def load_outputs(stamp: tuple = ()) -> dict:
    files = {"plan_8am": "plan_8am.json", "plan_fifo": "plan_fifo.json", "metrics": "metrics.json"}
    try:
        data = {k: json.loads((OUT / f).read_text(encoding="utf-8")) for k, f in files.items()}
    except (OSError, ValueError):  # missing or unreadable outputs: show sample data, never a traceback
        return _fake_outputs()
    data["fake"] = False
    return data


@st.cache_data
def load_event(stamp: tuple = ()) -> dict | None:
    """The disruption the pipeline replanned for (dispatch/outputs/event.json)."""
    try:
        return json.loads((OUT / "event.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@st.cache_data
def load_ticket_details() -> dict:
    """id -> {days_open, weight, requested_date} from the engine's own scoring (read-only)."""
    try:
        from dispatch.data_prep import load_clean_tickets
        df = scoring.score(load_clean_tickets(str(ROOT / "data" / "311_dispatch_sample.csv")))
        return {r.id: {"days_open": int(r.days_open), "weight": float(r.weight),
                       "requested_date": r.requested_date.strftime("%b %d, %Y")}
                for r in df.itertuples(index=False)}
    except Exception:  # engine missing: the panel shows what the plan itself carries
        return {}


def build_day(plan_8am: dict, metrics: dict, log: list) -> dict:
    """Replay every update of the day on the 8 a.m. plan. Returns the bundle or {"error"}."""
    try:
        return day_live.bundle(plan_8am, metrics, log)
    except Exception as e:  # engine missing or broken: keep the app usable
        return {"error": f"{type(e).__name__}: {e}"}


# --- llm wrappers (contract signatures only; fall back if llm.py fails) ------

EVENTS = ("crew_out", "crew_partial", "new_job", "unclear")


def claude_ready() -> bool:
    """True when an API key is configured, so Claude will be tried first."""
    try:
        return bool(llm and llm.USE_LLM and llm.has_api_key())
    except Exception:
        return False


def _source(kind: str) -> str:
    """'claude' if Claude produced the last parse/briefing, else 'rules' (regex/template fallback)."""
    src = (getattr(llm, "last_source", None) or {}).get(kind)
    return "claude" if src == "claude" else "rules"


def safe_parse(text: str, only_crew: int | None = None) -> tuple[dict | None, str | None]:
    """llm.parse_event(text) checked against the contract. Returns (event, error).

    only_crew: the supervisor already chose a crew (after "which one first?").
    """
    try:
        ev = llm.parse_event(text, only_crew) if only_crew is not None else llm.parse_event(text)
        if not isinstance(ev, dict) or ev.get("event") not in EVENTS:
            raise ValueError(f"unexpected result {ev!r}")
        if ev["event"] == "new_job":
            job = ev.get("job")
            if not isinstance(job, dict) or not job.get("label") or job.get("severity") not in (0, 1, 2, 3):
                raise ValueError(f"bad new job {job!r}")
        elif ev["event"] != "unclear" and ev.get("crew") not in range(1, 9):
            raise ValueError(f"crew must be 1-8, got {ev.get('crew')!r}")
        ev.setdefault("capacity", 0.0 if ev["event"] == "crew_out" else 0.5)
        ev.setdefault("question", None)
        if not 0.0 <= float(ev["capacity"]) <= 1.0:
            raise ValueError(f"capacity must be 0-1, got {ev['capacity']!r}")
        ev["source"] = _source("parse")
        ev["why"] = (getattr(llm, "last_source", None) or {}).get("parse") or ""
        return ev, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def safe_briefing(plan: dict, metrics: dict, when: str, changes: dict | None = None,
                  event: dict | None = None) -> tuple[str, str]:
    """llm.briefing(...) or a short numbers-only fallback. Returns (text, "claude" | "rules" | "numbers")."""
    try:
        try:  # pass the event when llm.briefing accepts it, so a short-handed crew isn't called "out"
            text = llm.briefing(plan, metrics, when, changes, event=event)
        except TypeError:
            text = llm.briefing(plan, metrics, when, changes)
        if isinstance(text, str) and text.strip():
            return text, _source("briefing")
    except Exception:
        pass
    if when == "noon":
        m = metrics["noon"]
        return (f"Noon plan: {m['n']} jobs, {m['safety']} safety tickets. {m.get('moved', 0)} jobs moved, "
                f"{m.get('dropped', 0)} deferred, {m.get('safety_dropped', 0)} safety tickets deferred."), "numbers"
    m, f = metrics["8am"], metrics["fifo"]
    return (f"8 a.m. plan: {m['n']} jobs, {m['safety']} safety tickets "
            f"(oldest-first would cover {f['safety']})."), "numbers"


def cached_briefing(key: str, *args, **kwargs) -> tuple[str, str]:
    """One briefing per plan state per session: clicks and reruns don't call Claude again."""
    store = st.session_state.setdefault("briefings", {})
    if key not in store:
        with st.spinner("Writing the briefing..."):
            store[key] = safe_briefing(*args, **kwargs)
    return store[key]


SOURCE_NOTES = {
    "claude": ":material/auto_awesome: Written by Claude from the plan's numbers.",
    "rules": ":material/rule: Rule-based template (Claude unavailable: no API key or no connection).",
    "numbers": ":material/info: Briefing service unavailable, showing the numbers only.",
}

BRIEF_COLORS = {"morning": "rgb(31,119,180)", "update": "rgb(255,127,14)", "day": "rgb(44,160,44)"}
CHIP_TONES = {
    "good": "background:rgba(44,160,44,.16);color:rgb(44,160,44)",
    "bad": "background:rgba(214,39,40,.14);color:rgb(214,39,40)",
    "neutral": "background:rgba(128,128,128,.16)",
}


def read_by(ev: dict) -> str:
    """Which reader turned the supervisor's message into this update."""
    src, why = ev.get("source"), str(ev.get("why", ""))
    if src == "claude":
        return ":material/auto_awesome: Read by **Claude**"
    if src == "manual":
        return ":material/edit: Entered by hand"
    if src == "caller":
        return ":material/call: From a caller report (Caller report tab)"
    if why.startswith("fallback"):
        return ":material/rule: Read by the **rule-based parser** (Claude unavailable: no API key or no connection)"
    return ":material/rule: Read by the **rule-based parser** (simple message, no Claude call needed)"


# --- selection ----------------------------------------------------------------

def select_crew(crew: int) -> None:
    """Switch straight to this crew (no need to deselect the previous one)."""
    st.session_state.selected_crew = crew
    st.session_state.selected_job = None


def show_all() -> None:
    st.session_state.selected_crew = None
    st.session_state.selected_job = None


def select_job(job_id: str | None, crew: int | None = None) -> None:
    """Select a job; its crew becomes the selected crew too, so the list and map stay in sync."""
    st.session_state.selected_job = job_id
    if crew is not None:
        st.session_state.selected_crew = crew


def find_job(plan: dict, changes: dict | None, job_id: str | None) -> tuple[dict | None, dict | None]:
    """(job, crew) for job_id in this plan; crew is None for a job deferred at noon."""
    if not job_id:
        return None, None
    for c in plan["crews"]:
        for j in c["jobs"]:
            if j["id"] == job_id:
                return j, c
    for j in (changes or {}).get("dropped_jobs", []):
        if j["id"] == job_id:
            return j, None
    return None, None


def find_crew(plan: dict, crew_id: int | None) -> dict | None:
    return next((c for c in plan["crews"] if c["crew"] == crew_id), None) if crew_id else None


# --- view helpers ------------------------------------------------------------

def crew_limit(crew_id: int | None) -> int:
    """A crew's 8 a.m. job limit (flexible crews), or 5 for a standard crew."""
    c = find_crew(data["plan_8am"], crew_id) if crew_id else None
    return int(c.get("limit", 5)) if c else 5


def describe(event: dict) -> str:
    kind, crew = event.get("event"), event.get("crew")
    if kind == "new_job":
        job = event["job"]
        where = job.get("resolved") or job.get("address") or str(job.get("community") or "the pinned spot").title()
        sev = day_live.SEVERITY_LABELS[job["severity"]] if day_live else job["severity"]
        return f"New **{sev}-priority job**: {job['label'].lower()} at {where}."
    if kind == "crew_out":
        return f"Crew {crew} is **out for the day**. All of its jobs will be reassigned or deferred."
    if kind == "crew_partial" and event.get("capacity", 0.5) >= 1.0:
        return f"Crew {crew} is **back at full strength** and takes back deferred jobs nearby."
    if kind == "crew_partial":
        cap = event.get("capacity", 0.5)
        return (f"Crew {crew} is **short-handed** ({cap:.0%} capacity). "
                f"It keeps its top {round(crew_limit(crew) * cap)} jobs; the rest will be reassigned or deferred.")
    return "I couldn't tell what changed."


def job_badges(job: dict, moved: set) -> None:
    """Clean labels instead of symbols: Safety / Moved / New today."""
    if job["safety"]:
        st.badge("Safety", color="red")
    if job["id"] in moved:
        st.badge("Moved", color="orange")
    if job.get("new"):
        st.badge("New today", color="blue")


def render_job_detail(job: dict | None, crew: dict | None, view: str) -> None:
    """Details for the selected job, including how its priority score was built."""
    job_id = st.session_state.selected_job
    with st.container(border=True):
        head, close = st.columns([5, 2], vertical_alignment="center")
        head.markdown(f"**:material/my_location: Ticket {job_id}**")
        close.button("Clear job", icon=":material/close:", type="tertiary", on_click=select_job, args=(None,),
                     key="clear_job")
        if job is None:
            st.caption(f"This ticket isn't on the {view} plan. Switch views or clear the selection.")
            return
        with st.container(horizontal=True, gap="small"):
            job_badges(job, set())
        info = load_ticket_details().get(job_id)
        where = (f"Crew {crew['crew']} ({crew['zone']})" if crew else "Deferred at noon")
        if job.get("new"):  # reported after 8 a.m.: no ticket history, so show how it was read and scored
            sev = day_live.SEVERITY_LABELS.get(job.get("severity"), "high") if day_live else job.get("severity")
            rows = [("Problem", job["type"]), ("Severity", f"{job.get('severity')} ({sev})"),
                    ("Community", job["community"].title()),
                    ("Address", job.get("address") or "not given"),
                    ("Location", f"{job['lat']:.5f}, {job['lon']:.5f} ({job.get('location_source') or 'pinned'})"),
                    ("Assigned to", where)]
            st.markdown("\n".join(f"- **{k}:** {v}" for k, v in rows))
            if job.get("summary"):
                st.caption(f"“{job['summary']}”")
            st.markdown(f"**Priority:** {p10(job['P']):.1f} / 10. Severity {job.get('severity')} sets P "
                        f"{job['P']:.2f}; emergencies (severity 3) always rank above every job already on the plan.")
            return
        rows = [
            ("Service type", job["service_name"]),
            ("Community", job["community"].title()),
            ("Location", f"{job['lat']:.5f}, {job['lon']:.5f}"),
            ("Assigned to", where),
            ("Days open", f"{info['days_open']} (reported {info['requested_date']})" if info else "n/a"),
            ("Reports", f"{job['reports']} ({job['reports'] - 1} duplicate)" if job["reports"] > 1
             else "1 (no duplicates)"),
            ("Safety ticket", "Yes (pothole or missing/damaged sign)" if job["safety"] else "No"),
        ]
        st.markdown("\n".join(f"- **{k}:** {v}" for k, v in rows))
        st.caption("The source data has no street address; the coordinates are the precise location.")
        explain = getattr(scoring, "explain", None)
        raw = explain(info["weight"], info["days_open"], job["reports"]) if info and explain else f"P {job['P']:.2f}"
        st.markdown(f"**Priority:** {p10(job['P']):.1f} / 10")
        st.caption(f"Raw score: {raw}")


def render_crew_panel(crew: dict, changes: dict | None, out_crews: set) -> None:
    """The selected crew's jobs in priority order; each row selects that job."""
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    with st.container(border=True):
        head, close = st.columns([5, 2], vertical_alignment="center")
        head.markdown(f"{dot(crew['crew'], '1.3em')} **Crew {crew['crew']}**", unsafe_allow_html=True)
        close.button("Show all crews", icon=":material/close:", type="tertiary", on_click=show_all,
                     key="clear_crew")
        jobs = sorted(crew["jobs"], key=lambda j: -j["P"])
        if not jobs:
            st.caption(f"Zone {crew['zone']} · " + ("out today, no jobs" if crew["crew"] in out_crews else "no jobs"))
            return
        st.caption(f"Zone {crew['zone']} · {crew.get('workers', 4)} people · {len(jobs)} of {crew.get('limit', 5)} jobs · "
                   f"{sum(j['safety'] for j in jobs)} safety · "
                   "priority out of 10 on the right")
        for rank, j in enumerate(jobs, 1):
            chosen = j["id"] == st.session_state.selected_job
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.button(f"{rank}. {j['type']} · {j['community'].title()}", key=f"crewjob_{j['id']}",
                          type="secondary" if chosen else "tertiary",
                          icon=":material/my_location:" if chosen else None,
                          on_click=select_job, args=(j["id"], crew["crew"]))
                job_badges(j, moved)
                st.space("stretch")
                st.markdown(f"**{p10(j['P']):.1f}**", width="content")


def _tip(job: dict, crew: str, status: str) -> str:
    safety = "<br/><b>Safety ticket</b>" if job["safety"] else ""
    note = f" · {status}" if status else ""
    return (f"<b>{job['type']}</b> · {job['community'].title()}<br/>"
            f"Priority {p10(job['P']):.1f} / 10 · {crew}{note}{safety}<br/>Ticket {job['id']}")


def map_layers(plan: dict, changes: dict | None, selected: dict | None = None,
               crew_id: int | None = None) -> list:
    """Job markers coloured by crew; other crews fade when one is selected; ring on the selected job."""
    sel_id = selected["id"] if selected else None
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    added = set((changes or {}).get("added", []))
    points = []
    for c in plan["crews"]:
        r, g, b = crew_rgb(c["crew"])
        focus = crew_id is None or c["crew"] == crew_id
        for j in c["jobs"]:
            status = "new today" if j["id"] in added else ("moved here" if j["id"] in moved else "")
            big = j["safety"] or j["id"] == sel_id or j["id"] in added
            points.append({**j, "crew": c["crew"], "color": [r, g, b, 230 if focus else 55],
                           "radius": (8 if big else 5) if focus else 4,
                           "line": [255, 193, 7] if j["id"] in added else (
                               [30, 30, 30] if j["id"] in moved else [255, 255, 255]),
                           "line_w": 2 if focus else 0,
                           "tip": _tip(j, f"crew {c['crew']} ({c['zone']})", status)})
    dropped = [{**j, "crew": None, "color": [150, 150, 150, 200 if crew_id is None else 55],
                "radius": 5, "line": [90, 90, 90], "line_w": 1, "tip": _tip(j, "deferred to tomorrow", "")}
               for j in (changes or {}).get("dropped_jobs", [])]
    labels = [{"crew": c["crew"], "lat": c["centroid"][0], "lon": c["centroid"][1], "label": f"Crew {c['crew']}",
               "color": crew_rgb(c["crew"]) + [255 if crew_id in (None, c["crew"]) else 70],
               "tip": f"<b>Crew {c['crew']}</b> · {c['zone']} zone<br/>Click to show this crew"}
              for c in plan["crews"]]
    layers = [
        pdk.Layer("ScatterplotLayer", dropped + points, id="jobs", get_position="[lon, lat]", pickable=True,
                  get_fill_color="color", get_radius="radius", radius_units="'pixels'", stroked=True,
                  get_line_color="line", get_line_width="line_w", line_width_units="'pixels'"),
        pdk.Layer("TextLayer", labels, id="crews", get_position="[lon, lat]", pickable=True, get_text="label",
                  get_color="color", get_size=14, get_alignment_baseline="'bottom'", font_weight=700),
    ]
    if selected:  # ring around the selected job, drawn on top
        layers.append(pdk.Layer("ScatterplotLayer", [selected], id="ring", get_position="[lon, lat]",
                                get_radius=15, radius_units="'pixels'", filled=False, stroked=True,
                                get_line_color=[20, 20, 20], line_width_units="'pixels'", get_line_width=3))
    return layers


def crew_view_state(crew: dict) -> pdk.ViewState:
    """Fit the map to one crew's jobs (zoom capped so a tight cluster isn't street level)."""
    view = pdk.data_utils.compute_view([[j["lon"], j["lat"]] for j in crew["jobs"]], view_proportion=1)
    return pdk.ViewState(latitude=view.latitude, longitude=view.longitude, zoom=min(view.zoom, 13.0))


@st.cache_data
def pick_grid() -> list:
    """Invisible clickable points across the city (about 330 m apart): a click on the map snaps to the nearest."""
    lat0, lat1, lon0, lon1 = 50.84, 51.22, -114.34, -113.86
    pts, lat = [], lat0
    while lat <= lat1:
        lon = lon0
        while lon <= lon1:
            pts.append({"lat": round(lat, 5), "lon": round(lon, 5)})
            lon += 0.0048
        lat += 0.003
    return pts


def render_map(plan: dict, changes: dict | None = None, selected: dict | None = None,
               crew: dict | None = None, tag: str = "", pick: bool = False):
    """Draw the map.

    Pick mode returns (lat, lon) of the clicked spot. Normal mode returns ("job", id, crew) or
    ("crew", n) when a marker or crew label was clicked, else None.
    """
    if crew and crew["jobs"]:
        view_state = crew_view_state(crew)
    elif selected:
        view_state = pdk.ViewState(latitude=selected["lat"], longitude=selected["lon"], zoom=13)
    else:
        view_state = pdk.ViewState(latitude=51.03, longitude=-114.07, zoom=10.1)
    layers = map_layers(plan, changes, selected, crew["crew"] if crew else None)
    alt = "Map of Calgary showing today's jobs, coloured by crew"
    if pick:  # nearly transparent dots to click on; the job markers stay visible underneath
        layers.append(pdk.Layer("ScatterplotLayer", pick_grid(), id="pick_grid", pickable=True,
                                get_position="[lon, lat]", get_radius=240, radius_units="'meters'",
                                get_fill_color=[255, 193, 7, 40], auto_highlight=True,
                                highlight_color=[255, 193, 7, 200]))
        deck = pdk.Deck(layers=layers, initial_view_state=view_state, map_provider="carto", map_style="light")
        event = st.pydeck_chart(deck, key=f"pick_{tag}", alt=alt, on_select="rerun",
                                selection_mode="single-object")
        hit = (event.selection.objects.get("pick_grid") or []) if event else []
        st.caption("Click anywhere on the map to place the job (it snaps to the nearest point, within about 300 m).")
        return (hit[0]["lat"], hit[0]["lon"]) if hit else None

    deck = pdk.Deck(layers=layers, initial_view_state=view_state, map_provider="carto", map_style="light",
                    tooltip={"html": "{tip}", "style": {"fontSize": "12px"}})
    # a new key per selection or plan remounts the map so it moves to the new view state
    key = f"map_{tag}_{selected['id'] if selected else ''}_{crew['crew'] if crew else ''}"
    event = st.pydeck_chart(deck, key=key, alt=alt, on_select="rerun", selection_mode="single-object")
    st.caption("Click a job or a crew name. Colour = crew · large dot = safety ticket · dark outline = moved · "
               "yellow outline = new today · grey = deferred.")
    objects = event.selection.objects if event else {}
    job_hit, crew_hit = objects.get("jobs") or [], objects.get("crews") or []
    if job_hit and job_hit[0].get("id") != st.session_state.selected_job:
        return ("job", job_hit[0]["id"], job_hit[0].get("crew"))
    if crew_hit and crew_hit[0].get("crew") != st.session_state.selected_crew:
        return ("crew", crew_hit[0]["crew"])
    return None


# --- results ----------------------------------------------------------------

BOX = "border:1px solid rgba(128,128,128,.35);border-radius:.6rem;padding:.7rem 1rem;height:100%"


def _compare_html(title: str, ours: str, theirs: str, note: str, size: float) -> str:
    """Two big numbers side by side: our agent (green) vs oldest-first (muted)."""
    return (
        f"<div style='{BOX}'>"
        f"<div style='font-size:.95rem;font-weight:600;opacity:.8'>{title}</div>"
        f"<div style='display:flex;align-items:baseline;gap:.7rem;flex-wrap:wrap;margin:.2rem 0'>"
        f"<span style='font-size:{size}rem;font-weight:800;line-height:1.05;color:{OURS_COLOR}'>{ours}</span>"
        f"<span style='font-size:{size * .4:.2f}rem;opacity:.6'>vs</span>"
        f"<span style='font-size:{size}rem;font-weight:800;line-height:1.05;opacity:.45'>{theirs}</span></div>"
        f"<div style='font-size:.8rem;opacity:.7'>{note}</div></div>"
    )


def _count_html(title: str, value: str | None, note: str) -> str:
    """Small card; greyed 'awaiting crew update' when there is no value yet."""
    if value is None:
        return (f"<div style='{BOX};opacity:.45'><div style='font-size:.95rem;font-weight:600'>{title}</div>"
                f"<div style='font-size:1rem;font-style:italic;margin-top:.5rem'>awaiting crew update</div></div>")
    return (f"<div style='{BOX}'><div style='font-size:.95rem;font-weight:600;opacity:.8'>{title}</div>"
            f"<div style='font-size:2.2rem;font-weight:700;line-height:1.1'>{value}</div>"
            f"<div style='font-size:.8rem;opacity:.7'>{note}</div></div>")


def render_metrics(view: str, m: dict, fifo: dict, base: dict, metrics: dict, n_updates: int = 0) -> None:
    """Headline row: safety and priority vs oldest-first, then moved / deferred.

    Before any crew update today, the moved / deferred cards show the benchmark replan
    (the pipeline's crew-out scenario) so the case's two counts are always on screen.
    """
    ours = base if view == "FIFO" else m  # the comparison always shows our plan vs oldest-first
    live = view == "Agent - noon"
    who = (f"our plan after {n_updates} update{'s' if n_updates != 1 else ''}" if live else "our agent, 8 a.m.")
    c1, c2, c3, c4 = st.columns([2.2, 1.4, 1, 1])
    c1.markdown(_compare_html("Safety hazards covered — our agent vs oldest-first",
                              str(ours["safety"]), str(fifo["safety"]),
                              f"{who} · oldest-first (FIFO), all crews", 4.2), unsafe_allow_html=True)
    c2.markdown(_compare_html("Priority served (total P)", f"{ours['P']:.1f}", f"{fifo['P']:.1f}",
                              "our agent vs oldest-first", 2.2), unsafe_allow_html=True)
    noon = m if live else metrics.get("noon")
    event = load_event(outputs_stamp()) or {"crew": 4}
    scope = "today's updates" if live else f"benchmark: crew {event['crew']} out"
    if noon:
        c3.markdown(_count_html("Jobs moved", str(noon.get("moved", 0)), f"to a nearby crew · {scope}"),
                    unsafe_allow_html=True)
        c4.markdown(_count_html("Jobs deferred", str(noon.get("dropped", 0)),
                                f"<b>{noon.get('safety_dropped', 0)} safety tickets dropped</b> · {scope}"),
                    unsafe_allow_html=True)
    else:
        c3.markdown(_count_html("Jobs moved", None, ""), unsafe_allow_html=True)
        c4.markdown(_count_html("Jobs deferred", None, ""), unsafe_allow_html=True)
    if live and m.get("added"):
        st.caption(f"Our count includes {m['added']} job{'s' if m['added'] != 1 else ''} reported after 8 a.m.; "
                   "the oldest-first baseline is the 8 a.m. list and never saw them.")
    note = getattr(scoring, "SCALE_NOTE", "")
    st.caption("**Priority score P** = hazard type (0–3) + 0.25 per day waiting + 0.5 per extra report. "
               f"{note}")
    st.caption("**Robustness:** we re-ran the plan with every type weight changed by ±1 — 16 variations. "
               "Our agent covered more safety tickets than oldest-first in all 16, by between +9 and +15.")


def _stage_html(label: str, big: str, of: str, lines: list[str], color: str) -> str:
    """One card of the day's story: a big number, then the detail lines."""
    body = "".join(f"<div style='font-size:.8rem;opacity:.85'>{t}</div>" for t in lines)
    return (f"<div style='{BOX}'>"
            f"<div style='font-size:.8rem;font-weight:700;letter-spacing:.06em'>{label}</div>"
            f"<div style='display:flex;align-items:baseline;gap:.4rem;margin:.15rem 0'>"
            f"<span style='font-size:2.6rem;font-weight:800;line-height:1.05;color:{color}'>{big}</span>"
            f"<span style='font-size:.95rem;opacity:.7'>{of}</span></div>{body}</div>")


def render_improvement(metrics: dict) -> None:
    """The improvement story in one row: oldest-first -> our 8 a.m. plan -> after a crew drops out."""
    fifo, am, noon = metrics.get("fifo"), metrics.get("8am"), metrics.get("noon")
    event = load_event(outputs_stamp()) or {"crew": 4}
    st.markdown("**Improvement round:** baseline → our plan → after the crew update (same 32 workers)")
    c1, c2, c3 = st.columns(3)
    c1.markdown(_stage_html("1 BASELINE · OLDEST-FIRST", str(fifo["safety"]), f"of {fifo['n']}",
                            ["jobs are safety tickets", f"total P {fifo['P']:.2f}"], FIFO_COLOR),
                unsafe_allow_html=True)
    c2.markdown(_stage_html("2 OUR 8 A.M. PLAN", str(am["safety"]), f"of {am['n']}",
                            ["jobs are safety tickets", f"<b>+{am['safety'] - fifo['safety']}</b> vs oldest-first, "
                             f"total P {am['P']:.2f}"], OURS_COLOR), unsafe_allow_html=True)
    if not noon:
        c3.markdown(_stage_html(f"3 CREW {event['crew']} OUT", "–", "", ["run python -m dispatch.run"],
                                FIFO_COLOR), unsafe_allow_html=True)
        return
    share = noon["P"] / am["P"] if am["P"] else 0
    held = "held at" if noon["safety"] >= am["safety"] else "now"
    c3.markdown(_stage_html(f"3 AFTER CREW {event['crew']} CALLS IN SICK", str(noon["n"]), "jobs done",
                            [f"safety coverage {held} <b>{noon['safety']}</b> · "
                             f"<b>{noon.get('safety_dropped', 0)}</b> safety dropped",
                             f"{noon.get('moved', 0)} moved · {noon.get('dropped', 0)} lower-priority deferred · "
                             f"<b>{share:.0%}</b> of priority served"], OURS_COLOR), unsafe_allow_html=True)
    if improvement:
        try:
            rows = improvement.comparison_rows(metrics, event)
            with st.expander("Raw numbers"):
                st.dataframe([{"Stage": f"{r['stage']} · {r['what']}", "Jobs": r["m"]["jobs"],
                               "Safety covered": r["m"]["safety"], "Total P": r["m"]["P"],
                               "Moved": r["m"].get("moved"), "Deferred": r["m"].get("deferred"),
                               "Safety dropped": r["m"].get("safety_dropped")}
                              for r in rows if r["m"]], hide_index=True)
        except Exception:
            pass


def _sentences(text: str) -> list[str]:
    """Split a briefing into sentences without breaking on 'a.m.' / 'p.m.'."""
    guarded = re.sub(r"\b([ap])\.m\.", r"\1<dot>m<dot>", text.strip())
    parts = [p.replace("<dot>", ".") for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", guarded) if p.strip()]
    if len(parts) > 1 and len(parts[0].split()) <= 3:  # "Good morning." opens the next point, not its own
        parts[:2] = [f"{parts[0]} {parts[1]}"]
    return parts


def _brief_card_html(when: str, title: str, accent: str, text: str | None, chips: list[tuple[str, str]],
                     waiting: str = "") -> str:
    """One briefing card: time + title, highlight chips, then the briefing as a numbered list.

    text=None renders a waiting state with the `waiting` message. Briefing text is escaped: it may come
    from Claude.
    """
    head = (f"<div style='display:flex;align-items:center;gap:.5rem;flex-wrap:wrap'>"
            f"<span style='font-weight:800;color:{accent}'>{html.escape(when)}</span>"
            f"<span style='font-weight:600;opacity:.85'>· {html.escape(title)}</span></div>")
    if text is None:
        return (f"<div style='border:1px dashed rgba(128,128,128,.5);border-left:5px solid {accent};"
                f"border-radius:.6rem;padding:.7rem 1rem;margin-bottom:.4rem;opacity:.6'>{head}"
                f"<div style='font-style:italic;margin-top:.45rem'>{html.escape(waiting)}</div></div>")
    pills = "".join(f"<span style='display:inline-block;margin:.45rem .35rem 0 0;padding:.15rem .6rem;"
                    f"border-radius:1rem;font-size:.82rem;font-weight:700;{CHIP_TONES[tone]}'>{html.escape(label)}"
                    f"</span>" for label, tone in chips)
    items = "".join(f"<li style='margin:.2rem 0'>{html.escape(s)}</li>" for s in _sentences(text))
    return (f"<div style='border:1px solid rgba(128,128,128,.35);border-left:5px solid {accent};"
            f"border-radius:.6rem;padding:.7rem 1rem;margin-bottom:.4rem'>{head}<div>{pills}</div>"
            f"<ol style='margin:.55rem 0 0 1.1rem;padding:0;line-height:1.45'>{items}</ol></div>")


def _digest(obj) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def day_timeline(plan_8am: dict, metrics: dict, log: list) -> list[dict]:
    """The 8 a.m. briefing, then one briefing per update, each written from the plan right after it.

    Each entry: {when, title, accent, text, src, chips, metrics}. Briefings are cached per plan state,
    so reruns never call Claude twice for the same update.
    """
    m, fifo = metrics["8am"], metrics["fifo"]
    text, src = cached_briefing("8am", plan_8am, metrics, "8am")
    out = [{"when": "8:00 a.m.", "title": "Morning briefing, before crews go out", "accent": BRIEF_COLORS["morning"],
            "text": text, "src": src, "metrics": metrics,
            "chips": [(f"{m['safety']} safety tickets", "good"), (f"{m['n']} jobs · 8 crews", "neutral"),
                      (f"{m['safety'] - fifo['safety']:+d} safety vs oldest-first", "good")]}]
    for i, entry in enumerate(log):
        bundle = build_day(plan_8am, metrics, log[:i + 1])
        if "error" in bundle:
            break
        ev = {**entry, "effect_text": day_live.effect_text(entry, bundle["effects"][-1]),
              "updates": [day_live.describe_event(e) for e in log[:i + 1]]}
        text, src = cached_briefing(f"update:{_digest(log[:i + 1])}", bundle["plan"], bundle["metrics"], "noon",
                                    bundle["changes"], event=ev)
        nm = bundle["metrics"]["noon"]
        lost = nm.get("safety_dropped", 0)
        chips = [(day_live.describe_event(entry), "bad"), (f"{nm['n']} jobs on plan", "neutral"),
                 (f"{nm.get('moved', 0)} moved · {nm.get('dropped', 0)} deferred so far", "neutral"),
                 (f"{lost} safety dropped", "good" if lost == 0 else "bad")]
        out.append({"when": entry.get("at") or f"Update {i + 1}", "title": f"Update {i + 1}",
                    "accent": BRIEF_COLORS["update"], "text": text, "src": src, "chips": chips,
                    "metrics": bundle["metrics"], "effect": ev["effect_text"], "what": day_live.describe_event(entry)})
    return out


def day_overview_text(timeline: list[dict]) -> tuple[str, str]:
    """The end-of-day overview for the current log (cached). Returns (text, "claude" | "rules" | "numbers")."""
    store = st.session_state.setdefault("overviews", {})
    key = _digest([t["when"] + (t.get("what") or "") for t in timeline])
    if key not in store:
        updates = [{"time": t["when"], "what": t["what"], "effect": t.get("effect", "")} for t in timeline[1:]]
        final = timeline[-1]["metrics"]
        with st.spinner("Writing the end-of-day overview..."):
            try:
                text = llm.day_overview(final, updates)
                src = _source("overview")
            except Exception:
                m = final.get("noon") or final["8am"]
                text, src = (f"End of day: {m['n']} jobs on the final plan, {m['safety']} safety tickets, "
                             f"{m.get('dropped', 0)} jobs deferred to tomorrow."), "numbers"
        store[key] = (text, src)
    return store[key]


def briefings_text(timeline: list[dict], overview: str | None) -> str:
    """All of today's briefings as plain text, for download."""
    parts = [f"{t['when']} - {t['title']}\n{t['text']}" for t in timeline]
    if overview:
        parts.append(f"End of day - Day overview\n{overview}")
    return "Calgary Roads - supervisor briefings, Aug 28, 2026\n\n" + "\n\n".join(parts) + "\n"


def render_briefings_tab(timeline: list[dict]) -> None:
    """Morning briefing, every update's briefing, then the end-of-day overview."""
    st.subheader("Supervisor briefings")
    st.caption("A morning briefing at 8 a.m. before crews go out, a short briefing after every update, and an "
               "end-of-day overview that consolidates the whole day. All written from the plan's own numbers.")
    for t in timeline:
        st.markdown(_brief_card_html(t["when"], t["title"], t["accent"], t["text"], t["chips"]),
                    unsafe_allow_html=True)
        st.caption(SOURCE_NOTES[t["src"]])
    if len(timeline) == 1:
        st.markdown(_brief_card_html("During the day", "Update briefings", BRIEF_COLORS["update"], None, [],
                                     "No updates yet. Each crew change or new job reported on the Dispatch tab "
                                     "adds a briefing here."), unsafe_allow_html=True)

    st.write("")
    ss_ = st.session_state
    closed = ss_.get("day_closed") == _digest([t["when"] + (t.get("what") or "") for t in timeline])
    if not closed:
        st.markdown(_brief_card_html("End of day", "Day overview", BRIEF_COLORS["day"], None, [],
                                     "Close out the day to consolidate every briefing into one overview."),
                    unsafe_allow_html=True)
        if st.button("Close out the day", type="primary", icon=":material/summarize:", key="close_day"):
            ss_.day_closed = _digest([t["when"] + (t.get("what") or "") for t in timeline])
            st.rerun()
        overview = None
    else:
        overview, src = day_overview_text(timeline)
        final = timeline[-1]["metrics"]
        m = final.get("noon") or final["8am"]
        chips = [(f"{len(timeline) - 1} update{'s' if len(timeline) != 2 else ''}", "neutral"),
                 (f"{m['n']} jobs done", "neutral"), (f"{m['safety']} safety tickets", "good"),
                 (f"{m.get('dropped', 0)} carry over", "neutral"),
                 (f"{m.get('safety_dropped', 0)} safety deferred", "good" if not m.get("safety_dropped") else "bad")]
        st.markdown(_brief_card_html("End of day", "Day overview", BRIEF_COLORS["day"], overview, chips),
                    unsafe_allow_html=True)
        st.caption(SOURCE_NOTES[src] + " New updates reopen the day.")
    st.download_button("Download today's briefings", briefings_text(timeline, overview),
                       file_name="briefings_2026-08-28.txt", mime="text/plain", icon=":material/download:")


def render_status(plan: dict, m: dict, n_updates: int) -> None:
    """What the supervisor needs at a glance about the plan on screen."""
    working = sum(1 for c in plan["crews"] if c["jobs"])
    deferred = m.get("dropped")
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_count_html("Crews working", f"{working} of {len(plan['crews'])}",
                            "all crews out on jobs" if working == len(plan["crews"]) else "a crew is out today"),
                unsafe_allow_html=True)
    c2.markdown(_count_html("Jobs on the plan", str(m["n"]),
                            f"{sum(c.get('workers', 4) for c in plan['crews'])} workers across {len(plan['crews'])} crews" if not n_updates else
                            f"after {n_updates} update{'s' if n_updates != 1 else ''}"), unsafe_allow_html=True)
    c3.markdown(_count_html("Safety tickets", str(m["safety"]), "potholes and missing or damaged signs"),
                unsafe_allow_html=True)
    if deferred is None:
        c4.markdown(_count_html("Deferred today", "0", "no changes yet"), unsafe_allow_html=True)
    else:
        c4.markdown(_count_html("Deferred today", str(deferred),
                                f"<b>{m.get('safety_dropped', 0)} safety</b> · {m.get('moved', 0)} moved to "
                                "other crews"), unsafe_allow_html=True)


def _km_to(job: dict, centroid: list) -> float:
    dy = (job["lat"] - centroid[0]) * 111.32
    dx = (job["lon"] - centroid[1]) * 111.32 * math.cos(math.radians(centroid[0]))
    return math.hypot(dx, dy)


def plan_efficiency(plan: dict) -> dict:
    """How a plan spends crew time: safety share, priority per crew, low-priority slots, travel."""
    details = load_ticket_details()
    jobs = [(j, c) for c in plan["crews"] for j in c["jobs"]]
    working = max(1, sum(1 for c in plan["crews"] if c["jobs"]))
    n = max(1, len(jobs))
    return {
        "safety_share": sum(j["safety"] for j, _ in jobs) / n,
        "p_per_crew": sum(j["P"] for j, _ in jobs) / working,
        "low_value": sum(1 for j, _ in jobs if details.get(j["id"], {}).get("weight", 2) <= 1),
        "avg_km": sum(_km_to(j, c["centroid"]) for j, c in jobs) / n,
        "types": Counter(j["type"] for j, _ in jobs),
    }


def jobs_by_type_chart(ours: Counter, theirs: Counter):
    """Grouped horizontal bars: jobs of each type in our plan vs oldest-first."""
    import altair as alt
    rows = [{"Job type": t, "Plan": label, "Jobs": counts.get(t, 0)}
            for t in sorted(set(ours) | set(theirs), key=lambda t: (-ours.get(t, 0), -theirs.get(t, 0), t))
            for label, counts in (("Our agent", ours), ("Oldest-first", theirs))]
    order = [r["Job type"] for r in rows[::2]]
    return (alt.Chart(alt.Data(values=rows))
            .mark_bar(cornerRadiusEnd=4, height=9)
            .encode(y=alt.Y("Job type:N", sort=order, title=None, axis=alt.Axis(labelLimit=180)),
                    yOffset=alt.YOffset("Plan:N", sort=["Our agent", "Oldest-first"]),
                    x=alt.X("Jobs:Q", title="Jobs in the day's plan", axis=alt.Axis(tickMinStep=1, grid=True)),
                    color=alt.Color("Plan:N", sort=["Our agent", "Oldest-first"],
                                    scale=alt.Scale(range=[OURS_COLOR, FIFO_COLOR]),
                                    legend=alt.Legend(orient="top", title=None)),
                    tooltip=["Plan:N", "Job type:N", "Jobs:Q"])
            .properties(height=34 * len(order)))


FLOW = ROOT / "dispatch" / "flow.mmd"
MERMAID_PAGE = """<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600;700&display=swap" rel="stylesheet">
<pre class="mermaid">__SRC__</pre>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<div class="legend">
  <span><i style="background:#eef2ff;border-color:#6366f1"></i>Data</span>
  <span><i style="background:#fff;border-color:#64748b"></i>Planning</span>
  <span><i style="background:#ecfdf5;border-color:#10b981"></i>During the day</span>
  <span><i style="background:#fffbeb;border-color:#f59e0b"></i>What the supervisor sees</span>
</div>
<script>
  mermaid.initialize({startOnLoad: false, theme: "base", securityLevel: "strict",
    flowchart: {curve: "basis", htmlLabels: true, nodeSpacing: 34, rankSpacing: 60, padding: 16},
    themeVariables: {fontFamily: "Inter, system-ui, sans-serif", fontSize: "15px", lineColor: "#94a3b8",
                     edgeLabelBackground: "#ffffff"}});
  // measure the boxes with the real font, or labels get clipped
  document.fonts.load("600 15px Inter").catch(() => {}).then(() => mermaid.run());
</script>
<style>
  body { margin: 0; background: transparent; font-family: Inter, system-ui, sans-serif; }
  pre.mermaid { display: flex; justify-content: center; margin: 0; }
  .node rect, .node path { stroke-width: 1.5px !important; }
  .node rect { rx: 12px; ry: 12px; }
  .nodeLabel { font-weight: 600; }
  .edgeLabel, .edgeLabel p { font-weight: 600; color: #059669; font-size: 13px; }
  .flowchart-link { stroke-width: 1.6px !important; }
  .legend { display: flex; justify-content: center; flex-wrap: wrap; gap: 6px 18px; margin-top: 10px;
            font-size: 13px; color: #64748b; }
  .legend i { display: inline-block; width: 12px; height: 12px; border-radius: 4px; border: 1.5px solid;
              margin-right: 6px; vertical-align: -1px; }
</style>
"""


DIAGRAM_HEIGHT = 330


def render_flow_diagram() -> None:
    """The system at a glance: dispatch/flow.mmd (Mermaid), drawn in the browser."""
    st.subheader("How the system works")
    try:
        src = FLOW.read_text(encoding="utf-8")
    except OSError:
        st.caption("dispatch/flow.mmd is missing.")
        return
    components.html(MERMAID_PAGE.replace("__SRC__", html.escape(src)), height=DIAGRAM_HEIGHT)
    st.caption("The morning plan ranks today's 311 tickets and gives each crew its jobs. When a crew changes or an "
               "urgent job comes in (typed or from a caller), the AI agent reads it and the plan is rebuilt.")
    with st.expander("Mermaid source", icon=":material/code:"):
        st.code(src, language="text")


def render_downtown_3d(plan: dict, show: bool) -> None:
    """Semir's live SUMO traffic sim of downtown Calgary, with today's downtown jobs as pins."""
    st.subheader("3D downtown")
    if sim3d is None or sim3d.LOCATION is None:
        st.caption("The 3D simulation isn't available (simulation/ folder or dispatch/sim3d.py missing).")
        return
    jobs = sim3d.write_pins(plan, {c["crew"]: crew_rgb(c["crew"]) for c in plan["crews"]})
    total = sum(len(c["jobs"]) for c in plan["crews"])
    crews = sorted({j["crew"] for j in jobs})
    who = " and ".join(f"{dot(c)} Crew {c}" for c in crews) or "No crew"
    st.markdown(f"Live SUMO traffic simulation of downtown Calgary (about 3 × 2 km: Downtown Core and the north "
                f"edge of the Beltline). {who} work{'s' if len(crews) == 1 else ''} here: **{len(jobs)} of today's "
                f"{total} jobs** are inside it, drawn as tall pins in each crew's colour.", unsafe_allow_html=True)
    if jobs:
        st.dataframe([{"Crew": j["crew"], "Job": j["type"], "Community": j["community"].title(),
                       "Priority": round(p10(j["P"]), 1), "Safety": "⚠️" if j["safety"] else ""} for j in jobs],
                     hide_index=True, width="stretch")
    if not show:
        return
    if sim3d.running():
        st.iframe(sim3d.URL + "?embed=1", height=640, alt="3D downtown traffic simulation with today's jobs")
        st.caption("Drag to look around, scroll to zoom. The panel on the right toggles the plan pins, traffic and "
                   "live incidents. Pins update within 10 s of a replan.")
        return
    st.info("The 3D simulation isn't running.", icon=":material/view_in_ar:")
    if st.button("Start the 3D sim", icon=":material/play_arrow:", key="start_3d"):
        err = sim3d.start()
        if err:
            st.error(err)
            return
        with st.spinner("Starting SUMO and loading the city (about 15 seconds)..."):
            for _ in range(60):
                if sim3d.running(timeout=1.0):
                    break
                time.sleep(0.5)
        if sim3d.running():
            st.rerun()
        st.error(f"The 3D sim didn't start. See the log at {sim3d.LOG}.")
    st.caption("Or start it yourself: simulation\\calgary3d\\start_3d.bat (needs SUMO from simulation\\setup.bat).")


def render_analysis(data: dict, metrics: dict) -> None:
    """How the agent changes what maintenance crews spend their day on, versus oldest-first."""
    st.subheader("How the agent improves crew efficiency")
    st.caption("Same 8 crews, same assignment method, same 99 field-crew tickets. The only difference is the order the "
               "work is chosen in.")
    render_metrics("Agent", metrics["8am"], metrics["fifo"], metrics["8am"], metrics)
    render_improvement(metrics)

    ours, theirs = plan_efficiency(data["plan_8am"]), plan_efficiency(data["plan_fifo"])
    st.markdown("**Where crew time goes**")
    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(_compare_html("Crew time on safety work", f"{ours['safety_share']:.0%}",
                              f"{theirs['safety_share']:.0%}", "share of today's job slots", 2.2),
                unsafe_allow_html=True)
    gain = ours["p_per_crew"] / theirs["p_per_crew"] - 1 if theirs["p_per_crew"] else 0
    c2.markdown(_compare_html("Priority served per crew", f"{ours['p_per_crew']:.1f}", f"{theirs['p_per_crew']:.1f}",
                              f"<b>{gain:+.0%}</b> priority per crew-day", 2.2), unsafe_allow_html=True)
    c3.markdown(_compare_html("Slots on low-priority work", str(ours["low_value"]), str(theirs["low_value"]),
                              "parking, waste and cart jobs (type weight 1)", 2.2), unsafe_allow_html=True)
    c4.markdown(_compare_html("Average distance to job", f"{ours['avg_km']:.1f}", f"{theirs['avg_km']:.1f}",
                              "km from the crew's zone centre (straight line)", 2.2), unsafe_allow_html=True)

    left, right = st.columns([1.1, 1], gap="large")
    with left:
        st.markdown("**What each plan sends crews to**")
        try:
            st.altair_chart(jobs_by_type_chart(ours["types"], theirs["types"]), width="stretch")
        except Exception:  # altair missing: show the same numbers as a table
            st.dataframe([{"Job type": t, "Our agent": ours["types"].get(t, 0),
                           "Oldest-first": theirs["types"].get(t, 0)}
                          for t in sorted(set(ours["types"]) | set(theirs["types"]))], hide_index=True)
        st.caption("Oldest-first fills slots with whatever was reported first, including parking-sign, waste and "
                   "cart requests. The agent spends those slots on potholes and damaged signs.")
    with right:
        which = st.segmented_control("Map", ["Our 8 a.m. plan", "Oldest-first"], default="Our 8 a.m. plan",
                                     key="analysis_map", label_visibility="collapsed")
        plan = data["plan_fifo"] if which == "Oldest-first" else data["plan_8am"]
        st.pydeck_chart(pdk.Deck(layers=map_layers(plan, None),
                                 initial_view_state=pdk.ViewState(latitude=51.03, longitude=-114.07, zoom=9.6),
                                 map_provider="carto", map_style="light",
                                 tooltip={"html": "{tip}", "style": {"fontSize": "12px"}}),
                        key=f"analysis_map_{which}", height=380,
                        alt="Map comparing the agent's plan with oldest-first")
        st.caption("Colour = crew · large dot = safety ticket")

    noon = st.session_state.noon
    if noon and "plan" in noon:
        nm = noon["metrics"]["noon"]
        st.markdown(f"**Today so far** · after {len(st.session_state.log)} update"
                    f"{'s' if len(st.session_state.log) != 1 else ''}")
        st.markdown(_compare_html("Safety tickets covered — our plan now vs oldest-first at full strength",
                                  str(nm["safety"]), str(metrics["fifo"]["safety"]),
                                  f"{nm.get('moved', 0)} moved · {nm.get('dropped', 0)} deferred · "
                                  f"{nm.get('safety_dropped', 0)} safety dropped", 3.0), unsafe_allow_html=True)


VOICE_WIDGET = ROOT / "dispatch" / "voice_call.html"


@st.cache_resource
def voice_api_port() -> int | None:
    """Start the local voice API once per dashboard process (see dispatch/voice_server.py)."""
    try:
        return voice_server.start()
    except Exception:
        return None


def _new_call(greet: bool = True) -> None:
    """Start a call in the shared store. greet: say the opening line in typed mode (not on first load)."""
    ss_ = st.session_state
    ss_.call_count = ss_.get("call_count", 0) + 1
    ss_.call_id = voice_server.new_call(ss_.caller_plan, ss_.call_count)
    ss_.speak_text = intake.OPENING if greet else None


def _typed(plan: dict) -> None:
    """A typed caller line: same conversation store as the voice call."""
    ss_ = st.session_state
    text = (ss_.get(f"caller_msg_{ss_.call_id}") or "").strip()
    if text:
        with st.spinner("Agent is replying..."):
            out = voice_server.turn(ss_.call_id, text)
        ss_.speak_text = out["reply"]


def _speak_once(text: str, use_voice: bool) -> None:
    """Say a typed-mode reply: ElevenLabs when a key is set, else the browser's built-in voice."""
    audio = voice.speak(text) if use_voice else None
    if audio:
        st.audio(audio, format="audio/mp3", autoplay=True)
        return
    components.html(  # browser speech synthesis: free, offline, no key
        f"<script>/*{hash(text) ^ id(text)}*/const u=new SpeechSynthesisUtterance({json.dumps(text)});u.rate=1.05;"
        f"speechSynthesis.cancel();speechSynthesis.speak(u);</script>", height=0)


def _caller_map(state: dict, plan: dict) -> None:
    """The caller's pin, the nearest crew's jobs in colour, everything else faint."""
    lat, lon = state["lat"], state["lon"]
    nearest = min(plan["crews"], key=lambda c: intake._km(lat, lon, *c["centroid"]))
    r, g, b = CREW_COLORS[(nearest["crew"] - 1) % len(CREW_COLORS)]
    jobs = [{**j, "crew": c["crew"], "color": [r, g, b, 230] if c is nearest else [150, 150, 150, 90],
             "radius": 6 if c is nearest else 3} for c in plan["crews"] for j in c["jobs"]]
    pin = [{"lat": lat, "lon": lon, "type": "Caller report", "where": state["where"]}]
    layers = [
        pdk.Layer("ScatterplotLayer", jobs, get_position="[lon, lat]", get_fill_color="color", get_radius="radius",
                  radius_units="'pixels'", pickable=True),
        pdk.Layer("ScatterplotLayer", pin, get_position="[lon, lat]", get_fill_color=[214, 39, 40], get_radius=11,
                  radius_units="'pixels'", stroked=True, get_line_color=[255, 255, 255], line_width_min_pixels=3,
                  pickable=True),
        pdk.Layer("TextLayer", [{"lat": nearest["centroid"][0], "lon": nearest["centroid"][1],
                                 "label": f"Crew {nearest['crew']}"}], get_position="[lon, lat]", get_text="label",
                  get_color=[r, g, b], get_size=14, font_weight=700),
    ]
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=12),
                             map_provider="carto", map_style="light",
                             tooltip={"text": "{type} {where}"}),
                    key=f"caller_map_{lat:.5f}_{lon:.5f}", height=260,
                    alt="Map with the caller's reported location and the nearest crew's jobs")
    approx = " · approximate (one street of the pair)" if state["precision"] == "approximate" else ""
    st.caption(f":material/location_on: **{state['where']}**{approx} · red = caller's report, colour = crew "
               f"{nearest['crew']}'s jobs today")


def _render_call(call_id: str, plan: dict, on_add=None) -> None:
    """Transcript, map and ticket for one call; re-run every second so voice turns appear live."""
    call = voice_server.get(call_id)
    if call is None:
        return
    for m in call["history"]:
        with st.chat_message(m["role"], avatar=":material/call:" if m["role"] == "user" else ":material/support_agent:"):
            st.write(m["content"])
    if call["source"]:
        st.caption({"claude": ":material/auto_awesome: Replies by Claude.",
                    "rules": ":material/rule: Rule-based call-taker (Claude unavailable: no API key or no "
                             "connection)."}[call["source"]])
    state = call["state"]
    if state["lat"] is not None:
        _caller_map(state, plan)
    elif state["failed_location"]:
        st.caption(f":material/wrong_location: Couldn't place “{state['failed_location']}” on the map yet.")
    t = call["ticket"]
    if call["done"] and not t:
        st.caption(":material/call_end: The caller ended the call; nothing was logged. Press New call to start again.")
    if t:
        with st.container(border=True):
            st.markdown(f"**:material/assignment_turned_in: Ticket {t['id']} logged** · {t['type']}"
                        )
            if t["safety"]:
                st.badge("Safety ticket", color="red")
            where = f"- **Where:** {t['where']}" + (f" ({t['community'].title()})" if t["community"] else "")
            nearest = f"- **Nearest crew:** Crew {t['crew']} ({t['zone']}), {t['crew_km']} km from its zone centre"
            if on_add is None:
                st.markdown("\n".join([where, f"- **Priority:** {p10(t['P']):.1f} / 10 (P {t['P']:.2f}, type weight {t['P']:g}, new today, "
                                        f"1 report)", nearest, f"- **Today's plan:** {t['fit']}"]))
            else:
                sev = caller_severity(t)
                st.markdown("\n".join([where, f"- **Severity:** {day_live.SEVERITY_LABELS[sev]}",
                                        nearest]))
            st.caption(f"Caller said: “{t['details']}”")
            if on_add is not None:
                added = call_id in st.session_state.get("added_calls", set())
                if st.button("Added to today's plan" if added else "Add to today's plan", type="primary",
                             icon=":material/check:" if added else ":material/playlist_add:", disabled=added,
                             key=f"add_call_{call_id}", width="stretch"):
                    on_add(call_id, t)
                    st.rerun(scope="app")  # this runs inside the live fragment: refresh the whole page
                result = st.session_state.get("added_effects", {}).get(call_id)
                if result:
                    st.success(result, icon=":material/check_circle:")


def caller_severity(ticket: dict) -> int:
    """A caller ticket's severity on the live-day scale: type weight 3 -> high (2), 2 -> routine (1), 1 -> low (0)."""
    return max(0, min(2, int(round(ticket["P"])) - 1))


def render_caller_intake(plan: dict, on_add=None) -> None:
    """Caller report: talk (or type) to the 311 agent until it knows what the problem is and exactly where."""
    if intake is None:
        st.caption("Caller intake isn't available (dispatch/intake.py failed to import).")
        return
    ss_ = st.session_state
    ss_.caller_plan = plan
    if not ss_.get("call_id") or voice_server.get(ss_.call_id) is None:
        _new_call(greet=False)
    use_voice = voice.has_voice_key()
    port = voice_api_port()

    st.caption("Talk to the 311 agent as the caller. It asks follow-up questions until it knows what the "
               "problem is and exactly where, then logs a scored ticket.")
    if port:
        hint = ("Natural voice: ElevenLabs." if use_voice else
                "Voice: your browser's built-in voice. Add ELEVENLABS_API_KEY to .env for a natural voice.")
        html = (VOICE_WIDGET.read_text(encoding="utf-8").replace("__API__", f"http://127.0.0.1:{port}")
                .replace("__CALL__", ss_.call_id).replace("__OPENING__", json.dumps(intake.OPENING))
                .replace("__VOICE__", hint + " Works in Chrome or Edge; allow the microphone when asked."))
        components.html(html, height=104)
    else:
        st.caption(":material/mic_off: The voice service couldn't start (no free port from 8502). Type below.")

    c_speak, c_new = st.columns([3, 2], vertical_alignment="center")
    c_speak.toggle("Read typed replies aloud", key="speak_replies", value=False,
                   help="For typed messages. The voice call always speaks its replies.")
    c_new.button("New call", icon=":material/restart_alt:", on_click=_new_call, key="new_call", width="stretch")

    st.fragment(_render_call, run_every=1.0)(ss_.call_id, plan, on_add)

    text = ss_.pop("speak_text", None)
    if text and ss_.get("speak_replies"):
        _speak_once(text, use_voice)
    call = voice_server.get(ss_.call_id)
    if call and not call["done"]:
        st.chat_input("Or type what the caller says...", key=f"caller_msg_{ss_.call_id}",
                      on_submit=_typed, args=(plan,))


def render_crew_list(plan: dict, out_crews: set) -> None:
    """Crew titles only: colour dot, Crew N, zone. Clicking one switches to it."""
    with st.container(horizontal=True, vertical_alignment="center"):
        st.subheader("Crews")
        st.space("stretch")
        if st.session_state.selected_crew:
            st.button("Show all crews", icon=":material/zoom_out_map:", type="tertiary", on_click=show_all,
                      key="show_all")
    with st.container(border=True, gap=None):
        for c in plan["crews"]:
            is_sel = c["crew"] == st.session_state.selected_crew
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(dot(c["crew"]), unsafe_allow_html=True, width="content")
                label = (f"**Crew {c['crew']}** · {c['zone']} · {c.get('workers', 4)} people"
                         + (" · out today" if c["crew"] in out_crews else ""))
                st.button(label, key=f"crew_{c['crew']}", type="secondary" if is_sel else "tertiary",
                          on_click=select_crew, args=(c["crew"],))


# --- page ------------------------------------------------------------------

st.set_page_config(page_title="311 Dispatch Agent", layout="wide")
data = load_outputs(outputs_stamp())
metrics = data["metrics"]
ss = st.session_state
ss.setdefault("update_text", "")
ss.setdefault("parsed", None)
ss.setdefault("parse_error", None)
ss.setdefault("received", "")
ss.setdefault("noon", None)
ss.setdefault("applied", None)
ss.setdefault("selected_job", None)
ss.setdefault("selected_crew", None)
ss.setdefault("log", [])          # every update reported today, in order; the plan is replayed from it
ss.setdefault("pending", None)    # a new job whose location still has to be clicked on the map
ss.setdefault("locating", None)   # a new job waiting for its address lookup
ss.setdefault("pick_n", 0)


# --- the day's updates: parse -> (locate) -> apply, with no confirmation step ---

def _rebuild() -> None:
    """Replay the whole log on the 8 a.m. plan and refresh what the dashboard shows."""
    if not ss.log:
        ss.noon = None
        return
    bundle = build_day(data["plan_8am"], metrics, ss.log)
    if "error" in bundle:
        ss.noon = bundle
        return
    last = ss.log[-1]
    ev = {**last, "effect_text": day_live.effect_text(last, bundle["effects"][-1]),
          "updates": [day_live.describe_event(e) for e in ss.log]}
    ss.noon = {**bundle, "event": ev, "log": ss.log}


def _apply(event: dict, received: str) -> None:
    """Add the update to today's log and replan straight away: the supervisor's only step is submitting."""
    entry = {k: v for k, v in event.items() if k not in ("why", "candidates")}
    entry["received"] = received
    entry["at"] = datetime.now().strftime("%I:%M %p").lstrip("0").replace("AM", "a.m.").replace("PM", "p.m.")
    ss.parsed = ss.parse_error = ss.pending = ss.locating = None
    ss.pop("view", None)
    if (entry["event"] == "crew_partial" and float(entry.get("capacity", 0.0)) >= 1.0
            and day_live.slots_now(data["plan_8am"], ss.log).get(entry["crew"], 5) >= crew_limit(entry["crew"])):
        ss.applied = {"event": event, "received": received, "noop": True,
                      "effect": f"Crew {entry['crew']} was already at full strength, so nothing changed."}
        return
    ss.log = ss.log + [entry]
    _rebuild()
    effect = ss.noon["event"].get("effect_text", "") if ss.noon and "event" in ss.noon else ""
    ss.applied = {"event": event, "received": received, "effect": effect}


def _run(text: str, only_crew: int | None = None) -> None:
    """Read the update and apply it. Only an ambiguous message stops to ask one question."""
    ss.received = text.strip()
    ss.applied = ss.pending = None
    event, err = safe_parse(ss.received, only_crew)
    if event and event["event"] == "new_job":
        ss.parsed = ss.parse_error = None
        ss.locating = {"event": event, "received": ss.received}   # located in the page body, with a spinner
    elif event and event["event"] != "unclear":
        _apply(event, ss.received)
    else:
        ss.parsed, ss.parse_error = event, err


def _submit() -> None:
    if ss.update_text.strip():
        _run(ss.update_text)


def _answer() -> None:
    """Re-read the original message together with the supervisor's answer to the question."""
    answer = ss.followup.strip()
    if not answer:
        return
    if answer.isdigit() or answer.lower() in NUMBER_WORDS:
        answer = f"crew {answer}"  # a bare "4" answers "Which crew?"
    ss.followup = ""
    _run(f"{ss.received}. {answer}")


def _manual(crew: int, kind: str) -> None:
    out = kind == "crew_out"
    _apply({"event": kind, "crew": crew, "capacity": 0.0 if out else 0.5, "question": None,
            "source": "manual"}, ss.get("received", ""))


def _place_pending(lat: float, lon: float) -> None:
    job = ss.pending["event"]["job"]
    day_live.finish_location(job, lat, lon, "map click")
    _apply({**ss.pending["event"], "source": ss.pending["event"].get("source")}, ss.pending["received"])

def _add_call(call_id: str, ticket: dict) -> None:
    """Put a caller's logged report on today's plan as a new job (located already), then replan."""
    spec = day_live.make_job_spec(ticket["type"], caller_severity(ticket), ticket["where"], ticket["community"],
                                  ticket["details"])
    day_live.finish_location(spec, ticket["lat"], ticket["lon"], "caller report (OpenStreetMap)")
    _apply({"event": "new_job", "job": spec, "crew": None, "capacity": 1.0, "question": None, "source": "caller"},
           f"Caller: {ticket['details']}")
    ss.added_calls = ss.get("added_calls", set()) | {call_id}
    effect = (ss.applied or {}).get("effect") or "Added to today's plan; the day was replanned."
    ss.added_effects = {**ss.get("added_effects", {}), call_id: effect}


def _cancel_pending() -> None:
    ss.pending = None


def _undo() -> None:
    ss.log = ss.log[:-1]
    ss.applied = None
    _rebuild()
    ss.pop("view", None)


def _reset() -> None:
    ss.log = []
    ss.noon = ss.applied = ss.pending = None
    ss.pop("view", None)


if ss.locating:  # find the address (can take a few seconds), else ask for a click on the map
    with st.spinner("Finding the location..."):
        job = ss.locating["event"]["job"]
        located = day_live.locate(job)
    if located:
        _apply(ss.locating["event"], ss.locating["received"])
    else:
        ss.pending = {"event": ss.locating["event"], "received": ss.locating["received"],
                      "note": job.get("locate_note") or ""}
        ss.pick_n += 1
        ss.locating = None
    st.rerun()


st.title("Who should 311 send next?")
st.caption("Calgary Roads dispatch · 8 crews, 32 workers · Open Calgary 311 tickets (Aug 25–27, 2026), "
           "planned for Aug 28, 2026")
if data["fake"]:
    st.warning("Showing sample data: run `python -m dispatch.run` to generate dispatch/outputs/.")

timeline = day_timeline(data["plan_8am"], metrics, ss.log) if day_live else []
tab_labels = [":material/local_shipping: Dispatch", f":material/campaign: Briefings ({len(timeline)})",
              ":material/insights: Analysis", ":material/call: Caller report"]
# The Briefings label changes with each update, which resets the tabs; stay on the tab the user had open.
_was = (ss.get("main_tab") or "").split(" (")[0]
tab_dispatch, tab_briefings, tab_analysis, tab_caller = st.tabs(
    tab_labels, default=next((t for t in tab_labels if _was and t.split(" (")[0] == _was), None),
    key="main_tab", on_change="rerun")

# --- dispatch: what the supervisor needs to run the day ---------------------------
with tab_dispatch:
    live = bool(ss.noon and "plan" in ss.noon)
    views = ["Agent - noon", "Agent"] if live else ["Agent"]
    if ss.get("view") not in views:
        ss.view = views[0]
    if live:
        n_up = len(ss.log)
        view = st.segmented_control("Plan shown", views, key="view", label_visibility="collapsed",
                                    format_func={"Agent - noon": f"Current plan ({n_up} update{'s' if n_up != 1 else ''})",
                                                 "Agent": "8 a.m. plan"}.get) or views[0]
    else:
        view = "Agent"

    out_crews, slots = set(), None
    if view == "Agent":
        plan, m, changes = data["plan_8am"], metrics["8am"], None
    else:
        plan, m, changes = ss.noon["plan"], ss.noon["metrics"]["noon"], ss.noon["changes"]
        slots = ss.noon["slots"]
        out_crews = {c for c, n in slots.items() if n == 0}

    render_status(plan, m, len(ss.log) if view != "Agent" else 0)
    st.write("")

    sel_job, sel_job_crew = find_job(plan, changes, ss.selected_job)
    sel_crew = find_crew(plan, ss.selected_crew)

    left, right = st.columns([3, 2])
    with left:
        if ss.pending:  # a new job the message couldn't place: the supervisor clicks where it is
            pend_job = ss.pending["event"]["job"]
            with st.container(border=True):
                st.markdown(f":material/ads_click: **Click the map to place the {pend_job['label'].lower()}**")
                why = ss.pending.get("note")
                st.caption("I couldn't place it from the message" + (f" ({why})." if why else "."))
                st.button("Cancel", key="cancel_pending", on_click=_cancel_pending)
            clicked = render_map(plan, changes, sel_job, sel_crew, tag=f"{ss.pick_n}", pick=True)
            if clicked:
                _place_pending(*clicked)
                st.rerun()
        else:
            ev_tag = ss.noon["event"] if live and "event" in ss.noon else {}
            hit = render_map(plan, changes, sel_job, sel_crew,
                             tag=f"{view}_{len(ss.log)}_{ev_tag.get('event')}_{ev_tag.get('crew')}_{ev_tag.get('capacity')}")
            if hit:  # a click on the map selects that job (and its crew) or that crew
                if hit[0] == "job":
                    select_job(hit[1], hit[2])
                else:
                    select_crew(hit[1])
                st.rerun()

        # under the map: the selected crew and job, or the latest briefing when nothing is selected
        if sel_crew and ss.selected_job:
            c_list, c_job = st.columns([1, 1])
            with c_list:
                st.subheader("Selected crew")
                render_crew_panel(sel_crew, changes, out_crews)
            with c_job:
                st.subheader("Selected job")
                render_job_detail(sel_job, sel_job_crew, view)
        elif sel_crew:
            st.subheader("Selected crew")
            render_crew_panel(sel_crew, changes, out_crews)
        elif ss.selected_job:
            st.subheader("Selected job")
            render_job_detail(sel_job, sel_job_crew, view)
        elif timeline:
            st.subheader("Latest briefing")
            t = timeline[-1]
            st.markdown(_brief_card_html(t["when"], t["title"], t["accent"], t["text"], t["chips"]),
                        unsafe_allow_html=True)
            st.caption(f"{SOURCE_NOTES[t['src']]} Every briefing and the end-of-day overview are on the "
                       "Briefings tab.")

    with right:
        render_crew_list(plan, out_crews)

        st.subheader("Report an update")
        with st.container(horizontal=True, vertical_alignment="center"):
            st.caption("Crew changes and new urgent jobs. Every update builds on the last one and adds a briefing.")
            if claude_ready():
                st.badge("Claude", icon=":material/auto_awesome:", color="green",
                         help="An API key is set: Claude reads updates, with the rule-based parser as backup.")
            else:
                st.badge("Rule-based", icon=":material/rule:", color="gray",
                         help="No ANTHROPIC_API_KEY on this machine: the rule-based parser reads updates.")
        with st.form("crew_update", clear_on_submit=True, border=False):
            st.text_area("Update", key="update_text", height=80, label_visibility="collapsed",
                         placeholder="e.g. crew 4 called in sick, or: sinkhole at 8 Ave SW and 4 St SW")
            st.form_submit_button("Submit update", type="primary", icon=":material/send:", on_click=_submit,
                                  width="stretch")
        with st.expander("Examples"):
            for text in QUICK_FILLS:
                st.button(text, type="tertiary", on_click=_run, args=(text,), key=f"example_{text}")

        if ss.get("applied"):
            ev, got = ss.applied["event"], ss.applied["received"]
            with st.container(border=True):
                st.markdown(f":material/check_circle: **{'No change.' if ss.applied.get('noop') else 'Replanned.'}** "
                            f"{describe(ev)}")
                if ss.applied.get("effect"):
                    st.markdown(ss.applied["effect"])
                if got:
                    st.caption(f"“{got}”")
                st.caption(read_by(ev))
                if ev.get("event") == "new_job":
                    job = ev["job"]
                    where = job.get("resolved") or job.get("address") or ""
                    st.caption(f":material/location_on: Located by {job.get('location_source', 'map click')}"
                               + (f": {where}" if where else "") + f" · severity {job['severity']} of 3")

        if ss.get("parse_error") and not ss.parsed:
            with st.container(border=True):
                st.error("Couldn't read that update automatically. Pick the crew and what happened:")
                crew = st.selectbox("Crew", range(1, 9), index=3)
                kind = st.radio("Status", ["Out for the day", "Short-handed (50%)"])
                st.button("Use this update", on_click=_manual,
                          args=(crew, "crew_out" if kind.startswith("Out") else "crew_partial"))

        if ss.parsed:  # only reached for an ambiguous message
            event = ss.parsed
            with st.container(border=True):
                st.markdown("**:material/help: One detail needed**")
                st.caption(f"“{ss.get('received', '')}”")
                st.markdown(f"**{event.get('question') or 'Which crew is affected?'}**")
                picks = event.get("candidates") or []
                if picks:  # several crews named: one click picks the one to replan
                    for col, n in zip(st.columns(len(picks)), picks):
                        col.button(f"Crew {n}", key=f"pick_{n}", on_click=_run, args=(ss.received, n), width="stretch")
                elif event.get("crew"):  # crew known, status unknown
                    st.button("Out for the day", key="manual_out", icon=":material/person_off:", width="stretch",
                              on_click=_manual, args=(event["crew"], "crew_out"))
                    st.button("Short-handed (50%)", key="manual_part", icon=":material/group_remove:", width="stretch",
                              on_click=_manual, args=(event["crew"], "crew_partial"))
                else:
                    st.text_input("Your answer", key="followup", placeholder="e.g. crew 4, or: pothole at 5 Ave SW",
                                  on_change=_answer)
                    st.button("Send answer", icon=":material/reply:", on_click=_answer)

        if ss.noon and "error" in ss.noon:
            st.error(f"Replan engine isn't available: {ss.noon['error']}")
        if ss.log:
            with st.container(border=True):
                st.markdown(f"**Today's updates ({len(ss.log)})**")
                st.markdown("\n".join(f"{i}. {e.get('at', '')} · {day_live.describe_event(e)}"
                                      if e.get("at") else f"{i}. {day_live.describe_event(e)}"
                                      for i, e in enumerate(ss.log, 1)))
                st.button("Undo last update", icon=":material/undo:", on_click=_undo, width="stretch")
                st.button("Reset to 8 a.m. plan", icon=":material/restart_alt:", on_click=_reset, width="stretch")

# --- briefings: 8 a.m., every update, end of day -----------------------------------
with tab_briefings:
    if timeline:
        render_briefings_tab(timeline)
    else:
        st.info("Briefings need the live-day engine (dispatch/live.py).")

# --- analysis: the case for the agent ------------------------------------------------
with tab_analysis:
    render_flow_diagram()
    st.divider()
    render_analysis(data, metrics)
    st.divider()
    render_downtown_3d(ss.noon["plan"] if ss.noon and "plan" in ss.noon else data["plan_8am"],
                       show=getattr(tab_analysis, "open", True))  # the 3D city loads only while this tab is open


# --- caller report: talk to the 311 agent; a logged call can go onto today's plan --------------
with tab_caller:
    st.subheader("Caller report")
    render_caller_intake(ss.noon["plan"] if ss.noon and "plan" in ss.noon else data["plan_8am"],
                         on_add=_add_call if day_live else None)
