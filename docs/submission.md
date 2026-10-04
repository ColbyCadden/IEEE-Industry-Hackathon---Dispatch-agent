# Submission draft

Draft for the submission form. Paste each section into its matching field.

Before submitting:
- Re-run `python -m dispatch.run` and `python -m tests.sensitivity` and check every number below.
- Resolve every **TODO**.
- Target: submitted by **11:00 AM MDT Sunday** (form closes 12:00 PM).

---

## Title

**City Link — who should 311 send next? A dispatch agent for Calgary Roads**

## Tagline (3 lines)

> Oldest-first leaves potholes and missing stop signs behind parking complaints.
> Our agent plans 8 crews' day around risk and covers all 30 open safety problems, where FIFO covers 16.
> When a crew calls in sick, it replans in seconds and drops zero safety jobs.

---

## About the project

### Inspiration

Calgary 311 gets potholes, broken signs, debris, missed garbage and cart requests all in one
queue. The default way to work that queue is oldest-first, which sounds fair but isn't: a
missing stop sign reported this morning waits behind a parking-sign complaint from yesterday.
Then a crew calls in sick, and the supervisor has to re-plan the day by hand.

The starter code for this case tried to fix the first problem with keyword matching, and showed
us how easy it is to get wrong. It gave ice and snow top priority by checking whether
`"ice"` appeared in the service name. But "ice" is inside "Serv**ice**s" and "L**ice**nce", so
the starter's "priority" plan spent **28 of its 40 crew slots** on cart deliveries, commercial
collection and licence inspections. It also scheduled 23 tickets that were already closed, and
not one damaged sign. We wanted a dispatcher that a Roads supervisor could actually trust.

### How we built it

A deterministic Python pipeline (pandas, NumPy, SciPy) does the planning. Claude (Anthropic API)
handles language: it reads the supervisor's free-text crew update and writes the briefings. A
Streamlit dashboard ties them together. Without an API key or network, a rule-based parser and
template briefings take over, and the dashboard labels which one answered.

1. **Clean.** We load the 311 sample and drop closed tickets. Duplicate reports of the same
   problem (same service type at the same spot, rounded to about 1 m) are merged into one job
   with a `reports` count. That turns 200 rows into 122 open tickets and 104 distinct problems.
2. **Score.** Every problem gets a priority

   $$P = w_{\text{type}} + 0.25 \cdot d_{\text{open}} + 0.5 \cdot (r - 1)$$

   where $w_{\text{type}} \in \{0,1,2,3\}$ is the type weight, $d_{\text{open}}$ is days open
   and $r$ is the number of reports. Weights come from an explicit table keyed on the exact
   service names, each with a written reason:
   - 3 is a safety hazard (potholes, missing or damaged signs).
   - 2 is a road hazard (debris, traffic markings).
   - 1 is a service request.
   - 0 is not a field-crew job.
3. **Choose the work.** The top 40 tickets in priority order make the day's core plan. The FIFO
   baseline takes the 40 oldest instead; everything after this step is the same code.
4. **Assign compact crews.** The 40 jobs are split into 8 tight groups that minimise driving
   (capacitated k-means with an optimal Hungarian assignment), then a clean-up pass swaps jobs
   between crews until no swap shortens anyone's drive. For the agent, the same 32 workers are
   split by workload (3 to 6 per crew), and a crew's job limit follows its size plus its short
   hops (jobs within 1 km of each other), up to 7 jobs. Spare room takes the next tickets nearby,
   so the agent covers 48 jobs with the same people. FIFO keeps standard crews (4 people, 5 jobs).
5. **Disrupt and replan.** A free-text sick call ("Crew 4 is out today") is parsed by an LLM into
   a structured event. If the message is unclear, it asks a question instead of guessing. Each
   of the lost crew's jobs, highest priority first, can bump the lowest-priority job of one of
   its 4 nearest crews. Every change is logged as moved or dropped.
6. **Brief.** The LLM writes a plain-English 8 a.m. briefing, a short briefing after every
   update, and an end-of-day overview for the supervisor, all from the computed metrics.

**Results on the sample (8 crews, 32 workers):**

| | FIFO | Agent 8 a.m. | Agent noon (crew 4 out) |
|---|---|---|---|
| Safety problems covered (of 30) | 16 | **30** | **30** |
| Total priority $\sum P$ | 99.0 | **140.75** | 130.0 |
| Jobs on the plan | 40 | **48** | 42 |
| Jobs moved / dropped | — | — | 6 / 6, **0 safety dropped** |

Driving: compact crews cut straight-line driving from 4.2 km to 2.8 km per job.

### Challenges we ran into

- **Substring matching is a trap.** The starter's `"ice"` check silently mis-ranked 4 of the
  10 ticket types. We replaced it with exact service names, plus a test that fails if a new
  type shows up without a weight.
- **Duplicates inflate the backlog.** The same pothole reported twice is one job, not two. We
  merge duplicates and let repeat reports raise priority instead.
- **Keeping the comparison fair.** It's easy to make FIFO look bad by giving it a worse setup.
  Both plans share the same crews, the same 32 workers and the same assignment code, and a
  test checks this.
- **Our first replan sent crews across the city.** With no distance limit, one of the sick
  crew's jobs went **34.9 km** to the far side of Calgary for a tiny priority gain. Moves are
  now limited to the 4 nearest crews, and the crew-4 replan still drops no safety job. Crew 4
  works the far south, so its jobs travel 9–24 km to the neighbouring crews that take them.
- **Hand-picked weights invite the question "did you tune this to win?"** We answered it
  with a sensitivity test (below).

### What we learned

- **A transparent rule beats a clever one.** A 10-line weight table with reasons is easier to
  defend, test and hand to a Roads supervisor than keyword logic.
- **Test the result's robustness, not just the code.** We moved every type's weight by ±1
  (16 variations), and the agent beat FIFO on safety in all 16, by +12 to +15 tickets.
- **Keep the LLM on language and the planning in code.** The LLM turns messy human messages
  into structured events and writes the briefings. The decisions stay deterministic and
  auditable.
- **Disruptions are where dispatch really gets hard.** Moved and dropped counts are what a
  supervisor needs to see at noon.

### What's next

- A shadow-mode pilot with one Calgary Roads district, connected to the city's work-order system.
- Job durations and crew skills.
- Real road routing.
- More disruption types (blizzard, equipment breakdown).
- Weights validated with Roads staff and calibrated on historical close-out data.

---

## Built with

Python · pandas · NumPy · SciPy · Anthropic Claude API · Streamlit · Calgary 311 open data

**TODO:** add the repo link and team member names.

---

## Screenshots we need

Take these at the same browser zoom, in light mode, and crop to the app.

1. **8 a.m. plan map:** 8 coloured crews with 48 jobs, safety jobs highlighted.
2. **FIFO vs agent comparison:** side by side or toggled, showing 16 vs 30 safety problems.
3. **Starter bug evidence:** the `"ice" in name` line next to the 28/40 slot breakdown, or a
   terminal run showing it.
4. **Sick-call input and parsed event:** "Crew 4 called in sick…" → `crew_out`, crew 4.
5. **Unclear message:** a vague sick call where the agent asks a clarifying question.
6. **Noon plan:** crew 4 empty, with its jobs moved to neighbouring crews and the deferred jobs listed.
7. **8 a.m. and noon briefings:** the LLM-written supervisor text.
8. **Sensitivity table:** terminal output of `python -m tests.sensitivity`.
9. **Tests passing:** terminal output of `python -m tests.test_core` (11 PASS).
10. **Architecture diagram:** the mermaid flowchart from `docs/pitch.md`, rendered.
