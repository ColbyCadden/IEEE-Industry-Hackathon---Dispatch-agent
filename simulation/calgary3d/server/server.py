#!/usr/bin/env python3
"""Live SUMO/TraCI HTTP server with SSE streaming, control API, and congestion endpoints.

Usage: python server.py [--port PORT]
Serves calgary3d/web/ at :8765 (default) or :PORT, starts SUMO headless, loops on sim end.
Uses ONLY stdlib + sumolib/traci.
GET /stream: SSE event stream (frame data)
POST /control: control parameters
GET /congestion.json: per-edge congestion levels + live incidents
GET /sources.json: all data sources (attribution, licence, status)
"""

import http.server
import json
import os
import shutil
import queue
import signal
import sys
import threading
import time
import traceback
import argparse
import uuid

# --- SUMO setup ---
# Locate SUMO: env var first, then PATH, then the historical local install.
def _find_sumo():
    env = os.environ.get('SUMO_BIN')
    if env and os.path.isfile(env):
        return os.path.normpath(env)
    found = shutil.which('sumo')
    if found:
        return os.path.normpath(found)
    for base in (os.environ.get('SUMO_HOME'),):
        if base:
            for cand in (os.path.join(base, 'sumo.exe'), os.path.join(base, 'bin', 'sumo.exe')):
                if os.path.isfile(cand):
                    return os.path.normpath(cand)
    # Last resort: the developer machine's tool install, if still present.
    legacy = os.path.expanduser(
        "~/AppData/Local/hermes/tools/python-3.14.7+20260901-win32-x64/Scripts/sumo.exe"
    )
    return os.path.normpath(legacy)


SUMO_BIN = _find_sumo()

# The SUMO network lives in ../calgary relative to calgary3d/, or wherever
# CALGARY_DIR points. Override with the CALGARY_DIR environment variable.
_HERE = os.path.dirname(os.path.abspath(__file__))
CALGARY_DIR = os.path.normpath(os.environ.get(
    'CALGARY_DIR', os.path.join(_HERE, os.pardir, os.pardir, 'calgary')))
WEB_DIR = os.path.normpath(os.path.join(_HERE, os.pardir, 'web'))

_script_dir = os.path.dirname(SUMO_BIN)
if 'SUMO_HOME' not in os.environ:
    os.environ['SUMO_HOME'] = _script_dir
_tools_dir = os.path.join(os.path.dirname(_script_dir), 'tools')
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)

import traci

# Import congestion module
try:
    from congestion import compute_congestion, get_sources
except ImportError:
    compute_congestion = None
    get_sources = None


# ============================================================
#  Shared state (thread-safe)
# ============================================================

class SimState:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.clients = []           # list of queue.Queue
        self.control = {
            'playbackRate': 1.0,
            'speedScale': 1.0,
            'demandScale': 1.0,
            'signalMode': 'normal',
            'closeEdges': [],
        }
        self.control_dirty = True
        self.reset_requested = False
        self.original_speeds = {}      # lane_id -> maxSpeed (unscaled)
        self.vehicle_id_map = {}       # string vehicle id -> stable int
        self.next_vehicle_int = 1
        self.frame_count = 0
        self.running = True
        self.closed_lanes = set()      # lane_ids currently disallowed
        self.signal_mode_current = 'normal'

    # ---- Client management ----
    def add_client(self, q):
        with self.lock:
            self.clients.append(q)

    def remove_client(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def broadcast(self, data_str):
        with self.lock:
            dead = []
            for q in self.clients:
                try:
                    q.put_nowait(data_str)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.clients.remove(q)

    # ---- Frame get/set ----
    def get_frame(self):
        with self.lock:
            return self.frame

    def set_frame(self, frame):
        with self.lock:
            self.frame = frame
            self.frame_count += 1

    # ---- Control ----
    def get_control_snapshot(self):
        with self.lock:
            return dict(self.control), self.control_dirty

    def mark_control_applied(self):
        with self.lock:
            self.control_dirty = False


state = SimState()


# Global scene data (roads, bounds, net_offset) - loaded once
scene_data = {
    'roads': [],
    'bounds': None,
    'net_offset': None,
}


# ============================================================
#  SSE client helper
# ============================================================

class SSEHandler:
    def __init__(self, wfile):
        self.wfile = wfile
        self.queue = queue.Queue(maxsize=200)
        self.active = True

    def start(self):
        state.add_client(self.queue)

    def stop(self):
        self.active = False
        state.remove_client(self.queue)

    def pump(self):
        while self.active:
            try:
                data = self.queue.get(timeout=1.0)
                self.wfile.write(data.encode('utf-8'))
                self.wfile.flush()
            except queue.Empty:
                try:
                    self.wfile.write(b': k\n\n')
                    self.wfile.flush()
                except Exception:
                    self.active = False
            except (BrokenPipeError, ConnectionResetError, OSError):
                self.active = False


# ============================================================
#  HTTP request handler
# ============================================================

class RequestHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress default stderr logging

    # ---- CORS helpers ----
    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self._cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- Routing ----
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/stream':
            self._handle_stream()
        elif path == '/congestion.json':
            self._handle_congestion()
        elif path == '/sources.json':
            self._handle_sources()
        elif path == '/requests.json':
            self._handle_requests()
        elif path == '/routes.json':
            self._serve_data_file('routes.json', '{"teams": []}')
        elif path == '/updates.json':
            self._handle_updates()
        elif path == '/potholes.json':
            self._handle_potholes()
        else:
            self._handle_static(path)

    def do_POST(self):
        if self.path == '/control':
            self._handle_control()
        elif self.path == '/call':
            self._handle_call()
        elif self.path == '/tts':
            self._handle_tts()
        elif self.path == '/call/next':
            self._handle_call_next()
        else:
            self._send_json(404, {'error': 'Not found'})

    # ---- Congestion endpoint ----
    def _handle_congestion(self):
        if not compute_congestion or not scene_data['roads']:
            self._send_json(503, {'error': 'Congestion model not available'})
            return
        try:
            result = compute_congestion(scene_data['roads'], scene_data['net_offset'])
            self._send_json(200, result)
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    # ---- 311 requests endpoint (display only; never touches SUMO) ----
    def _handle_requests(self):
        import csv
        f = os.path.join(_HERE, os.pardir, os.pardir, 'agents', 'data', 'requests.csv')
        out = []
        try:
            with open(f, encoding='utf-8', newline='') as fh:
                for r in csv.DictReader(fh):
                    out.append({'id': r['id'], 'category': r['category'], 'status': r['status'],
                                'source': r['source'], 'opened': r['opened'],
                                'location': r['location'], 'x': float(r['x']), 'y': float(r['y'])})
        except FileNotFoundError:
            pass
        # Overlay live call updates (resolved / reopened / escalated) on the city data.
        try:
            root = os.path.normpath(os.path.join(_HERE, os.pardir, os.pardir))
            if root not in sys.path:
                sys.path.insert(0, root)
            from agents.dispatch import updates as U
            st = U.state()
            for r in out:
                if r['id'] in st['resolved']:
                    r['status'] = 'Closed'; r['by_call'] = True
                elif any(u['request_id'] == r['id'] and u['action'] == 'reopened' for u in U.load()):
                    r['status'] = 'Open'; r['by_call'] = True
                if st['delta'].get(r['id'], 0) > 0:
                    r['escalated'] = True
        except Exception:
            pass
        self._send_json(200, {'requests': out,
                              'mtime': os.path.getmtime(f) if os.path.exists(f) else None})

    # ---- Dispatch: routes, live updates, calls, ElevenLabs TTS ----
    # Display / planning only: nothing here touches SUMO traffic.
    def _serve_data_file(self, name, default):
        f = os.path.join(_HERE, os.pardir, os.pardir, 'agents', 'data', name)
        try:
            with open(f, 'rb') as fh:
                body = fh.read()
        except FileNotFoundError:
            body = default.encode()
        self.send_response(200)
        self._cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        cl = int(self.headers.get('Content-Length', 0))
        try:
            return json.loads(self.rfile.read(cl).decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return None

    def _handle_updates(self):
        root = os.path.normpath(os.path.join(_HERE, os.pardir, os.pardir))
        if root not in sys.path:
            sys.path.insert(0, root)
        from agents.dispatch import updates as U
        st = U.state()
        self._send_json(200, {'resolved': sorted(st['resolved']), 'delta': st['delta'],
                              'log': U.load()[-30:]})

    def _handle_call(self):
        body = self._read_json_body()
        if body is None:
            self._send_json(400, {'error': 'Invalid JSON'})
            return
        root = os.path.normpath(os.path.join(_HERE, os.pardir, os.pardir))
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from agents.dispatch import calls
            res = calls.handle_call(body.get('transcript', ''), caller=body.get('caller', 'caller'),
                                    request_id=body.get('request_id'), action=body.get('action'),
                                    replan_now=not body.get('wait'))
            if body.get('wait') and res.get('applied'):
                calls.replan(blocking=True)
            self._send_json(200, res)
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _handle_call_next(self):
        """One turn of the voice call: the agent asks, the caller answers,
        the issue and the routes update."""
        body = self._read_json_body() or {}
        root = os.path.normpath(os.path.join(_HERE, os.pardir, os.pardir))
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            from agents.dispatch import calls
            res = calls.next_question(body.get('call_id') or uuid.uuid4().hex[:8],
                                      utterance=body.get('utterance', ''),
                                      request_id=body.get('request_id'),
                                      action=body.get('action'))
            self._send_json(200, res)
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _handle_tts(self):
        """Proxy to ElevenLabs text-to-speech so the API key stays server-side."""
        import urllib.request, urllib.error
        body = self._read_json_body() or {}
        text = (body.get('text') or '')[:400]
        key = os.environ.get('ELEVENLABS_API_KEY')
        if not key:
            for envf in (os.path.join(_HERE, os.pardir, os.pardir, '.env'),      # simulation/.env (gitignored)
                         os.path.join(os.environ.get('LOCALAPPDATA', ''), 'hermes', '.env')):
                try:
                    for line in open(envf, encoding='utf-8'):
                        if line.startswith('ELEVENLABS_API_KEY='):
                            key = line.split('=', 1)[1].strip().strip('"\'')
                except OSError:
                    pass
        if not key or not text:
            self._send_json(503, {'error': 'ELEVENLABS_API_KEY not configured' if not key else 'no text'})
            return
        voice = body.get('voice') or os.environ.get('ELEVENLABS_VOICE_ID', 'JBFqnCBsd6RMkjVDRZzb')
        req = urllib.request.Request(
            'https://api.elevenlabs.io/v1/text-to-speech/%s?output_format=mp3_44100_64' % voice,
            data=json.dumps({'text': text, 'model_id': 'eleven_flash_v2_5'}).encode(),
            headers={'xi-api-key': key, 'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                audio = r.read()
        except urllib.error.HTTPError as e:
            self._send_json(502, {'error': 'ElevenLabs %d' % e.code})
            return
        except Exception as e:
            self._send_json(502, {'error': str(e)})
            return
        self.send_response(200)
        self._cors_headers()
        self.send_header('Content-Type', 'audio/mpeg')
        self.send_header('Content-Length', str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)

    # ---- Potholes endpoint (display only; never touches SUMO) ----
    def _handle_potholes(self):
        import csv
        f = os.path.join(_HERE, os.pardir, os.pardir, 'agents', 'data', 'potholes.csv')
        out = []
        try:
            with open(f, encoding='utf-8', newline='') as fh:
                for r in csv.DictReader(fh):
                    if r.get('in_network') == '1':
                        out.append({'id': r['id'], 'status': r['status'], 'source': r['source'],
                                    'opened': r['opened'], 'location': r['location'],
                                    'x': float(r['x']), 'y': float(r['y'])})
        except FileNotFoundError:
            pass
        self._send_json(200, {'potholes': out,
                              'mtime': os.path.getmtime(f) if os.path.exists(f) else None})

    # ---- Sources endpoint ----
    def _handle_sources(self):
        if not get_sources or not scene_data['roads']:
            self._send_json(503, {'error': 'Sources metadata not available'})
            return
        try:
            result = get_sources((scene_data['net_offset'], scene_data['bounds'],
                                  len(scene_data['roads']), 0))
            self._send_json(200, result)
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    # ---- Static files ----
    def _handle_static(self, path):
        clean = path.lstrip('/')
        if clean == '':
            clean = 'index.html'
        safe = os.path.normpath(clean)
        filepath = os.path.join(WEB_DIR, safe)

        # Security: must be under WEB_DIR
        if not os.path.abspath(filepath).startswith(os.path.abspath(WEB_DIR)):
            self._send_json(403, {'error': 'Forbidden'})
            return

        if os.path.isdir(filepath):
            filepath = os.path.join(filepath, 'index.html')

        if not os.path.isfile(filepath):
            self._send_json(404, {'error': 'Not found'})
            return

        ext = os.path.splitext(filepath)[1].lower()
        mime = {
            '.html': 'text/html',
            '.js':   'application/javascript',
            '.json': 'application/json',
            '.css':  'text/css',
            '.wasm': 'application/wasm',
            '.png':  'image/png',
            '.jpg':  'image/jpeg',
            '.svg':  'image/svg+xml',
        }.get(ext, 'application/octet-stream')

        with open(filepath, 'rb') as f:
            body = f.read()

        self.send_response(200)
        self._cors_headers()
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- SSE /stream ----
    def _handle_stream(self):
        self.send_response(200)
        self._cors_headers()
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()

        sse = SSEHandler(self.wfile)
        sse.start()
        try:
            sse.pump()
        finally:
            sse.stop()

    # ---- POST /control ----
    def _handle_control(self):
        cl = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(cl)
        try:
            body = json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            self._send_json(400, {'error': 'Invalid JSON'})
            return

        errors = []

        with state.lock:
            # playbackRate
            if 'playbackRate' in body:
                try:
                    v = float(body['playbackRate'])
                    if 0 <= v <= 4:
                        state.control['playbackRate'] = v
                        state.control_dirty = True
                    else:
                        errors.append('playbackRate must be 0..4')
                except (TypeError, ValueError):
                    errors.append('playbackRate must be a number')

            # speedScale
            if 'speedScale' in body:
                try:
                    v = float(body['speedScale'])
                    if 0.2 <= v <= 2.0:
                        state.control['speedScale'] = v
                        state.control_dirty = True
                    else:
                        errors.append('speedScale must be 0.2..2.0')
                except (TypeError, ValueError):
                    errors.append('speedScale must be a number')

            # demandScale
            if 'demandScale' in body:
                try:
                    v = float(body['demandScale'])
                    if 0 <= v <= 3:
                        state.control['demandScale'] = v
                        state.control_dirty = True
                    else:
                        errors.append('demandScale must be 0..3')
                except (TypeError, ValueError):
                    errors.append('demandScale must be a number')

            # signalMode
            if 'signalMode' in body:
                v = body['signalMode']
                if v in ('normal', 'allRed', 'allGreen', 'flashing'):
                    state.control['signalMode'] = v
                    state.control_dirty = True
                else:
                    errors.append('signalMode must be normal|allRed|allGreen|flashing')

            # closeEdges
            if 'closeEdges' in body:
                v = body['closeEdges']
                if isinstance(v, list) and all(isinstance(e, str) for e in v):
                    state.control['closeEdges'] = v
                    state.control_dirty = True
                else:
                    errors.append('closeEdges must be a string array')

            # reset
            if body.get('reset') is True:
                state.reset_requested = True
                state.control_dirty = True

            if errors:
                self._send_json(400, {'error': '; '.join(errors)})
                return

            resp = dict(state.control)
            self._send_json(200, resp)


# ============================================================
#  TraCI simulation loop (runs in background thread)
# ============================================================

def _apply_speed_scale(scale, originals):
    """Set every lane's maxSpeed = original * scale."""
    try:
        lane_ids = traci.lane.getIDList()
        for lid in lane_ids:
            if lid not in originals:
                originals[lid] = traci.lane.getMaxSpeed(lid)
            traci.lane.setMaxSpeed(lid, originals[lid] * scale)
    except traci.exceptions.TraCIException:
        pass


def _apply_demand_scale(scale):
    try:
        traci.simulation.setScale(scale)
    except traci.exceptions.TraCIException:
        pass


def _apply_signal_mode(mode):
    """Override or restore TLS programs."""
    tls_ids = traci.trafficlight.getIDList()
    for tid in tls_ids:
        try:
            if mode == 'normal':
                traci.trafficlight.setProgram(tid, '0')
            elif mode == 'allRed':
                cur = traci.trafficlight.getRedYellowGreenState(tid)
                traci.trafficlight.setRedYellowGreenState(tid, 'r' * len(cur))
            elif mode == 'allGreen':
                cur = traci.trafficlight.getRedYellowGreenState(tid)
                traci.trafficlight.setRedYellowGreenState(tid, 'G' * len(cur))
            elif mode == 'flashing':
                cur = traci.trafficlight.getRedYellowGreenState(tid)
                traci.trafficlight.setRedYellowGreenState(tid, 'y' * len(cur))
        except traci.exceptions.TraCIException:
            pass


def _apply_close_edges(edge_ids):
    """Close listed edges; reopen previously closed ones."""
    # Reopen all previously closed
    for lid in list(state.closed_lanes):
        try:
            traci.lane.setAllowed(lid, ['passenger'])
        except traci.exceptions.TraCIException:
            pass
    state.closed_lanes.clear()

    # Close requested
    for eid in edge_ids:
        try:
            n = traci.edge.getLaneNumber(eid)
            for i in range(n):
                lid = f"{eid}_{i}"
                traci.lane.setAllowed(lid, [])
                state.closed_lanes.add(lid)
        except traci.exceptions.TraCIException:
            pass


def apply_control_changes():
    """Apply any pending control changes to the simulation."""
    ctrl, dirty = state.get_control_snapshot()
    if not dirty:
        return

    _apply_speed_scale(ctrl['speedScale'], state.original_speeds)
    _apply_demand_scale(ctrl['demandScale'])

    if ctrl['signalMode'] != state.signal_mode_current:
        _apply_signal_mode(ctrl['signalMode'])
        state.signal_mode_current = ctrl['signalMode']

    _apply_close_edges(ctrl['closeEdges'])

    state.mark_control_applied()


def sim_loop():
    """Run SUMO headless, loop on end, respond to control."""
    config = os.path.join(CALGARY_DIR, "dt.sumocfg")

    while state.running:
        print(f"[sim] Starting SUMO with {config}", flush=True)

        try:
            traci.start([
                SUMO_BIN, "-c", config,
                "--step-length", "0.5",
                "--start",
                "--no-warnings", "true", "--no-step-log", "true",
            ])
        except Exception as e:
            print(f"[sim] SUMO start failed: {e}", flush=True)
            time.sleep(5)
            continue

        # ---- Per-run init ----
        state.original_speeds.clear()
        state.vehicle_id_map.clear()
        state.next_vehicle_int = 1
        state.signal_mode_current = 'normal'
        state.closed_lanes.clear()

        # Snapshot original lane speeds
        try:
            for lid in traci.lane.getIDList():
                state.original_speeds[lid] = traci.lane.getMaxSpeed(lid)
        except traci.exceptions.TraCIException:
            pass

        # Apply initial control state
        state.control_dirty = True
        try:
            apply_control_changes()
        except Exception:
            pass

        last_tls_states = {}

        try:
            while state.running:
                # ----- Reset check -----
                if state.reset_requested:
                    state.reset_requested = False
                    print("[sim] Reset requested, restarting SUMO...", flush=True)
                    break

                # ----- Pause -----
                ctrl, _ = state.get_control_snapshot()
                if ctrl['playbackRate'] == 0:
                    time.sleep(0.1)
                    # Still apply control changes while paused
                    apply_control_changes()
                    continue

                # ----- Step -----
                wall_start = time.time()
                try:
                    traci.simulationStep()
                except traci.exceptions.FatalTraCIError:
                    print("[sim] Fatal TraCI error, restarting...", flush=True)
                    break

                if traci.simulation.getMinExpectedNumber() == 0:
                    print("[sim] Simulation ended (no vehicles), restarting...", flush=True)
                    break

                # Apply pending control
                apply_control_changes()

                t = traci.simulation.getTime()
                fc = state.frame_count
                ctrl, _ = state.get_control_snapshot()

                # --- Vehicles ---
                vids = traci.vehicle.getIDList()
                vehicles = []
                for vid in vids:
                    # Stable integer id
                    if vid not in state.vehicle_id_map:
                        state.vehicle_id_map[vid] = state.next_vehicle_int
                        state.next_vehicle_int += 1
                    iid = state.vehicle_id_map[vid]
                    try:
                        x, y = traci.vehicle.getPosition(vid)
                        a = traci.vehicle.getAngle(vid)
                        s = traci.vehicle.getSpeed(vid)
                        vehicles.append([iid, round(x, 1), round(y, 1), round(a, 1), round(s, 1)])
                    except traci.exceptions.TraCIException:
                        pass

                # --- TLS states (incremental + full every 20) ---
                tls_data = {}
                tids = traci.trafficlight.getIDList()
                full_dump = (fc % 20 == 0)
                for tid in tids:
                    try:
                        st = traci.trafficlight.getRedYellowGreenState(tid)
                        if full_dump or last_tls_states.get(tid) != st:
                            tls_data[tid] = st
                        last_tls_states[tid] = st
                    except traci.exceptions.TraCIException:
                        pass

                # --- Stats ---
                n = len(vehicles)
                spds = [v[4] for v in vehicles]
                mean_speed = sum(spds) / max(n, 1)
                halting = sum(1 for s in spds if s < 0.1)

                frame = {
                    "t": round(t, 1),
                    "v": vehicles,
                    "tls": tls_data,
                    "stats": {
                        "n": n,
                        "meanSpeed": round(mean_speed, 1),
                        "halting": halting,
                        "control": ctrl,
                    },
                }

                state.set_frame(frame)
                data_str = f"data: {json.dumps(frame, separators=(',', ':'))}\n\n"
                state.broadcast(data_str)

                # ---- Playback rate wall-clock pacing ----
                elapsed = time.time() - wall_start
                target = 0.5 / ctrl['playbackRate']
                sleep_time = target - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            try:
                traci.close()
            except Exception:
                pass

        time.sleep(1)  # brief pause between restarts

    print("[sim] Loop ended.", flush=True)


# ============================================================
#  Main
# ============================================================

def main():
    # Parse args
    parser = argparse.ArgumentParser(description='SUMO/TraCI HTTP server with 3D visualization')
    parser.add_argument('--port', type=int, default=8765, help='Port to listen on (default 8765)')
    args = parser.parse_args()
    
    os.makedirs(WEB_DIR, exist_ok=True)
    
    # Load scene data for congestion endpoints
    scene_path = os.path.join(WEB_DIR, 'scene.json')
    try:
        with open(scene_path, 'r') as f:
            scene = json.load(f)
            scene_data['roads'] = scene.get('roads', [])
            scene_data['bounds'] = scene.get('bounds')
            # net_offset is not in scene.json; use a default (from export_scene.py context)
            # For congestion model, we need the reverse offset
            scene_data['net_offset'] = (-704066.21, -5657892.69)
    except Exception as e:
        print(f"[server] Warning: could not load scene.json: {e}", flush=True)

    # Start simulation thread
    t = threading.Thread(target=sim_loop, daemon=True, name="sumo-sim")
    t.start()

    # Wait for first frame
    print(f"[server] Waiting for simulation...", flush=True)
    for _ in range(100):
        if state.get_frame() is not None:
            break
        time.sleep(0.1)
    if state.get_frame() is None:
        print("[server] WARNING: no frame after 10s, starting server anyway", flush=True)

    # HTTP server
    addr = ('0.0.0.0', args.port)
    http.server.ThreadingHTTPServer.allow_reuse_address = False  # Windows: avoid silent dual-binding
    httpd = http.server.ThreadingHTTPServer(addr, RequestHandler)
    print(f"[server] Listening on http://localhost:{args.port}", flush=True)

    def _shutdown(sig, frame):
        print("\n[server] Shutting down...", flush=True)
        state.running = False
        httpd.shutdown()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.running = False
        httpd.server_close()
        print("[server] Stopped.", flush=True)


if __name__ == '__main__':
    main()