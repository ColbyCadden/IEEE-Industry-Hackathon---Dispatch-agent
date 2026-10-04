"""Roads dispatch console: `streamlit run dispatch/app.py` (run `python -m dispatch.run` first)."""
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

try:  # the baseline / result / improved comparison is shared with `python -m dispatch.improvement`
    from dispatch import improvement  # noqa: E402
except Exception:
    improvement = None

OUT = ROOT / "dispatch" / "outputs"
CREW_COLORS = [  # one per crew, readable on a light basemap
    [31, 119, 180], [255, 127, 14], [44, 160, 44], [214, 39, 40],
    [148, 103, 189], [140, 86, 75], [227, 119, 194], [23, 190, 207],
]
QUICK_FILLS = ["Crew 4 called in sick", "Crew 2 is down a guy"]
NUMBER_WORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight"}
EVENT_BADGES = {  # event -> (label, badge colour, icon) for the interpretation panel
    "crew_out": ("Crew out for the day", "red", ":material/person_off:"),
    "crew_partial": ("Crew short-handed", "orange", ":material/group_remove:"),
}


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
def load_ticket_details() -> dict:
    """id -> {days_open, weight, requested_date} from the engine's own scoring (read-only)."""
    try:
        from dispatch.data_prep import load_clean_tickets
        from dispatch.scoring import score
        df = score(load_clean_tickets(str(ROOT / "data" / "311_dispatch_sample.csv")))
        return {r.id: {"days_open": int(r.days_open), "weight": float(r.weight),
                       "requested_date": r.requested_date.strftime("%b %d, %Y")}
                for r in df.itertuples(index=False)}
    except Exception:  # engine missing: the panel shows what the plan itself carries
        return {}


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
        if ev["event"] != "unclear" and ev.get("crew") not in range(1, 9):
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
    m = metrics["8am"]
    return f"8 a.m. plan: {m['n']} jobs, {m['safety']} safety tickets.", "numbers"


def cached_briefing(key: str, *args, **kwargs) -> tuple[str, str]:
    """One briefing per plan state per session: clicks and reruns don't call Claude again."""
    store = st.session_state.setdefault("briefings", {})
    if key not in store:
        with st.spinner("Writing the briefing..."):
            store[key] = safe_briefing(*args, **kwargs)
    return store[key]


SOURCE_NOTES = {
    "claude": ":material/auto_awesome: Written by Claude from the plan's numbers.",
    "rules": ":material/rule: Claude unavailable (no API key or no connection) - rule-based template briefing.",
    "numbers": ":material/warning: Briefing service unavailable - showing the numbers only.",
}


# --- view helpers ------------------------------------------------------------

def describe(event: dict) -> str:
    kind, crew = event.get("event"), event.get("crew")
    if kind == "crew_out":
        return f"Crew {crew} is **out for the day**. All of its jobs will be reassigned or deferred."
    if kind == "crew_partial" and event.get("capacity", 0.5) >= 1.0:
        return f"Crew {crew} is **at full strength**. Nothing to replan: showing the 8 a.m. plan."
    if kind == "crew_partial":
        cap = event.get("capacity", 0.5)
        return (f"Crew {crew} is **short-handed** ({cap:.0%} capacity). "
                f"It keeps its top {round(5 * cap)} jobs; the rest will be reassigned or deferred.")
    return "I couldn't tell what changed."


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


def select_job(job_id: str | None) -> None:
    st.session_state.selected_job = job_id


def toggle_crew(crew: int) -> None:
    ss_ = st.session_state
    ss_.selected_crew = None if ss_.selected_crew == crew else crew


def find_crew(plan: dict, crew_id: int | None) -> dict | None:
    return next((c for c in plan["crews"] if c["crew"] == crew_id), None) if crew_id else None


def _job_label(j: dict, moved: set) -> str:
    return (" ⚠️" if j["safety"] else "") + (" ↪ moved" if j["id"] in moved else "")


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
        if job["safety"]:
            st.badge("Safety ticket", icon=":material/warning:", color="red")
        info = load_ticket_details().get(job_id)
        where = (f"Crew {crew['crew']} ({crew['zone']})" if crew else "Deferred at noon")
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
        if info:
            extra = job["reports"] - 1
            st.markdown(
                f"**Priority score:** {info['weight']:g} type weight + 0.25 × {info['days_open']} "
                f"day{'s' if info['days_open'] != 1 else ''} open + 0.5 × {extra} extra "
                f"report{'s' if extra != 1 else ''} = **P {job['P']:.2f}**")
        else:
            st.markdown(f"**Priority score:** P {job['P']:.2f}")


def render_crew_panel(crew: dict, changes: dict | None, out_crew: int | None) -> None:
    """The selected crew's jobs in priority order; each row selects that job."""
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    with st.container(border=True):
        head, close = st.columns([5, 2], vertical_alignment="center")
        r, g, b = CREW_COLORS[(crew["crew"] - 1) % len(CREW_COLORS)]
        head.markdown(f"<span style='color:rgb({r},{g},{b});font-size:1.3em'>●</span> "
                      f"**Crew {crew['crew']}**", unsafe_allow_html=True)
        close.button("Clear crew", icon=":material/close:", type="tertiary", on_click=toggle_crew,
                     args=(crew["crew"],), key="clear_crew")
        jobs = sorted(crew["jobs"], key=lambda j: -j["P"])
        if not jobs:
            st.caption(f"Zone {crew['zone']} · " + ("out today, no jobs" if crew["crew"] == out_crew else "no jobs"))
            return
        st.caption(f"Zone {crew['zone']} · {len(jobs)} jobs · total P {sum(j['P'] for j in jobs):.2f} · "
                   f"{sum(j['safety'] for j in jobs)} safety")
        st.caption("Exact locations (lat, lon). The source data has no street addresses.")
        for rank, j in enumerate(jobs, 1):
            chosen = j["id"] == st.session_state.selected_job
            st.button(f"{rank}. {j['type']} · {j['community'].title()} · P {j['P']:.2f}{_job_label(j, moved)}",
                      key=f"crewjob_{j['id']}", type="secondary" if chosen else "tertiary",
                      on_click=select_job, args=(None if chosen else j["id"],),
                      help="Selected: click again to clear" if chosen else "Show details and find on map")
            st.caption(f":material/location_on: {j['lat']:.5f}, {j['lon']:.5f} · ticket {j['id']}")


def map_layers(plan: dict, changes: dict | None, selected: dict | None = None,
               crew_id: int | None = None) -> list:
    """Job markers coloured by crew; only that crew's markers when one is selected; ring on the selected job."""
    sel_id = selected["id"] if selected else None

    def shown(crew: int | None, job_id: str) -> bool:  # crew isolation; the selected job always stays visible
        return crew_id is None or crew == crew_id or job_id == sel_id

    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    points = []
    for c in plan["crews"]:
        color = CREW_COLORS[(c["crew"] - 1) % len(CREW_COLORS)]
        for j in c["jobs"]:
            if not shown(c["crew"], j["id"]):
                continue
            is_sel = j["id"] == sel_id
            points.append({**j, "crew": c["crew"], "zone": c["zone"], "color": color,
                           "radius": (11 if is_sel else 7) if j["safety"] else (9 if is_sel else 4),
                           "line": [0, 0, 0] if j["id"] in moved else [255, 255, 255],
                           "status": "moved here" if j["id"] in moved else ("safety" if j["safety"] else "")})
    dropped = [{**j, "crew": "-", "zone": "-", "color": [150, 150, 150],
                "radius": 9 if j["id"] == sel_id else 4, "line": [90, 90, 90], "status": "deferred"}
               for j in (changes or {}).get("dropped_jobs", []) if shown(None, j["id"])]
    centroids = [{"crew": c["crew"], "zone": c["zone"], "lat": c["centroid"][0], "lon": c["centroid"][1],
                  "label": f"Crew {c['crew']}", "color": CREW_COLORS[(c["crew"] - 1) % len(CREW_COLORS)]}
                 for c in plan["crews"] if crew_id is None or c["crew"] == crew_id]
    common = dict(get_position="[lon, lat]", pickable=True)
    layers = [
        pdk.Layer("ScatterplotLayer", points + dropped, get_fill_color="color", get_radius="radius", radius_units="'pixels'",
                  get_line_color="line", stroked=True, line_width_min_pixels=2, opacity=0.85, **common),
        pdk.Layer("TextLayer", centroids, get_text="label", get_color="color", get_size=14,
                  get_alignment_baseline="'bottom'", font_weight=700, **common),
    ]
    if selected:  # ring around the selected job, drawn on top; all other markers stay
        layers.append(pdk.Layer("ScatterplotLayer", [selected], get_position="[lon, lat]", get_radius=18,
                                radius_units="'pixels'", filled=False, stroked=True,
                                get_line_color=[20, 20, 20], line_width_units="'pixels'", get_line_width=3))
    return layers


def crew_view_state(crew: dict) -> pdk.ViewState:
    """Fit the map to one crew's jobs (zoom capped so a tight cluster isn't street level)."""
    view = pdk.data_utils.compute_view([[j["lon"], j["lat"]] for j in crew["jobs"]], view_proportion=1)
    return pdk.ViewState(latitude=view.latitude, longitude=view.longitude, zoom=min(view.zoom, 13.5))


def render_map(plan: dict, changes: dict | None = None, selected: dict | None = None,
               crew: dict | None = None, height: int = 500, key_prefix: str = "map") -> None:
    if selected:
        view_state = pdk.ViewState(latitude=selected["lat"], longitude=selected["lon"], zoom=14.5)
    elif crew and crew["jobs"]:
        view_state = crew_view_state(crew)
    else:
        view_state = pdk.ViewState(latitude=51.04, longitude=-114.08, zoom=9.6)
    deck = pdk.Deck(
        layers=map_layers(plan, changes, selected, crew["crew"] if crew else None),
        initial_view_state=view_state,
        map_provider="carto", map_style="light",
        tooltip={"text": "{type} - {community}\nP {P} | crew {crew} ({zone}) {status}\nticket {id}"},
    )
    # a new key per selection remounts the map so it actually moves to the new view state
    key = f"{key_prefix}_{selected['id'] if selected else ''}_{crew['crew'] if crew else ''}"
    st.pydeck_chart(deck, key=key, height=height, alt="Map of Calgary showing jobs, coloured by crew")
    st.caption("Colour = crew. Large dots = safety tickets. Black outline = moved at noon. Grey = deferred.")


VIEW_LABELS = {"Agent": "8 a.m. plan", "Agent - noon": "After the crew update"}
SENSITIVITY_NOTE = ("We re-ran the plan with every type weight changed by ±1 (16 variations). The agent covered "
                    "more hazard tickets than oldest-first in all 16, by +9 to +15.")


def render_facts(view: str, plan: dict, m: dict) -> None:
    """The operational facts, compact: crews working, jobs today, hazard tickets, and what changed."""
    working = sum(1 for c in plan["crews"] if c["jobs"])
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.badge(f"{working} of {len(plan['crews'])} crews working", icon=":material/groups:", color="gray")
        st.badge(f"{m['n']} jobs assigned today", icon=":material/assignment:", color="gray")
        st.badge(f"{m['safety']} hazard tickets covered", icon=":material/warning:", color="gray",
                 help="Hazard tickets: potholes and missing or damaged signs.")
        if view == "Agent - noon":
            ev = ss.noon["event"]
            who = f"Crew {ev['crew']} " + ("out" if ev["event"] == "crew_out"
                                          else f"at {float(ev.get('capacity', 0.0)):.0%}")
            st.badge(f"{who} · {m.get('moved', 0)} moved · {m.get('dropped', 0)} deferred · "
                     f"{m.get('safety_dropped', 0)} hazards deferred", icon=":material/sync:", color="orange")


def render_assistant_turns(view: str, plan: dict, changes: dict | None) -> None:
    """The conversation: the agent's briefing, the supervisor's last update, and what the agent did with it."""
    bot, me = ":material/smart_toy:", ":material/person:"
    with st.chat_message("assistant", avatar=bot):
        # operational briefing only: the oldest-first figures stay on the Analysis tab
        if view == "Agent":
            text, src = cached_briefing("ops:8am", data["plan_8am"], {"8am": metrics["8am"]}, "8am")
        else:
            ev = ss.noon["event"]
            ops = {k: v for k, v in ss.noon["metrics"].items() if k != "fifo"}
            text, src = cached_briefing(f"ops:noon:{ev['event']}:{ev['crew']}:{ev.get('capacity')}",
                                        plan, ops, "noon", changes, event=ev)
        st.markdown(text)
        st.caption(SOURCE_NOTES[src])

    if ss.get("applied"):
        ev, got = ss.applied["event"], ss.applied["received"]
        if got:
            with st.chat_message("user", avatar=me):
                st.markdown(got)
        how = {"claude": ":material/auto_awesome: Read by Claude", "manual": ":material/edit: Entered by hand"}.get(
            ev.get("source"), ":material/rule: Read by the rule-based parser"
            + (" (Claude unavailable: no API key or no connection)" if str(ev.get("why", "")).startswith("fallback")
               else " (no Claude call needed)"))
        with st.chat_message("assistant", avatar=bot):
            back = ev["event"] == "crew_partial" and float(ev.get("capacity", 0.0)) >= 1.0
            st.markdown(":material/check_circle: **Understood. Plan updated.**" if not back
                        else ":material/check_circle: **Understood. No replan needed.**")
            label, color, icon = EVENT_BADGES.get(ev["event"], ("Update", "gray", ":material/info:"))
            if back:
                label, color, icon = "Crew at full strength", "green", ":material/group:"
            f_ev, f_crew, f_cap = st.columns([1.8, 1, 1])
            with f_ev:
                st.caption("Event")
                st.badge(label, icon=icon, color=color)
            f_crew.caption("Crew")
            f_crew.markdown(f"**{ev.get('crew')}**")
            f_cap.caption("Capacity")
            f_cap.markdown(f"**{float(ev.get('capacity', 0.0)):.0%}**")
            st.caption(describe(ev))
            st.caption(how)
            with st.expander("Parsed result (JSON)", expanded=False):
                st.json({k: v for k, v in ev.items() if k not in ("source", "why")})

    if ss.get("parse_error") and not ss.parsed:
        with st.chat_message("assistant", avatar=bot):
            st.markdown("I couldn't read that update automatically. Which crew, and what happened?")
            st.caption(ss.parse_error)
            m_crew, m_kind = st.columns(2)
            crew = m_crew.selectbox("Crew", range(1, 9), index=3)
            kind = m_kind.radio("Status", ["Out for the day", "Short-handed (50%)"])
            st.button("Use this update", on_click=_manual,
                      args=(crew, "crew_out" if kind.startswith("Out") else "crew_partial"))

    if ss.parsed:  # only reached for an ambiguous message
        event = ss.parsed
        if ss.get("received"):
            with st.chat_message("user", avatar=me):
                st.markdown(ss.received)
        with st.chat_message("assistant", avatar=bot):
            st.markdown(f"**{event.get('question') or 'Which crew is affected?'}**")
            st.caption("Read by Claude" if event.get("source") == "claude" else "Read by the rule-based parser")
            picks = event.get("candidates") or []
            if picks:  # several crews named: one click picks the one to replan
                for col, n in zip(st.columns(len(picks)), picks):
                    col.button(f"Crew {n}", key=f"pick_{n}", on_click=_run, args=(ss.received, n), width="stretch")
            elif event.get("crew"):  # crew known, status unknown
                b_out, b_part = st.columns(2)
                b_out.button("Out for the day", key="manual_out", icon=":material/person_off:", width="stretch",
                             on_click=_manual, args=(event["crew"], "crew_out"))
                b_part.button("Short-handed (50%)", key="manual_part", icon=":material/group_remove:", width="stretch",
                              on_click=_manual, args=(event["crew"], "crew_partial"))
            else:
                st.text_input("Your answer", key="followup", placeholder="e.g. crew 4", on_change=_answer)
                st.button("Send answer", icon=":material/reply:", on_click=_answer)


def render_crew_list(plan: dict, changes: dict | None, out_crew: int | None) -> None:
    """Dense crew list beside the map: crew row (click to isolate) and its jobs (click for detail)."""
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    for c in plan["crews"]:
        r, g, b = CREW_COLORS[(c["crew"] - 1) % len(CREW_COLORS)]
        is_sel = c["crew"] == st.session_state.selected_crew
        jobs = sorted(c["jobs"], key=lambda j: -j["P"])
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown(f"<span style='color:rgb({r},{g},{b});font-size:1.2em'>●</span>",
                        unsafe_allow_html=True, width="content")
            st.button(f"**Crew {c['crew']}** · {c['zone']}", key=f"crew_{c['crew']}",
                      type="secondary" if is_sel else "tertiary", on_click=toggle_crew, args=(c["crew"],),
                      icon=":material/filter_center_focus:" if is_sel else None,
                      help="Selected: click again to show the whole city" if is_sel else "Isolate this crew on the map")
            if jobs:
                st.caption(f"{len(jobs)} jobs · {sum(j['safety'] for j in jobs)} hazard")
            else:
                st.caption("Out today" if c["crew"] == out_crew else "No jobs")
        for j in jobs:
            chosen = j["id"] == st.session_state.selected_job
            st.button(f"{j['type']} · {j['community'].title()} · P {j['P']:.2f}{_job_label(j, moved)}",
                      key=f"job_{j['id']}", type="secondary" if chosen else "tertiary",
                      icon=":material/my_location:" if chosen else None,
                      on_click=select_job, args=(None if chosen else j["id"],),
                      help="Selected: click again to clear" if chosen else "Show details and find on map")
        st.divider()


def comparison_table(bundle: dict, event: dict | None):
    """Rows for the evidence table: baseline, agent plan, replanned plan (same code as dispatch.improvement)."""
    import pandas as pd
    if improvement is not None:
        rows = improvement.comparison_rows(bundle, event)
    else:  # improvement.py missing: same three rows straight from the metrics
        crew = (event or {}).get("crew", "?")
        rows = [{"key": k, "stage": s, "what": w, "m": None if bundle.get(k) is None else
                 {"jobs": bundle[k]["n"], "safety": bundle[k]["safety"], "P": bundle[k]["P"],
                  **({"moved": bundle[k].get("moved", 0), "deferred": bundle[k].get("dropped", 0),
                      "safety_dropped": bundle[k].get("safety_dropped", 0)} if k == "noon" else {})}}
                for k, s, w in (("fifo", "1 BASELINE", "oldest-first (FIFO)"),
                                ("8am", "2 FIRST RESULT", "priority-scored agent"),
                                ("noon", "3 IMPROVED", f"replanned, crew {crew} out"))]
    names = {"fifo": "1 · Oldest-first baseline", "8am": "2 · Agent plan, 8 a.m.", "noon": "3 · Agent replanned"}
    table = []
    for r in rows:
        m = r["m"] or {}
        table.append({  # all text, so "—" and numbers share a column cleanly
            "Plan": names[r["key"]] + (f" ({r['what'].split(', ', 1)[-1]})" if r["key"] == "noon" else ""),
            "Jobs": str(m.get("jobs", "n/a")),
            "Hazard tickets covered": str(m.get("safety", "n/a")),
            "Total priority (P)": f"{m['P']:.2f}" if "P" in m else "n/a",
            "Moved": str(m.get("moved", "—")),
            "Deferred": str(m.get("deferred", "—")),
            "Hazards deferred": str(m.get("safety_dropped", "—")),
        })
    summary = improvement.summary_line(rows, event) if improvement is not None else ""
    return pd.DataFrame(table), summary


def render_analysis(metrics: dict) -> None:
    """Evidence the plan is better: improvement round, disruption counts, scoring, robustness, FIFO view."""
    if ss.noon and "plan" in ss.noon:  # the replan made in this session
        bundle, event, note = ss.noon["metrics"], ss.noon["event"], "Row 3 is the crew update you applied."
    else:  # the reference disruption from `python -m dispatch.run`
        try:
            event = json.loads((OUT / "event.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            event = None
        bundle, note = metrics, "Row 3 is the reference disruption from `python -m dispatch.run`."

    st.markdown("##### Improvement round: baseline → agent plan → replanned")
    st.caption("Same 8 crews × 5 jobs, same zones, same assignment code. Only the order tickets are taken in differs.")
    try:
        df, summary = comparison_table(bundle, event)
        st.table(df, hide_index=True)
        st.caption(note + (f" {summary}" if summary else ""))
    except Exception:  # unexpected metrics shape: say so rather than break the page
        st.caption("Comparison unavailable: run `python -m dispatch.run`.")

    f_col, s_col = st.columns(2, gap="medium")
    with f_col:
        st.markdown("##### Priority score")
        st.markdown("P = hazard type weight (0–3) + 0.25 × days open + 0.5 × extra reports of the same problem. "
                    "Higher means more urgent. Weight 3: potholes, missing or damaged signs. 2: debris, traffic "
                    "markings. 1: service requests. 0: not a field-crew job.")
    with s_col:
        st.markdown("##### Robustness")
        st.markdown(SENSITIVITY_NOTE)
        st.caption("Reproduce: `python -m tests.sensitivity`")

    st.markdown("##### Oldest-first plan, for comparison")
    fifo, fm = data["plan_fifo"], metrics["fifo"]
    st.caption(f"Tickets taken by age, type ignored: {fm['n']} jobs, {fm['safety']} hazard tickets, "
               f"total P {fm['P']:.2f}. Not a dispatch plan.")
    import pandas as pd
    map_col, table_col = st.columns([3, 2], gap="medium")
    with map_col:
        render_map(fifo, height=420, key_prefix="fifo_map")
    with table_col:
        rows = [{"Crew": f"{c['crew']} · {c['zone']}", "Job": j["type"], "Community": j["community"].title(),
                 "P": round(j["P"], 2), "Hazard": "⚠️" if j["safety"] else ""}
                for c in fifo["crews"] for j in sorted(c["jobs"], key=lambda j: -j["P"])]
        st.dataframe(pd.DataFrame(rows), hide_index=True, height=420)
    st.caption("Data: frozen sample of 200 Open Calgary 311 tickets. No street addresses (coordinates only); "
               "jobs are assigned to crews, not routed.")


# --- page ------------------------------------------------------------------

st.set_page_config(page_title="Roads Dispatch · Calgary 311", page_icon=":material/engineering:", layout="wide")
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


def _apply(event: dict, received: str) -> None:
    """Replan straight away: the supervisor's only step is submitting the update."""
    clean = {k: v for k, v in event.items() if k not in ("source", "why", "candidates")}  # engine gets the contract dict
    if clean["event"] == "crew_partial" and float(clean.get("capacity", 0.0)) >= 1.0:
        ss.noon = None  # the crew is back: nothing to replan
    else:
        ss.noon = {**replan(data["plan_8am"], metrics, clean), "event": clean}
    ss.applied = {"event": event, "received": received}
    ss.parsed = ss.parse_error = None
    ss.pop("view", None)


def _run(text: str, only_crew: int | None = None) -> None:
    """Read the update and apply it. Only an ambiguous message stops to ask one question."""
    ss.received = text.strip()
    ss.applied = None
    event, err = safe_parse(ss.received, only_crew)
    if event and event["event"] != "unclear":
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


def _reset() -> None:
    ss.noon = None
    ss.applied = None
    ss.pop("view", None)


replanned = bool(ss.noon and "plan" in ss.noon)
views = ["Agent - noon", "Agent"] if replanned else ["Agent"]  # the live plan first
if ss.get("view") not in views:
    ss.view = views[0]

st.markdown("#### :material/engineering: Roads Dispatch · Calgary 311")
st.caption("Plan for Friday, Aug 28, 2026 · 8 crews × 5 jobs · frozen sample of 200 Open Calgary 311 tickets "
           "(Aug 25–27, 2026)")
if data["fake"]:
    st.warning("Showing sample data: run `python -m dispatch.run` to generate dispatch/outputs/.")

tab_dispatch, tab_analysis = st.tabs([":material/local_shipping: Dispatch", ":material/insights: Analysis"])

with tab_dispatch:
    with st.container(horizontal=True, vertical_alignment="center"):
        view = st.segmented_control("Plan shown", views, key="view", required=True,
                                    format_func=lambda v: VIEW_LABELS[v], label_visibility="collapsed")
        if view == "Agent":
            plan, m, changes, out_crew = data["plan_8am"], metrics["8am"], None, None
        else:
            plan, m, changes = ss.noon["plan"], ss.noon["metrics"]["noon"], ss.noon["changes"]
            out_crew = ss.noon["event"].get("crew")
        render_facts(view, plan, m)

    sel_job, sel_job_crew = find_job(plan, changes, ss.selected_job)
    sel_crew = find_crew(plan, ss.selected_crew)

    left, right = st.columns([5, 3], gap="medium")
    with left:
        render_map(plan, changes, sel_job, sel_crew, height=620)
        if ss.selected_job:
            render_job_detail(sel_job, sel_job_crew, VIEW_LABELS[view])
            if sel_crew:
                st.caption(f"Clear the job to go back to crew {sel_crew['crew']}'s list.")
        elif sel_crew:
            render_crew_panel(sel_crew, changes, out_crew)

    with right:
        st.markdown("**:material/forum: Dispatch assistant**")
        with st.container(border=True):
            render_assistant_turns(view, plan, changes)
            with st.form("crew_update", clear_on_submit=True, border=False):
                st.text_area("Crew update", key="update_text", height=80, label_visibility="collapsed",
                             placeholder="Report a crew update, e.g. hey it's crew 4, two guys called in sick")
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.form_submit_button("Send", type="primary", icon=":material/send:", on_click=_submit)
                    st.caption("Replans from the 8 a.m. plan; one crew at a time.")
            with st.container(horizontal=True, vertical_alignment="center"):
                with st.popover("Examples", icon=":material/lightbulb:"):
                    for text in QUICK_FILLS:
                        st.button(text, type="tertiary", on_click=_run, args=(text,), key=f"example_{text}")
                if replanned:
                    st.button("Reset to 8 a.m. plan", icon=":material/restart_alt:", type="tertiary",
                              on_click=_reset)
            if ss.noon and "error" in ss.noon:
                st.error(f"Replan engine isn't available: {ss.noon['error']}")

        st.markdown("**:material/groups: Crews**")
        st.caption("⚠️ hazard ticket · ↪ moved at noon · each crew works its own area")
        with st.container(height=420, border=True):
            render_crew_list(plan, changes, out_crew)

with tab_analysis:
    render_analysis(metrics)
