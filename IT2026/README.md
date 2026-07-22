## Quick Start

### One-script startup
```powershell
# from repo root
.\start_platform.ps1
```

### Manual startup (fallback)
```bash
pip install -r requirements.txt
python assets_api.py
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173
API docs: http://localhost:8080/docs

### Agent
Use the GPO deployment package or `start_agent_admin.ps1` for the endpoint agent.
The endpoint Agent uses `9000` for remote desktop WebSocket traffic and `9001` for authenticated command/report control traffic. Override the latter with `ZVIEW_AGENT_CONTROL_PORT` when required.

