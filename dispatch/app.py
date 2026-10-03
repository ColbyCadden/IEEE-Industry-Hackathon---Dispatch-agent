"""Streamlit demo: `streamlit run dispatch/app.py` (run `python -m dispatch.run` first)."""
import json
import sys
from pathlib import Path

import pydeck as pdk
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # `streamlit run` only puts dispatch/ on the path

try:  # llm.py is owned by a teammate; the dashboard must still load without it
    from dispatch import llm  # noqa: E402
except Exception:
    llm = None

OUT = ROOT / "dispatch" / "outputs"
CREW_COLORS = [  # one per crew, readable on a light basemap
    [31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40],
    [148, 103, 189], [140, 86, 75], [227, 119, 194], [23, 190, 207],
]
QUICK_FILLS = ["Crew 4 called in sick", "Crew 2 is down a guy"]


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
    if not all((OUT / f).exists() for f in files.values()):
        return _fake_outputs()
    data = {k: json.loads((OUT / f).read_text(encoding="utf-8")) for k, f in files.items()}
    data["fake"] = False
    return data


def replan(plan_8am: dict, metrics: dict, event: dict) -> dict:
    """Run the engine's replan + metrics. Returns {"plan","changes","metrics"} or {"error"}."""
    try:
        from dispatch.metrics import compute
        from dispatch.replan import apply_event
        new_plan, changes = apply_event(plan_8am, event)
        bundle = {k: v for k, v in metrics.items() if k in ("8am", "fifo")}
        bundle["noon"] = compute(new_plan, changes)
        bundle["changes"] = changes
        return {"plan": new_plan, "changes": changes, "metrics": bundle}
    except Exception as e:  # engine missing or broken: keep the app usable
        return {"error": f"{type(e).__name__}: {e}"}


# --- llm wrappers (contract signatures only; fall back if llm.py fails) ------

EVENTS = ("crew_out", "crew_partial", "unclear")


def safe_parse(text: str) -> tuple[dict | None, str | None]:
    """llm.parse_event(text) checked against the contract. Returns (event, error)."""
    try:
        ev = llm.parse_event(text)
        if not isinstance(ev, dict) or ev.get("event") not in EVENTS:
            raise ValueError(f"unexpected result {ev!r}")
        if ev["event"] != "unclear" and ev.get("crew") not in range(1, 9):
            raise ValueError(f"crew must be 1-8, got {ev.get('crew')!r}")
        ev.setdefault("capacity", 0.0 if ev["event"] == "crew_out" else 0.5)
        ev.setdefault("question", None)
        return ev, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def safe_briefing(plan: dict, metrics: dict, when: str, changes: dict | None = None) -> tuple[str, bool]:
    """llm.briefing(...) or a short numbers-only fallback. Returns (text, came_from_llm_module)."""
    try:
        text = llm.briefing(plan, metrics, when, changes)
        if isinstance(text, str) and text.strip():
            return text, True
    except Exception:
        pass
    if when == "noon":
        m = metrics["noon"]
        return (f"Noon plan: {m['n']} jobs, {m['safety']} safety tickets. {m.get('moved', 0)} jobs moved, "
                f"{m.get('dropped', 0)} deferred, {m.get('safety_dropped', 0)} safety tickets deferred."), False
    m, f = metrics["8am"], metrics["fifo"]
    return (f"8 a.m. plan: {m['n']} jobs, {m['safety']} safety tickets "
            f"(oldest-first would cover {f['safety']})."), False


# --- view helpers ------------------------------------------------------------

def describe(event: dict) -> str:
    kind, crew = event.get("event"), event.get("crew")
    if kind == "crew_out":
        return f"Crew {crew} is **out for the day**. All of its jobs will be reassigned or deferred."
    if kind == "crew_partial":
        cap = event.get("capacity", 0.5)
        return (f"Crew {crew} is **short-handed** ({cap:.0%} capacity). "
                f"It keeps its top {round(5 * cap)} jobs; the rest will be reassigned or deferred.")
    return "I couldn't tell what changed."


def map_layers(plan: dict, changes: dict | None) -> list:
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    points = []
    for c in plan["crews"]:
        color = CREW_COLORS[(c["crew"] - 1) % len(CREW_COLORS)]
        for j in c["jobs"]:
            points.append({**j, "crew": c["crew"], "zone": c["zone"], "color": color,
                           "radius": 7 if j["safety"] else 4,
                           "line": [0, 0, 0] if j["id"] in moved else [255, 255, 255],
                           "status": "moved here" if j["id"] in moved else ("safety" if j["safety"] else "")})
    dropped = [{**j, "crew": "-", "zone": "-", "color": [150, 150, 150], "radius": 4, "line": [90, 90, 90],
                "status": "deferred"} for j in (changes or {}).get("dropped_jobs", [])]
    centroids = [{"crew": c["crew"], "zone": c["zone"], "lat": c["centroid"][0], "lon": c["centroid"][1],
                  "label": f"Crew {c['crew']}", "color": CREW_COLORS[(c["crew"] - 1) % len(CREW_COLORS)]}
                 for c in plan["crews"]]
    common = dict(get_position="[lon, lat]", pickable=True)
    return [
        pdk.Layer("ScatterplotLayer", points + dropped, get_fill_color="color", get_radius="radius", radius_units="'pixels'",
                  get_line_color="line", stroked=True, line_width_min_pixels=2, opacity=0.85, **common),
        pdk.Layer("TextLayer", centroids, get_text="label", get_color="color", get_size=14,
                  get_alignment_baseline="'bottom'", font_weight=700, **common),
    ]


def render_map(plan: dict, changes: dict | None = None) -> None:
    deck = pdk.Deck(
        layers=map_layers(plan, changes),
        initial_view_state=pdk.ViewState(latitude=51.04, longitude=-114.08, zoom=9.6),
        map_provider="carto", map_style="light",
        tooltip={"text": "{type} - {community}\nP {P} | crew {crew} ({zone}) {status}\nticket {id}"},
    )
    st.pydeck_chart(deck)
    st.caption("Colour = crew. Large dots = safety tickets. Black outline = moved at noon. Grey = deferred.")


def render_metrics(view: str, m: dict, fifo: dict, base: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    if view == "FIFO":
        c1.metric("Priority served", f"{m['P']:.1f}")
        c2.metric("Safety served", m["safety"])
        c3.metric("Jobs moved", "-")
        c4.metric("Jobs deferred", "-")
        return
    ref, ref_name = (fifo, "vs FIFO") if view == "Agent" else (base, "vs 8 a.m.")
    c1.metric("Priority served", f"{m['P']:.1f}", f"{m['P'] - ref['P']:+.1f} {ref_name}")
    c2.metric("Safety served", m["safety"], f"{m['safety'] - ref['safety']:+d} {ref_name}")
    c3.metric("Jobs moved", m.get("moved", 0))
    c4.metric("Jobs deferred", m.get("dropped", 0),
              help=f"Safety tickets deferred: {m.get('safety_dropped', 0)}", delta_color="inverse")


def render_crews(plan: dict, changes: dict | None, out_crew: int | None) -> None:
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    cols = st.columns(4)
    for i, c in enumerate(plan["crews"]):
        with cols[i % 4].container(border=True):
            r, g, b = CREW_COLORS[(c["crew"] - 1) % len(CREW_COLORS)]
            st.markdown(f"<span style='color:rgb({r},{g},{b});font-size:1.3em'>●</span> "
                        f"**Crew {c['crew']}** · {c['zone']}", unsafe_allow_html=True)
            if not c["jobs"]:
                st.caption("Out today" if c["crew"] == out_crew else "No jobs")
                continue
            safety = sum(j["safety"] for j in c["jobs"])
            st.caption(f"{len(c['jobs'])} jobs · {safety} safety · P {sum(j['P'] for j in c['jobs']):.1f}")
            for j in sorted(c["jobs"], key=lambda j: -j["P"]):
                tags = (" ⚠️" if j["safety"] else "") + (" ↪ moved" if j["id"] in moved else "")
                st.markdown(f"<small>{j['type']} · {j['community'].title()} · P {j['P']:.2f}{tags}</small>",
                            unsafe_allow_html=True)


# --- page ------------------------------------------------------------------

st.set_page_config(page_title="311 Dispatch Agent", layout="wide")
data = load_outputs()
metrics = data["metrics"]
ss = st.session_state
ss.setdefault("update_text", "")
ss.setdefault("parsed", None)
ss.setdefault("noon", None)

st.title("Who should 311 send next?")
st.caption("Calgary Roads · 8 crews × 5 jobs · priority agent vs oldest-first (FIFO)")
if data["fake"]:
    st.warning("Showing sample data: run `python -m dispatch.run` to generate dispatch/outputs/.")

views = ["Agent", "FIFO"] + (["Agent - noon"] if ss.noon and "plan" in ss.noon else [])
if ss.get("view") not in views:
    ss.view = views[-1] if ss.noon and "plan" in ss.noon else "Agent"
view = st.radio("Plan", views, horizontal=True, key="view", label_visibility="collapsed")

if view == "FIFO":
    plan, m, changes, out_crew = data["plan_fifo"], metrics["fifo"], None, None
elif view == "Agent":
    plan, m, changes, out_crew = data["plan_8am"], metrics["8am"], None, None
else:
    plan, m, changes = ss.noon["plan"], ss.noon["metrics"]["noon"], ss.noon["changes"]
    out_crew = ss.noon["event"].get("crew")

render_metrics(view, m, metrics["fifo"], metrics["8am"])

left, right = st.columns([3, 2])
with left:
    render_map(plan, changes)
with right:
    st.subheader("Briefing")
    with st.container(border=True):
        if view == "FIFO":
            st.write(f"Baseline: oldest tickets first, same crews and zones. It covers {m['safety']} safety "
                     f"tickets, versus {metrics['8am']['safety']} in the agent's plan.")
        else:
            if view == "Agent":
                text, ok = safe_briefing(data["plan_8am"], metrics, "8am")
            else:
                text, ok = safe_briefing(plan, ss.noon["metrics"], "noon", changes)
            st.write(text)
            if not ok:
                st.caption("Briefing service unavailable - showing the numbers only.")

    st.subheader("Report a crew update")

    def _fill(text: str) -> None:
        ss.update_text, ss.parsed = text, None

    b1, b2 = st.columns(2)
    b1.button(QUICK_FILLS[0], on_click=_fill, args=(QUICK_FILLS[0],), use_container_width=True)
    b2.button(QUICK_FILLS[1], on_click=_fill, args=(QUICK_FILLS[1],), use_container_width=True)
    st.text_area("What happened?", key="update_text", height=80,
                 placeholder="e.g. Crew 4 called in sick, they're out for the day")
    if st.button("Submit", type="primary", disabled=not ss.update_text.strip()):
        ss.parsed, ss.parse_error = safe_parse(ss.update_text)

    if ss.get("parse_error") and not ss.parsed:
        with st.container(border=True):
            st.error("Couldn't read that update automatically. Pick the crew and what happened:")
            st.caption(ss.parse_error)
            m_crew, m_kind = st.columns(2)
            crew = m_crew.selectbox("Crew", range(1, 9), index=3)
            kind = m_kind.radio("Status", ["Out for the day", "Short-handed (50%)"])
            if st.button("Use this update"):
                ss.parsed = {"event": "crew_out" if kind.startswith("Out") else "crew_partial", "crew": crew,
                             "capacity": 0.0 if kind.startswith("Out") else 0.5, "question": None}
                ss.parse_error = None
                st.rerun()

    if ss.parsed:
        event = ss.parsed
        with st.container(border=True):
            if event["event"] == "unclear":
                st.warning(event.get("question") or "Which crew is affected?")
                st.caption("Add the missing detail above and submit again.")
            else:
                st.markdown(f"**Confirm update:** {describe(event)}")
                st.json(event, expanded=False)
                c_ok, c_cancel = st.columns(2)
                if c_ok.button("Confirm and replan", type="primary", use_container_width=True):
                    result = replan(data["plan_8am"], metrics, event)
                    ss.noon = {**result, "event": event}
                    ss.parsed = None
                    ss.pop("view", None)
                    st.rerun()
                if c_cancel.button("Cancel", use_container_width=True):
                    ss.parsed = None
                    st.rerun()

    if ss.noon and "error" in ss.noon:
        st.error(f"Replan engine isn't available: {ss.noon['error']}")
    elif ss.noon:
        if st.button("Reset to 8 a.m. plan"):
            ss.noon = None
            ss.pop("view", None)
            st.rerun()

st.subheader("Crews")
render_crews(plan, changes, out_crew)
