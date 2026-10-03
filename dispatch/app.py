"""Streamlit dashboard: `streamlit run dispatch/app.py` from the repo root."""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dispatch import llm  # noqa: E402
from dispatch.assign import CREWS, JOBS_PER_CREW, make_plan  # noqa: E402
from dispatch.data_prep import load_clean_tickets  # noqa: E402
from dispatch.metrics import compute  # noqa: E402
from dispatch.replan import apply_event  # noqa: E402
from dispatch.scoring import score  # noqa: E402

CREW_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#393b79", "#637939",
]
STATUS_COLORS = {"moved": "#fff3cd", "dropped": "#f8d7da"}
TABLE_COLS = ["crew", "zone", "id", "type", "community", "P", "safety", "reports"]

st.set_page_config(page_title="311 Dispatch Agent", layout="wide")


@st.cache_data
def load_scored() -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    """(open tickets, scored tickets, using_temp_weights)."""
    tickets = load_clean_tickets()
    try:
        return tickets, score(tickets), False
    except ImportError:
        # TEMPORARY test table, same as the engine's __main__ blocks, until weights.py lands.
        tmp_weights = {
            "Roads - Pothole Maintenance": 3,
            "Roads - Signs - Missing - Damaged": 3,
            "Roads - Signs - Traffic and Roadmarking": 3,
            "Roads - Debris on Street/Sidewalk/Boulevard": 2,
            "Roads - Signs - Parking": 1,
            "WRS - Waste - Residential": 1,
            "WRS - Commercial Collection Services": 1,
            "WRS - New Service - Carts": 1,
        }
        tmp_safety = set(list(tmp_weights)[:4])
        tmp_short = {name: name.split(" - ", 1)[-1] for name in tmp_weights}
        return tickets, score(tickets, weights=tmp_weights, safety_types=tmp_safety,
                              short_names=tmp_short), True


@st.cache_data
def build_plan(order: str, crews: int, jobs: int) -> dict:
    scored = load_scored()[1]
    return make_plan(scored, order=order, crews=crews, jobs=jobs)


def plan_df(plan: dict) -> pd.DataFrame:
    """Flatten {"crews": [{"crew", "zone", "jobs": [...]}]} into one row per job."""
    rows = [{"crew": c["crew"], "zone": c["zone"], **j} for c in plan["crews"] for j in c["jobs"]]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(["crew", "P"], ascending=[True, False]).reset_index(drop=True)


def crew_color(crew: int) -> str:
    return CREW_COLORS[(crew - 1) % len(CREW_COLORS)]


def crew_map(df: pd.DataFrame) -> None:
    pts = df.copy()
    pts["color"] = pts["crew"].map(crew_color)
    st.map(pts, latitude="lat", longitude="lon", color="color", size=80)
    legend = " &nbsp; ".join(
        f"<span style='color:{crew_color(c)}'>■</span> Crew {c}" for c in sorted(pts["crew"].unique())
    )
    st.markdown(f"<small>{legend}</small>", unsafe_allow_html=True)


def replan_table(before: dict, changes: dict) -> pd.DataFrame:
    """Every 8 a.m. job with its noon crew and status: kept / moved / dropped."""
    df = plan_df(before).rename(columns={"crew": "crew_8am"})
    moved_to = {m["id"]: m["to"] for m in changes["moved"]}
    dropped = set(changes["dropped"])

    def status(row):
        if row["id"] in dropped:
            return "dropped"
        return "moved" if row["id"] in moved_to else "kept"

    df["status"] = df.apply(status, axis=1)
    df["crew_noon"] = [
        pd.NA if s == "dropped" else moved_to.get(i, c)
        for i, c, s in zip(df["id"], df["crew_8am"], df["status"])
    ]
    df["crew_noon"] = df["crew_noon"].astype("Int64")
    order = {"dropped": 0, "moved": 1, "kept": 2}
    df = df.sort_values(["status", "crew_8am", "P"], ascending=[True, True, False],
                        key=lambda s: s.map(order) if s.name == "status" else s)
    return df[["status", "crew_8am", "crew_noon", "id", "type", "community", "P", "safety"]]


def highlight(row):
    color = STATUS_COLORS.get(row["status"], "")
    return [f"background-color: {color}; color: #000" if color else ""] * len(row)


def ask_claude(fn, *args, **kwargs):
    """Call the LLM layer; show the problem instead of a traceback."""
    try:
        return fn(*args, **kwargs)
    except llm.LLMUnavailable as e:
        st.error(f"Claude unavailable: {e}")
    except Exception as e:  # keep the dashboard alive during the demo
        st.error(f"Claude call failed: {type(e).__name__}: {e}")
    return None


def event_label(event: dict) -> str:
    if event["event"] == "crew_out":
        return f"Crew {event['crew']} out for the day"
    if event["event"] == "crew_partial":
        return f"Crew {event['crew']} at {event['capacity']:.0%} capacity"
    return "No change to the plan"


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    crews = st.slider("Crews", 2, 12, CREWS)
    jobs = st.slider("Jobs per crew", 1, 10, JOBS_PER_CREW)
    st.divider()
    if llm.has_key():
        st.success(f"Claude connected ({llm.MODEL})")
    else:
        st.error("No ANTHROPIC_API_KEY found. Briefings and message reading are disabled. "
                 "Add `ANTHROPIC_API_KEY=...` to a `.env` file in the repo root.")

# A new crew setup invalidates any replan and briefings from the old setup.
if st.session_state.get("setup") != (crews, jobs):
    for key in ["pending_event", "event", "plan_noon", "changes", "brief_8am", "brief_noon"]:
        st.session_state.pop(key, None)
    st.session_state["setup"] = (crews, jobs)

# ---------------------------------------------------------------- 8 a.m. plans
tickets, scored, temp_weights = load_scored()
plan_8am = build_plan("priority", crews, jobs)
plan_fifo = build_plan("fifo", crews, jobs)
m_agent, m_fifo = compute(plan_8am), compute(plan_fifo)
df_8am = plan_df(plan_8am)

st.title("311 Dispatch Agent")
st.caption("Who should 311 send next? A priority-scored crew plan vs oldest-first, "
           "replanned live when a crew drops out.")
if temp_weights:
    st.warning("`weights.py` isn't ready yet, so scores use the engine's temporary test weights.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Open problems", len(tickets), help="Open tickets after merging duplicate reports")
c2.metric("Crew slots today", crews * jobs)
c3.metric("Priority covered (P)", f"{m_agent['P']:.1f}", f"{m_agent['P'] - m_fifo['P']:+.1f} vs oldest-first")
c4.metric("Safety jobs covered", m_agent["safety"], f"{m_agent['safety'] - m_fifo['safety']:+d} vs oldest-first",
          help=f"{int(scored['safety'].sum())} safety tickets in the backlog")

tab_plan, tab_disrupt, tab_tickets = st.tabs(["8 a.m. plan", "Disruption & replan", "All scored tickets"])

# ---------------------------------------------------------------- tab: 8 a.m. plan
with tab_plan:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("Crew assignments")
        st.dataframe(df_8am[TABLE_COLS], hide_index=True, width="stretch", height=420)
    with right:
        st.subheader("Map")
        crew_map(df_8am)

    st.subheader("Scored plan vs oldest-first")
    comp = pd.DataFrame(
        {"Scored plan": m_agent, "Oldest-first": m_fifo}
    ).rename(index={"P": "Priority points (P)", "safety": "Safety jobs", "n": "Jobs assigned"})
    comp["Difference"] = comp["Scored plan"] - comp["Oldest-first"]
    st.dataframe(comp, width="stretch")

    st.subheader("8 a.m. briefing")
    if st.button("Generate 8 a.m. briefing", disabled=not llm.has_key()):
        with st.spinner("Claude is writing the briefing..."):
            payload = {
                "crews": crews,
                "jobs_per_crew": jobs,
                "scored_plan": m_agent,
                "oldest_first_baseline": m_fifo,
                "safety_tickets_in_backlog": int(scored["safety"].sum()),
                "jobs_by_type": df_8am["type"].value_counts().to_dict(),
                "crew_zones": {c["crew"]: c["zone"] for c in plan_8am["crews"]},
            }
            st.session_state["brief_8am"] = ask_claude(llm.briefing, payload, "8am")
    if st.session_state.get("brief_8am"):
        st.info(st.session_state["brief_8am"])

# ---------------------------------------------------------------- tab: disruption
with tab_disrupt:
    st.subheader("Message from the field")
    msg = st.text_input(
        "What happened?",
        placeholder="e.g. Crew 4 just called in sick / Crew 2 is short two guys today",
    )
    if st.button("Read message", disabled=not (msg and llm.has_key())):
        with st.spinner("Claude is reading the message..."):
            st.session_state["pending_event"] = ask_claude(llm.parse_event, msg, crews)
        for key in ["event", "plan_noon", "changes", "brief_noon"]:
            st.session_state.pop(key, None)

    pending = st.session_state.get("pending_event")
    if pending:
        st.markdown(f"**Claude's reading:** {event_label(pending)}  \n_{pending['reason']}_")
        if pending["event"] == "unclear":
            st.warning("No replan needed, or Claude couldn't tell which crew. Try rewording the message.")
        elif st.button(f"Confirm and replan: {event_label(pending)}", type="primary"):
            event = {k: pending[k] for k in ("event", "crew", "capacity")}
            new_plan, changes = apply_event(plan_8am, event, jobs)
            st.session_state.update(event=event, plan_noon=new_plan, changes=changes)

    if st.session_state.get("plan_noon") is not None:
        event = st.session_state["event"]
        plan_noon = st.session_state["plan_noon"]
        changes = st.session_state["changes"]
        m_noon = compute(plan_noon, changes)

        st.divider()
        st.subheader(f"Noon replan: {event_label(event)}")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Moved to another crew", m_noon["moved"])
        d2.metric("Dropped", m_noon["dropped"])
        d3.metric("Safety jobs dropped", m_noon["safety_dropped"])
        d4.metric("Priority covered (P)", f"{m_noon['P']:.1f}", f"{m_noon['P'] - m_agent['P']:+.1f} vs 8 a.m.")

        left, right = st.columns([3, 2])
        with left:
            table = replan_table(plan_8am, changes)
            st.dataframe(table.style.apply(highlight, axis=1).format({"P": "{:.2f}"}),
                         hide_index=True, width="stretch", height=420)
        with right:
            crew_map(plan_df(plan_noon))

        st.subheader("Noon briefing")
        if st.button("Generate noon briefing", disabled=not llm.has_key()):
            with st.spinner("Claude is writing the briefing..."):
                payload = {
                    "plan_8am": m_agent,
                    "plan_noon": m_noon,
                    "moved": changes["moved"],
                    "dropped_jobs": [
                        {k: j[k] for k in ("id", "type", "community", "P", "safety")}
                        for j in changes["dropped_jobs"]
                    ],
                }
                st.session_state["brief_noon"] = ask_claude(llm.briefing, payload, "noon", event)
        if st.session_state.get("brief_noon"):
            st.info(st.session_state["brief_noon"])

# ---------------------------------------------------------------- tab: tickets
with tab_tickets:
    cols = ["id", "type", "community", "requested_date", "days_open", "reports", "weight", "P", "safety"]
    st.dataframe(scored.sort_values("P", ascending=False)[cols], hide_index=True, width="stretch")
