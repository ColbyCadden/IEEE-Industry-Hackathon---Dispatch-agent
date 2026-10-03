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
               crew: dict | None = None) -> None:
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
    key = f"map_{selected['id'] if selected else ''}_{crew['crew'] if crew else ''}"
    st.pydeck_chart(deck, key=key, alt="Map of Calgary showing today's jobs, coloured by crew")
    st.caption("Colour = crew. Large dots = safety tickets. Black outline = moved at noon. Grey = deferred.")


OURS_COLOR = "rgb(44,160,44)"  # crew-palette green; readable on light and dark themes
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


def render_metrics(view: str, m: dict, fifo: dict, base: dict, out_crew: int | None) -> None:
    ours = base if view == "FIFO" else m  # the comparison always shows our plan vs oldest-first
    who = f"our noon plan (crew {out_crew} out)" if view == "Agent - noon" else "our agent, 8 a.m."
    c1, c2, c3, c4 = st.columns([2.2, 1.4, 1, 1])
    c1.markdown(_compare_html("Safety hazards covered — our agent vs oldest-first",
                              str(ours["safety"]), str(fifo["safety"]),
                              f"{who} · oldest-first (FIFO), all crews", 4.2), unsafe_allow_html=True)
    c2.markdown(_compare_html("Priority served (total P)", f"{ours['P']:.1f}", f"{fifo['P']:.1f}",
                              "our agent vs oldest-first", 2.2), unsafe_allow_html=True)
    if view == "Agent - noon":
        c3.markdown(_count_html("Jobs moved", str(m.get("moved", 0)), "to a nearby crew"),
                    unsafe_allow_html=True)
        c4.markdown(_count_html("Jobs deferred", str(m.get("dropped", 0)),
                                f"{m.get('safety_dropped', 0)} of them safety tickets"), unsafe_allow_html=True)
    else:
        c3.markdown(_count_html("Jobs moved", None, ""), unsafe_allow_html=True)
        c4.markdown(_count_html("Jobs deferred", None, ""), unsafe_allow_html=True)
    st.caption("**Priority score P** = hazard type (0–3) + 0.25 per day waiting + 0.5 per extra report. "
               "Higher = more urgent.")
    st.caption("**Robustness:** we re-ran the plan with every type weight changed by ±1 — 16 variations. "
               "Our agent covered more safety tickets than oldest-first in all 16, by between +9 and +15.")


def _stage_html(row: dict, tone: str) -> str:
    """One card of the improvement panel: safety tickets covered, then jobs and total P."""
    m = row["m"]
    title = (f"<div style='font-size:.8rem;font-weight:700;letter-spacing:.06em'>{row['stage']}"
             f"<span style='font-weight:400;opacity:.7'> · {row['what']}</span></div>")
    if m is None:
        return (f"<div style='{BOX};opacity:.45'>{title}"
                f"<div style='font-size:1rem;font-style:italic;margin-top:.5rem'>run python -m dispatch.run</div></div>")
    extra = (f"{m['moved']} moved · {m['deferred']} deferred · {m['safety_dropped']} safety dropped"
             if "moved" in m else "&nbsp;")
    return (f"<div style='{BOX}'>{title}"
            f"<div style='display:flex;align-items:baseline;gap:.5rem;margin:.15rem 0'>"
            f"<span style='font-size:2.6rem;font-weight:800;line-height:1.05;{tone}'>{m['safety']}</span>"
            f"<span style='font-size:.85rem;opacity:.7'>safety tickets covered</span></div>"
            f"<div style='font-size:.8rem;opacity:.8'>{m['jobs']} jobs · total P {m['P']:.2f}</div>"
            f"<div style='font-size:.8rem;opacity:.8'>{extra}</div></div>")


def render_improvement(metrics: dict) -> None:
    """Baseline, first result and improved result side by side, always on screen (no clicking)."""
    if improvement is None:
        return
    try:
        event = json.loads((OUT / "event.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        event = None
    try:
        rows = improvement.comparison_rows(metrics, event)
        summary = improvement.summary_line(rows, event)
    except Exception:  # unexpected metrics shape: hide the panel rather than break the page
        return
    st.markdown("**Improvement round:** baseline → first result → improved result "
                "(safety tickets covered, 8 crews × 5 jobs)")
    tones = {"fifo": "opacity:.45", "8am": f"color:{OURS_COLOR}", "noon": f"color:{OURS_COLOR}"}
    for col, row in zip(st.columns(3), rows):
        col.markdown(_stage_html(row, tones[row["key"]]), unsafe_allow_html=True)
    st.caption(summary)


def render_crews(plan: dict, changes: dict | None, out_crew: int | None) -> None:
    moved = {m["id"] for m in (changes or {}).get("moved", [])}
    cols = st.columns(4)
    for i, c in enumerate(plan["crews"]):
        with cols[i % 4].container(border=True):
            r, g, b = CREW_COLORS[(c["crew"] - 1) % len(CREW_COLORS)]
            is_sel = c["crew"] == st.session_state.selected_crew
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(f"<span style='color:rgb({r},{g},{b});font-size:1.3em'>●</span>",
                            unsafe_allow_html=True, width="content")
                st.button(f"**Crew {c['crew']}** · {c['zone']}", key=f"crew_{c['crew']}",
                          type="secondary" if is_sel else "tertiary", on_click=toggle_crew, args=(c["crew"],),
                          icon=":material/filter_center_focus:" if is_sel else None,
                          help="Selected: click again to show the whole city" if is_sel
                          else "Focus the map on this crew")
            if not c["jobs"]:
                st.caption("Out today" if c["crew"] == out_crew else "No jobs")
                continue
            safety = sum(j["safety"] for j in c["jobs"])
            st.caption(f"{len(c['jobs'])} jobs · {safety} safety · P {sum(j['P'] for j in c['jobs']):.1f}")
            for j in sorted(c["jobs"], key=lambda j: -j["P"]):
                tags = _job_label(j, moved)
                chosen = j["id"] == st.session_state.selected_job
                st.button(f"{j['type']} · {j['community'].title()} · P {j['P']:.2f}{tags}",
                          key=f"job_{j['id']}", type="secondary" if chosen else "tertiary",
                          icon=":material/my_location:" if chosen else None,
                          on_click=select_job, args=(None if chosen else j["id"],),
                          help="Selected: click again to clear" if chosen else "Show details and find on map")


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

st.title("Who should 311 send next?")
st.caption("Calgary Roads · 8 crews × 5 jobs · priority agent vs oldest-first (FIFO) · "
           "frozen sample of 200 Open Calgary 311 tickets (Aug 25–27, 2026), planned for Aug 28, 2026")
if data["fake"]:
    st.warning("Showing sample data: run `python -m dispatch.run` to generate dispatch/outputs/.")

render_improvement(metrics)

views = ["Agent", "FIFO"] + (["Agent - noon"] if ss.noon and "plan" in ss.noon else [])
if ss.get("view") not in views:
    ss.view = views[-1] if ss.noon and "plan" in ss.noon else "Agent"
view = st.radio("Plan shown", views, horizontal=True, key="view",
                captions=["8 a.m. priority plan", "oldest-first baseline", "after the crew update"][:len(views)])

if view == "FIFO":
    plan, m, changes, out_crew = data["plan_fifo"], metrics["fifo"], None, None
elif view == "Agent":
    plan, m, changes, out_crew = data["plan_8am"], metrics["8am"], None, None
else:
    plan, m, changes = ss.noon["plan"], ss.noon["metrics"]["noon"], ss.noon["changes"]
    out_crew = ss.noon["event"].get("crew")

render_metrics(view, m, metrics["fifo"], metrics["8am"], out_crew)

sel_job, sel_job_crew = find_job(plan, changes, ss.selected_job)
sel_crew = find_crew(plan, ss.selected_crew)

left, right = st.columns([3, 2])
with left:
    render_map(plan, changes, sel_job, sel_crew)
with right:
    if ss.selected_job:
        st.subheader("Selected job")
        render_job_detail(sel_job, sel_job_crew, view)
        if sel_crew:
            st.caption(f"Clear the job to go back to crew {sel_crew['crew']}'s list.")
    elif sel_crew:
        st.subheader("Selected crew")
        render_crew_panel(sel_crew, changes, out_crew)
    else:
        st.subheader("Briefing")
        with st.container(border=True):
            if view == "FIFO":
                st.write(f"Baseline: oldest tickets first, same crews and zones. It covers {m['safety']} safety "
                         f"tickets, versus {metrics['8am']['safety']} in the agent's plan.")
            else:
                if view == "Agent":
                    text, src = cached_briefing("8am", data["plan_8am"], metrics, "8am")
                else:
                    ev = ss.noon["event"]
                    text, src = cached_briefing(f"noon:{ev['event']}:{ev['crew']}:{ev.get('capacity')}",
                                                plan, ss.noon["metrics"], "noon", changes, event=ev)
                st.write(text)
                st.caption(SOURCE_NOTES[src])
        st.caption(":material/touch_app: Click a crew name or a job below to focus the map on it.")

    st.subheader("Report a crew update")

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

    with st.form("crew_update", clear_on_submit=True, border=False):
        st.text_area("Crew update", key="update_text", height=90, label_visibility="collapsed",
                     placeholder="Type a crew update and press Submit, e.g. hey it's crew 4, two guys called in sick")
        st.form_submit_button("Submit update", type="primary", icon=":material/send:", on_click=_submit)
    with st.expander("Try an example", expanded=False):
        for text in QUICK_FILLS:
            st.button(text, type="tertiary", on_click=_run, args=(text,), key=f"example_{text}")

    if ss.get("applied"):
        ev, got = ss.applied["event"], ss.applied["received"]
        how = {"claude": ":material/auto_awesome: Read by Claude", "manual": ":material/edit: Entered by hand"}.get(
            ev.get("source"), ":material/rule: Read by the rule-based parser"
            + (" (Claude unavailable: no API key or no connection)" if str(ev.get("why", "")).startswith("fallback")
               else " (no Claude call needed)"))
        with st.container(border=True):
            st.markdown(f":material/check_circle: **Replanned.** {describe(ev)}")
            if got:
                st.caption(f"\u201c{got}\u201d")
            st.caption(how)
            st.caption("Every replan starts from the 8 a.m. plan; one crew update applies at a time.")
            with st.expander("Parsed result (JSON)", expanded=False):
                st.json({k: v for k, v in ev.items() if k not in ("source", "why")})

    if ss.get("parse_error") and not ss.parsed:
        with st.container(border=True):
            st.error("Couldn't read that update automatically. Pick the crew and what happened:")
            st.caption(ss.parse_error)
            m_crew, m_kind = st.columns(2)
            crew = m_crew.selectbox("Crew", range(1, 9), index=3)
            kind = m_kind.radio("Status", ["Out for the day", "Short-handed (50%)"])
            st.button("Use this update", on_click=_manual, args=(crew, "crew_out" if kind.startswith("Out") else "crew_partial"))

    if ss.parsed:  # only reached for an ambiguous message
        event = ss.parsed
        with st.container(border=True):
            st.markdown("**:material/help: One detail needed**")
            st.code(ss.get("received", ""), language=None, wrap_lines=True)
            st.info(f"**{event.get('question') or 'Which crew is affected?'}**", icon=":material/help:")
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

    if ss.noon and "error" in ss.noon:
        st.error(f"Replan engine isn't available: {ss.noon['error']}")
    elif ss.noon:
        if st.button("Reset to 8 a.m. plan"):
            ss.noon = None
            ss.applied = None
            ss.pop("view", None)
            st.rerun()

st.subheader("Crews")
st.caption("⚠️ = safety ticket (potholes and missing or damaged signs). ↪ moved = reassigned at noon.")
st.caption("Each crew works its own area of the city, so a crew's mix of jobs reflects what was reported "
           "there today. That's why some crews carry more safety tickets than others.")
render_crews(plan, changes, out_crew)
