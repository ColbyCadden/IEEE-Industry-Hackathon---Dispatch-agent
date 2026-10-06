---
name: restart-website
description: Restart the City Link dashboard (Streamlit, dispatch/app.py) when localhost won't load, the page is blank or the website "broke". Checks whether it is already running, starts it as an independent process that survives this session, verifies it loads, and can also start the 3D downtown simulation.
---

# Restart the City Link website

The dashboard is `dispatch/app.py`, served by Streamlit on **http://localhost:8501**. The 3D downtown
simulation is a separate server on **http://localhost:8765** (shown inside the dashboard's Analysis tab).
Repo root: `C:\Users\Bennett Balogh\IEEE-Industry-Hackathon---Dispatch-agent`.

Servers started through the browser preview or a background shell are tied to the Claude session and
stop when it goes idle. Always start the dashboard as an independent process (step 3).

## Steps

1. **Check what's running** (PowerShell):
   ```powershell
   foreach ($p in 8501, 8502, 8765) { try { $r = Invoke-WebRequest "http://localhost:$p/" -UseBasicParsing -TimeoutSec 4; "port ${p}: up ($($r.StatusCode))" } catch { "port ${p}: down" } }
   ```
   If 8501 or 8502 is up, the dashboard is running: just reload the page and go to step 4.

2. **If the dashboard is down, rule out a broken file first** (a half-finished git merge has broken it before):
   ```powershell
   cd "C:\Users\Bennett Balogh\IEEE-Industry-Hackathon---Dispatch-agent"; git status --short; Select-String -Path dispatch\*.py -Pattern '^(<<<<<<<|>>>>>>>)' ; python -c "import ast; ast.parse(open('dispatch/app.py', encoding='utf-8').read()); print('syntax ok')"
   ```
   If there are conflict markers or a syntax error, stop and fix those (or tell the user) before restarting.

3. **Start the dashboard as its own process** (a minimized terminal window; keeps running after this session):
   ```powershell
   Start-Process -FilePath "python" -ArgumentList "-m","streamlit","run","dispatch/app.py","--server.port","8501","--server.headless","true" -WorkingDirectory "C:\Users\Bennett Balogh\IEEE-Industry-Hackathon---Dispatch-agent" -WindowStyle Minimized
   Start-Sleep -Seconds 12
   (Invoke-WebRequest "http://localhost:8501/_stcore/health" -UseBasicParsing -TimeoutSec 5).Content
   ```
   `ok` means it's up. If port 8501 is taken by something else, use `8502` in both places.

4. **Verify the page loads**: open http://localhost:8501 and confirm the **City Link** title and the
   "Crews working" card appear (the map can take a few extra seconds). The first load after a restart
   waits for Claude to write the 8 a.m. briefing (5–10 s); after that it's instant.

5. **3D simulation (only if asked, or the Analysis tab says it isn't running):** from the repo root,
   ```powershell
   python -c "from dispatch import sim3d; print(sim3d.running() or sim3d.start() or 'starting')"
   ```
   It needs SUMO installed (`simulation\setup.bat`, about 200 MB, ask before running it). It takes about
   15 s to come up on port 8765. Or click **Start the 3D sim** on the Analysis tab.

6. **Tell the user** the address, and that the minimized terminal window on the taskbar must stay open:
   closing it stops the website. To restart it themselves:
   ```powershell
   cd "C:\Users\Bennett Balogh\IEEE-Industry-Hackathon---Dispatch-agent"
   python -m streamlit run dispatch/app.py
   ```
