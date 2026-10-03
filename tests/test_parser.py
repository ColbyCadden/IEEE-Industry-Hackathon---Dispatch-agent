"""Crew-update parser tests. Run from the repo root: python -m tests.test_parser

Rules-only (no API key, no network): checks dispatch.llm.regex_parse_event.
"""
import sys

from dispatch.llm import regex_parse_event

# (text, event, crew, capacity)  None = don't care
T = [
 ("Crew 4 called in sick","crew_out",4,0.0),("crew 2 is down a guy","crew_partial",2,0.75),("Crew four is out","crew_out",4,0.0),
 ("crew #3 off today","crew_out",3,0.0),("C5 is out","crew_out",5,0.0),("team 6 is sick","crew_out",6,0.0),
 ("crew 3 is back","crew_partial",3,1.0),("crew 3 is not sick","crew_partial",3,1.0),("crew 4 is no longer out","crew_partial",4,1.0),
 ("crew 1 is at 75%","crew_partial",1,0.75),("crew 2 only has 3 of 5 people","crew_partial",2,0.6),("crew 7 has one person out","crew_partial",7,0.75),
 ("crew 8 half crew today","crew_partial",8,0.5),("two guys on crew 5 are out","crew_partial",5,0.5),("crew 4 truck broke down","crew_out",4,0.0),
 ("crew 3 won't make it in","crew_out",3,0.0),("crew 6 is running late","unclear",6,None),("crew 2 is down two guys","crew_partial",2,0.5),
 ("crew 5 is fully staffed","crew_partial",5,1.0),("Sick: crew 4","crew_out",4,0.0),("4 is out","crew_out",4,0.0),
 ("crew number 4 out","crew_out",4,0.0),("crew 4 out for the day","crew_out",4,0.0),("Crew 4: called in sick","crew_out",4,0.0),
 ("crew 4 out, crew 5 short-handed","unclear",None,None),("crew 4 and crew 5 are both out","unclear",None,None),("crews 2 and 6 called in sick","unclear",None,None),
 ("crew 1 and 2 out","unclear",None,None),("everyone is out","unclear",None,None),("all crews out","unclear",None,None),
 ("crew 0 sick","unclear",None,None),("crew 9 sick","unclear",None,None),("crew 12 is out","unclear",None,None),("crew 4","unclear",4,None),
 ("crew 2 has the flu","crew_out",2,0.0),("crew 3 is on vacation","crew_out",3,0.0),("crew 5 got pulled for an emergency","crew_out",5,0.0),
 ("crew 4 is done for today","crew_out",4,0.0),("crew 4 left early","crew_out",4,0.0),("crew 3 is out sick but crew 6 is fine","crew_out",3,0.0),
 ("hey its crew 4, two guys are sick, we're done today","crew_out",4,None),("Someone called in sick","unclear",None,None),("CREW 7 OUT","crew_out",7,0.0),
 ("crew 2 is short-handed","crew_partial",2,0.5),("crew 2 is shorthanded today","crew_partial",2,0.5),("Crew five is short two people","crew_partial",5,0.5),
 ("crew 6 off today","crew_out",6,0.0),("crew 3 checking in","unclear",3,None),("crew 1 is 2 of 4 today","crew_partial",1,0.5),
 ("2 out of 5 on crew 3 are sick","crew_partial",3,0.6),("crew 3 is at 0%","crew_out",3,0.0),("crew 8 is down 3 guys","crew_partial",8,0.25),
 ("crew 4 is down all four guys","crew_out",4,0.0),("crew 2 is not short-handed","crew_partial",2,1.0),("crew 6 isn't coming in","crew_out",6,0.0),
 ("crew 7 is sick and crew 2 is fine","crew_out",7,0.0),("crew 3 is down","crew_out",3,0.0),("The crew 3 guys are out sick","crew_out",3,0.0),
 ("crew 4 lost a guy to the flu","crew_partial",4,None),("crew 1, 4 and 6 are out","unclear",None,None),("crew 5 out. crew 5 only","crew_out",5,0.0),
]

bad = 0
for text, ev, crew, cap in T:
    e = regex_parse_event(text)
    ok = (e["event"] == ev and (crew is None or e["crew"] == crew)
          and (cap is None or abs(e["capacity"] - cap) < 1e-9))
    if not ok:
        bad += 1
        print("FAIL:", repr(text), "->", e["event"], e.get("crew"), e.get("capacity"))
print(f"{len(T) - bad}/{len(T)} parser cases pass")
sys.exit(1 if bad else 0)
