"""Streamlit demo: `streamlit run dispatch/app.py` (run `python -m dispatch.run` first)."""
import hashlib
import html
import json
import re
import sys
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


@st.cache_data
def load_outputs() -> dict:
    files = {"plan_8am": "plan_8am.json", "plan_fifo": "plan_fifo.json", "metrics": "metrics.json"}
    try:
        data = {k: json.loads((OUT / f).read_text(encoding="utf-8")) for k, f in files.items()}
    except (OSError, ValueError):  # missing or unreadable outputs: show sample data, never a traceback
        return _fake_outputs()
    data["fake"] = False
    return data


@st.cache_data
def load_event() -> dict | None:
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

# The case asks what the supervisor hears at 8 a.m. and at noon: one slot each, in time order.
BRIEF_SLOTS = {  # key -> (time, title, icon, accent colour)
    "8am": ("8:00 a.m.", "Morning plan", "", "rgb(31,119,180)"),
    "noon": ("12:00 noon", "Midday replan", "", "rgb(255,127,14)"),
}
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
                f"It keeps its top {round(5 * cap)} jobs; the rest will be reassigned or deferred.")
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
    """What the selected job is, and how its priority was scored."""
    job_id = st.session_state.selected_job
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"**Ticket {job_id}**")
            st.space("stretch")
            st.button("Close", icon=":material/close:", type="tertiary", on_click=select_job, args=(None,),
                      key="clear_job")
        if job is None:
            st.caption(f"This ticket isn't on the {view} plan. Switch plans or close it.")
            return
        with st.container(horizontal=True, vertical_alignment="center"):
            st.markdown(f"### {job['type']}")
            job_badges(job, set())
        where = f"Crew {crew['crew']} · {crew['zone']} zone" if crew else "Deferred to tomorrow"
        st.metric("Priority", f"{p10(job['P']):.1f} / 10")

        info = load_ticket_details().get(job_id)
        if job.get("new"):  # reported after 8 a.m.: no ticket history, so show how it was read and scored
            sev = day_live.SEVERITY_LABELS.get(job.get("severity"), "high") if day_live else job.get("severity")
            rows = [("Assigned to", where), ("Community", job["community"].title()),
                    ("Address", job.get("address") or "not given"),
                    ("Reported", "today, after 8 a.m."),
                    ("Severity", f"{job.get('severity')} of 3 ({sev})")]
            score_text = (f"severity {job.get('severity')} sets P {job['P']:.2f}. Emergencies (severity 3) "
                          "always rank above every job already on the plan.")
        else:
            rows = [("Assigned to", where), ("Community", job["community"].title()),
                    ("Service type", job["service_name"]),
                    ("Days open", f"{info['days_open']} (reported {info['requested_date']})" if info else "n/a"),
                    ("Reports", f"{job['reports']} ({job['reports'] - 1} duplicate)" if job["reports"] > 1
                     else "1")]
            explain = getattr(scoring, "explain", None)
            score_text = (explain(info["weight"], info["days_open"], job["reports"])
                          if info and explain else f"P {job['P']:.2f}")
        rows.append(("Location", f"{job['lat']:.5f}, {job['lon']:.5f}"))
        st.markdown("\n".join(f"- **{k}:** {v}" for k, v in rows))
        st.caption(f"Raw score: {score_text}")


def render_crew_panel(crew: dict, changes: dict | None, out_crews: set) -> None:
    """The selected crew's jobs in priority order; each row selects that job."""
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    jobs = sorted(crew["jobs"], key=lambda j: -j["P"])
    with st.container(border=True):
        st.markdown(f"{dot(crew['crew'], '1.4em')} **Crew {crew['crew']}** · {crew['zone']} zone",
                    unsafe_allow_html=True)
        if not jobs:
            st.caption("Out today, no jobs." if crew["crew"] in out_crews else "No jobs.")
            return
        st.caption(f"{len(jobs)} jobs · {sum(j['safety'] for j in jobs)} safety · priority out of 10 on the right")
        for rank, j in enumerate(jobs, 1):
            chosen = j["id"] == st.session_state.selected_job
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.button(f"{rank}. {j['type']} · {j['community'].title()}", key=f"crewjob_{j['id']}",
                          type="secondary" if chosen else "tertiary",
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

BOX = "border:1px solid rgba(128,128,128,.35);border-radius:.6rem;padding:.8rem 1rem;height:100%"


def _stage_html(label: str, big: str, of: str, line: str, color: str) -> str:
    return (f"<div style='{BOX}'>"
            f"<div style='font-size:.8rem;font-weight:700;letter-spacing:.05em;opacity:.75'>{label}</div>"
            f"<div style='display:flex;align-items:baseline;gap:.4rem;margin:.2rem 0'>"
            f"<span style='font-size:2.8rem;font-weight:800;line-height:1;color:{color}'>{big}</span>"
            f"<span style='font-size:1.1rem;opacity:.7'>{of}</span></div>"
            f"<div style='font-size:.85rem;opacity:.85'>{line}</div></div>")


def render_results(metrics: dict) -> None:
    """The improvement story in one place: FIFO -> our 8 a.m. plan -> after a crew drops out."""
    fifo, am, noon = metrics.get("fifo"), metrics.get("8am"), metrics.get("noon")
    event = load_event() or {"crew": 4}
    st.subheader("Results")
    c1, c2, c3 = st.columns(3)
    c1.markdown(_stage_html("OLDEST-FIRST (FIFO)", str(fifo["safety"]), f"of {fifo['n']}",
                            "jobs are safety tickets: today's baseline", FIFO_COLOR), unsafe_allow_html=True)
    c2.markdown(_stage_html("OUR 8 A.M. PLAN", str(am["safety"]), f"of {am['n']}",
                            f"jobs are safety tickets: <b>+{am['safety'] - fifo['safety']}</b> with the same crews",
                            OURS_COLOR), unsafe_allow_html=True)
    if not noon:
        c3.markdown(_stage_html(f"CREW {event['crew']} OUT", "–", "", "run python -m dispatch.run", FIFO_COLOR),
                    unsafe_allow_html=True)
        return
    held = "held at" if noon["safety"] >= am["safety"] else "now"
    c3.markdown(_stage_html(f"AFTER CREW {event['crew']} CALLS IN SICK", str(noon["n"]), "jobs done",
                            f"safety coverage {held} <b>{noon['safety']}</b>; only lower-priority work deferred"
                            if noon.get("safety_dropped", 0) == 0 else
                            f"safety coverage {held} <b>{noon['safety']}</b>",
                            OURS_COLOR), unsafe_allow_html=True)

    dropped = (metrics.get("changes") or {}).get("dropped_jobs", [])
    top_deferred = max((p10(j["P"]) for j in dropped), default=None)
    share = noon["P"] / am["P"] if am["P"] else 0
    st.write("")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Jobs moved", noon.get("moved", 0), border=True,
              help=f"Crew {event['crew']}'s jobs handed to one of the 3 nearest crews")
    k2.metric("Jobs deferred", noon.get("dropped", 0), border=True,
              help="Pushed to tomorrow" + (f"; the most urgent scored {top_deferred:.1f}/10" if dropped else ""))
    k3.metric("Safety tickets dropped", noon.get("safety_dropped", 0), border=True)
    k4.metric("Priority served", f"{share:.0%}", border=True,
              help=f"Total P {noon['P']:.2f} of the 8 a.m. plan's {am['P']:.2f}")
    lost = (1 - noon["n"] / am["n"]) if am["n"] else 0
    kept = "and every safety ticket" if noon.get("safety_dropped", 0) == 0 else \
        f"but dropped {noon['safety_dropped']} safety ticket(s)"
    st.caption(f"The crew update removed {lost * 100:.3g}% of today's job slots; the replan still served "
               f"{share:.0%} of the 8 a.m. priority, {kept}.")


def render_how_it_works(metrics: dict) -> None:
    """Scoring, robustness and raw numbers, tucked away."""
    with st.expander("How the numbers are computed"):
        if hasattr(scoring, "FORMULA"):
            st.markdown(f"**Priority score.** `{scoring.FORMULA}`. {getattr(scoring, 'SCALE_NOTE', '')}")
        st.markdown("**Safety tickets** are potholes and missing or damaged signs (type weight 3).")
        st.markdown("**Robustness.** We re-ran the plan with every type weight changed by ±1 (16 variations). "
                    "Our agent covered more safety tickets than oldest-first in all 16, by +9 to +15.")
        if improvement:
            try:
                rows = improvement.comparison_rows(metrics, load_event())
                st.dataframe([{"Stage": f"{r['stage']} · {r['what']}", "Jobs": r["m"]["jobs"],
                               "Safety covered": r["m"]["safety"], "Total P": r["m"]["P"],
                               "Moved": r["m"].get("moved"), "Deferred": r["m"].get("deferred"),
                               "Safety dropped": r["m"].get("safety_dropped")}
                              for r in rows if r["m"]], hide_index=True)
            except Exception:
                pass


def render_live_strip(m: dict, fifo: dict, n_updates: int) -> None:
    """Numbers for the plan after the supervisor's own updates today."""
    a, b, c, d = st.columns(4)
    a.metric("Safety tickets covered", m["safety"], f"{m['safety'] - fifo['safety']:+d} vs oldest-first")
    b.metric("Jobs moved", m.get("moved", 0))
    c.metric("Jobs deferred", m.get("dropped", 0))
    d.metric("Safety tickets dropped", m.get("safety_dropped", 0))
    note = f"After {n_updates} update{'s' if n_updates != 1 else ''} today."
    if m.get("added"):
        note += (f" Includes {m['added']} job{'s' if m['added'] != 1 else ''} reported after 8 a.m., "
                 "which oldest-first never saw.")
    st.caption(note)

def _sentences(text: str) -> list[str]:
    """Split a briefing into sentences without breaking on 'a.m.' / 'p.m.'."""
    guarded = re.sub(r"\b([ap])\.m\.", r"\1<dot>m<dot>", text.strip())
    parts = [p.replace("<dot>", ".") for p in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", guarded) if p.strip()]
    if len(parts) > 1 and len(parts[0].split()) <= 3:  # "Good morning." opens the next point, not its own
        parts[:2] = [f"{parts[0]} {parts[1]}"]
    return parts


def _brief_card_html(slot: str, text: str | None, chips: list[tuple[str, str]], active: bool) -> str:
    """One timeline card: time + title, highlight chips, then the briefing as a numbered list.

    text=None renders the waiting state (no crew update yet). Briefing text is escaped: it may come
    from Claude.
    """
    when, title, icon, accent = BRIEF_SLOTS[slot]
    border = f"2px solid {accent}" if active else "1px solid rgba(128,128,128,.35)"
    tag = (f"<span style='margin-left:auto;font-size:.72rem;font-weight:600;padding:.1rem .5rem;"
           f"border-radius:1rem;background:{accent};color:white'>on the map</span>" if active else "")
    icon_html = f"<span style='font-size:1.25rem'>{icon}</span>" if icon else ""
    head = (f"<div style='display:flex;align-items:center;gap:.5rem;flex-wrap:wrap'>{icon_html}"
            f"<span style='font-weight:800;color:{accent}'>{when}</span>"
            f"<span style='font-weight:600;opacity:.85'>· {title}</span>{tag}</div>")
    if text is None:
        return (f"<div style='border:1px dashed rgba(128,128,128,.5);border-left:5px solid {accent};"
                f"border-radius:.6rem;padding:.7rem 1rem;margin-bottom:.4rem;opacity:.6'>{head}"
                f"<div style='font-style:italic;margin-top:.45rem'>Waiting for a crew update. Report one below "
                f"and the noon briefing appears here.</div></div>")
    pills = "".join(f"<span style='display:inline-block;margin:.45rem .35rem 0 0;padding:.15rem .6rem;"
                    f"border-radius:1rem;font-size:.82rem;font-weight:700;{CHIP_TONES[tone]}'>{html.escape(label)}"
                    f"</span>" for label, tone in chips)
    items = "".join(f"<li style='margin:.2rem 0'>{html.escape(s)}</li>" for s in _sentences(text))
    return (f"<div style='border:{border};border-left:5px solid {accent};border-radius:.6rem;"
            f"padding:.7rem 1rem;margin-bottom:.4rem'>{head}<div>{pills}</div>"
            f"<ol style='margin:.55rem 0 0 1.1rem;padding:0;line-height:1.45'>{items}</ol></div>")


def _event_label(event: dict) -> str:
    if event.get("event") == "new_job":
        return f"New job: {event['job'].get('label', 'urgent job')}"
    if event.get("event") == "crew_partial":
        return f"Crew {event.get('crew')} short-handed ({float(event.get('capacity', 0.5)):.0%})"
    return f"Crew {event.get('crew')} out"


def render_briefings(view: str, data: dict, metrics: dict) -> None:
    """Both supervisor briefings in time order: 8 a.m. plan, then the noon replan (or its waiting state)."""
    st.markdown("**Supervisor briefings**")
    st.caption("What the Roads supervisor hears at 8 a.m. and at noon, written from the plan's numbers.")
    if view == "FIFO":
        st.caption(f":material/info: The map shows the oldest-first baseline: {metrics['fifo']['safety']} safety "
                   f"tickets versus {metrics['8am']['safety']} in the agent's plan. Both briefings below are "
                   f"for the agent's plan.")

    text, src = cached_briefing("8am", data["plan_8am"], metrics, "8am")
    m, fifo = metrics["8am"], metrics["fifo"]
    chips = [(f"{m['safety']} safety tickets", "good"),
             (f"{m['safety'] - fifo['safety']:+d} vs oldest-first", "good" if m["safety"] >= fifo["safety"] else "bad"),
             (f"{m['n']} jobs", "neutral")]
    st.markdown(_brief_card_html("8am", text, chips, active=view == "Agent"), unsafe_allow_html=True)
    st.caption(SOURCE_NOTES[src])

    noon = st.session_state.noon
    log = st.session_state.get("log") or []
    if noon and "plan" in noon:
        ev, nm = noon["event"], noon["metrics"]["noon"]
        digest = hashlib.md5(json.dumps(log, sort_keys=True, default=str).encode()).hexdigest()
        text, src = cached_briefing(f"noon:{digest}", noon["plan"], noon["metrics"], "noon", noon["changes"], event=ev)
        lost = nm.get("safety_dropped", 0)
        chips = [(_event_label(ev) if len(log) == 1 else f"{len(log)} updates today", "bad"),
                 (f"{nm.get('moved', 0)} moved", "neutral"), (f"{nm.get('dropped', 0)} deferred", "neutral")]
        if nm.get("added"):
            chips.append((f"{nm['added']} new job{'s' if nm['added'] != 1 else ''}", "neutral"))
        chips.append((f"{lost} safety dropped", "good" if lost == 0 else "bad"))
        st.markdown(_brief_card_html("noon", text, chips, active=view == "Agent - noon"), unsafe_allow_html=True)
        st.caption(SOURCE_NOTES[src])
    else:
        st.markdown(_brief_card_html("noon", None, [], active=False), unsafe_allow_html=True)



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
                st.markdown("\n".join([where, f"- **Severity:** {day_live.SEVERITY_LABELS[sev]} ({sev} of 3)",
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
        st.markdown("**Crews**")
        st.space("stretch")
        if st.session_state.selected_crew:
            st.button("Show all crews", icon=":material/zoom_out_map:", type="tertiary", on_click=show_all,
                      key="show_all")
    with st.container(border=True, gap=None):
        for c in plan["crews"]:
            is_sel = c["crew"] == st.session_state.selected_crew
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(dot(c["crew"]), unsafe_allow_html=True, width="content")
                label = f"**Crew {c['crew']}** · {c['zone']}" + (" · out today" if c["crew"] in out_crews else "")
                st.button(label, key=f"crew_{c['crew']}", type="secondary" if is_sel else "tertiary",
                          on_click=select_crew, args=(c["crew"],))


# --- page ------------------------------------------------------------------

st.set_page_config(page_title="311 Dispatch Agent", layout="wide")
data = load_outputs()
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
    ss.parsed = ss.parse_error = ss.pending = ss.locating = None
    ss.pop("view", None)
    if (entry["event"] == "crew_partial" and float(entry.get("capacity", 0.0)) >= 1.0
            and day_live.slots_now(data["plan_8am"], ss.log).get(entry["crew"], 5) >= 5):
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
st.caption("Calgary Roads · 8 crews × 5 jobs · 200 Open Calgary 311 tickets (Aug 25–27, 2026), planned for Aug 28")
if data["fake"]:
    st.warning("Showing sample data: run `python -m dispatch.run` to generate dispatch/outputs/.")

render_results(metrics)
render_how_it_works(metrics)
st.divider()

# --- today's plan: map, crews, updates ---

st.subheader("Today's plan")
views = ["Agent", "FIFO"] + (["Agent - noon"] if ss.noon and "plan" in ss.noon else [])
if ss.get("view") not in views:
    ss.view = views[-1] if ss.noon and "plan" in ss.noon else "Agent"
view = st.radio("Plan shown", views, horizontal=True, key="view", label_visibility="collapsed",
                format_func={"Agent": "Our 8 a.m. plan", "FIFO": "Oldest-first baseline",
                             "Agent - noon": "After today's updates"}.get)

out_crews, slots = set(), None
if view == "FIFO":
    plan, m, changes = data["plan_fifo"], metrics["fifo"], None
elif view == "Agent":
    plan, m, changes = data["plan_8am"], metrics["8am"], None
else:
    plan, m, changes = ss.noon["plan"], ss.noon["metrics"]["noon"], ss.noon["changes"]
    slots = ss.noon["slots"]
    out_crews = {c for c, n in slots.items() if n == 0}

if view == "Agent - noon":
    render_live_strip(m, metrics["fifo"], len(ss.log))

sel_job, sel_job_crew = find_job(plan, changes, ss.selected_job)
sel_crew = find_crew(plan, ss.selected_crew)

left, right = st.columns([3, 1.3], gap="large")
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
        ev_tag = ss.noon["event"] if ss.noon and "event" in ss.noon else {}
        hit = render_map(plan, changes, sel_job, sel_crew,
                         tag=f"{view}_{len(ss.log)}_{ev_tag.get('event')}_{ev_tag.get('crew')}_{ev_tag.get('capacity')}")
        if hit:  # a click on the map selects that job (and its crew) or that crew
            if hit[0] == "job":
                select_job(hit[1], hit[2])
            else:
                select_crew(hit[1])
            st.rerun()

    # under the map: the selected crew and job, or the briefing when nothing is selected
    if sel_crew and ss.selected_job:
        c_list, c_job = st.columns([1, 1])
        with c_list:
            render_crew_panel(sel_crew, changes, out_crews)
        with c_job:
            render_job_detail(sel_job, sel_job_crew, view)
    elif sel_crew:
        render_crew_panel(sel_crew, changes, out_crews)
    elif ss.selected_job:
        render_job_detail(sel_job, sel_job_crew, view)
    else:
        render_briefings(view, data, metrics)

with right:
    render_crew_list(plan, out_crews)
    st.write("")

    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("**Report an update**")
        st.space("stretch")
        if claude_ready():
            st.badge("Claude", icon=":material/auto_awesome:", color="green",
                     help="An API key is set: Claude reads updates, with the rule-based parser as backup.")
        else:
            st.badge("Rule-based", icon=":material/rule:", color="gray",
                     help="No ANTHROPIC_API_KEY on this machine: the rule-based parser reads updates.")
    crew_tab, caller_tab = st.tabs([":material/engineering: Crew update", ":material/call: Caller report"],
                                   key="report_tab", on_change="rerun")  # keyed: stays put across reruns
    with caller_tab:
        render_caller_intake(ss.noon["plan"] if ss.noon and "plan" in ss.noon else data["plan_8am"],
                             on_add=_add_call if day_live else None)
    with crew_tab:
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
                with st.expander("Parsed result (JSON)"):
                    st.json({k: v for k, v in ev.items() if k not in ("source", "why")})

        if ss.get("parse_error") and not ss.parsed:
            with st.container(border=True):
                st.error("Couldn't read that update automatically. Pick the crew and what happened:")
                st.caption(ss.parse_error)
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
                st.markdown("\n".join(f"{i}. {day_live.describe_event(e)}" for i, e in enumerate(ss.log, 1)))
                st.button("Undo last update", icon=":material/undo:", on_click=_undo, width="stretch")
                st.button("Reset to 8 a.m. plan", icon=":material/restart_alt:", on_click=_reset, width="stretch")
