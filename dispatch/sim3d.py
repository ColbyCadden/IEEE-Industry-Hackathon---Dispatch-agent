"""Bridge to Semir's 3D downtown simulation (simulation/calgary3d): our plan's jobs as pins in his scene.

The 3D city is a separate server (SUMO + three.js, http://localhost:8765). The dashboard writes today's
downtown jobs to calgary3d/web/dispatch_plan.json in the scene's own coordinates (SUMO network x/y), and
web/plan_pins.js draws them. Nothing here touches the traffic simulation.
"""
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "simulation"
NET = SIM / "calgary" / "dt.net.xml"
WEB = SIM / "calgary3d" / "web"
SERVER = SIM / "calgary3d" / "server" / "server.py"
PINS = WEB / "dispatch_plan.json"
URL = "http://localhost:8765/"
LOG = Path(os.environ.get("TEMP", "/tmp")) / "calgary3d_server.log"


def _net_location() -> dict:
    """netOffset and the lon/lat box the SUMO network covers (read from dt.net.xml's <location> tag)."""
    head = NET.read_text(encoding="utf-8", errors="ignore")[:4000]
    loc = re.search(r"<location[^>]*>", head).group(0)
    off = [float(v) for v in re.search(r'netOffset="([^"]+)"', loc).group(1).split(",")]
    w, s, e, n = (float(v) for v in re.search(r'origBoundary="([^"]+)"', loc).group(1).split(","))
    zone = int(re.search(r"zone=(\d+)", loc).group(1))
    return {"offset": off, "box": (w, s, e, n), "zone": zone}


try:
    LOCATION = _net_location()
except Exception:  # simulation folder missing: the section says so instead of failing
    LOCATION = None


def _utm(lat: float, lon: float, zone: int) -> tuple[float, float]:
    """WGS84 lat/lon -> UTM easting/northing (northern hemisphere), the projection SUMO used for the network."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi, lam = math.radians(lat), math.radians(lon)
    lam0 = math.radians(zone * 6 - 183)
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    t, c = math.tan(phi) ** 2, ep2 * math.cos(phi) ** 2
    A = math.cos(phi) * (lam - lam0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * phi))
    x = k0 * n * (A + (1 - t + c) * A ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * A ** 5 / 120) + 500000
    y = k0 * (m + n * math.tan(phi) * (A * A / 2 + (5 - t + 9 * c + 4 * c * c) * A ** 4 / 24
                                       + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * A ** 6 / 720))
    return x, y


def to_scene(lat: float, lon: float) -> tuple[float, float]:
    """lat/lon -> the 3D scene's x/y (SUMO network coordinates)."""
    x, y = _utm(lat, lon, LOCATION["zone"])
    return x + LOCATION["offset"][0], y + LOCATION["offset"][1]


def inside(lat: float, lon: float) -> bool:
    w, s, e, n = LOCATION["box"]
    return w <= lon <= e and s <= lat <= n


def downtown_jobs(plan: dict) -> list[dict]:
    """Jobs on the plan that fall inside the simulated downtown area."""
    if LOCATION is None:
        return []
    return [{**j, "crew": c["crew"], "zone": c["zone"]} for c in plan["crews"] for j in c["jobs"]
            if inside(j["lat"], j["lon"])]


def write_pins(plan: dict, colors: dict) -> list[dict]:
    """Write the downtown jobs for plan_pins.js; returns them. colors: crew -> [r, g, b]."""
    jobs = downtown_jobs(plan)
    pins = []
    for j in jobs:
        x, y = to_scene(j["lat"], j["lon"])
        pins.append({"id": j["id"], "crew": j["crew"], "type": j["type"], "P": j["P"], "safety": j["safety"],
                     "community": j["community"], "x": round(x, 1), "y": round(y, 1),
                     "color": "#%02x%02x%02x" % tuple(colors[j["crew"]][:3])})
    body = json.dumps({"jobs": pins}, indent=1)
    try:
        if not PINS.exists() or PINS.read_text(encoding="utf-8") != body:
            PINS.write_text(body, encoding="utf-8")
    except OSError:
        pass
    return jobs


def running(timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(URL + "api/config", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def sumo_python() -> str | None:
    """A Python that has SUMO installed: the shared venv from setup, else simulation/.venv."""
    for p in (Path.home() / ".venvs" / "sumo" / "Scripts" / "python.exe", SIM / ".venv" / "Scripts" / "python.exe",
              Path.home() / ".venvs" / "sumo" / "bin" / "python", SIM / ".venv" / "bin" / "python"):
        if p.exists():
            return str(p)
    return None


def start() -> str | None:
    """Start the 3D server in the background. Returns an error message, or None once it's launching."""
    py = sumo_python()
    if py is None:
        return "SUMO isn't installed: run simulation\\setup.bat once (it creates the SUMO Python environment)."
    # The venv isn't activated, so tell the server where the venv's SUMO binary is (server.py reads SUMO_BIN).
    venv = Path(py).parent.parent
    env = dict(os.environ)
    for home in (next(venv.glob("Lib/site-packages/sumo"), None), next(venv.glob("lib/python*/site-packages/sumo"), None)):
        if home is not None:
            exe = home / "bin" / ("sumo.exe" if sys.platform == "win32" else "sumo")
            if exe.exists():
                env.update(SUMO_BIN=str(exe), SUMO_HOME=str(home))
                break
    env["PATH"] = str(Path(py).parent) + os.pathsep + env.get("PATH", "")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    log = open(LOG, "w", encoding="utf-8")
    subprocess.Popen([py, "-u", str(SERVER)], cwd=str(SERVER.parent.parent), env=env, stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                     start_new_session=sys.platform != "win32")
    return None
