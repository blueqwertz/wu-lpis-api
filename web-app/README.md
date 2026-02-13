# web-app

A modular FastAPI web app that supports:

- Creating signup jobs that run in the background.
- Rejoining job progress at any time by job id.
- Streaming job logs to clients using Server-Sent Events (SSE).

## Branch

This implementation is prepared on the `web-app` branch.

## Run locally

```bash
cd web-app/backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open: http://127.0.0.1:8000

## Run with Docker

```bash
cd web-app/backend
docker build -t wu-lpis-web-app .
docker run --rm -p 8000:8000 wu-lpis-web-app
```

Open: http://127.0.0.1:8000

## API

- `POST /api/jobs` create signup job
- `GET /api/jobs/{job_id}` get status/result
- `GET /api/jobs/{job_id}/logs` get full log history
- `GET /api/jobs/{job_id}/logs/stream` stream live logs (SSE)

## Notes

The current implementation uses a pluggable `SignupProvider` abstraction with a `MockSignupProvider` by default.
You can swap in a real LPIS adapter provider that calls `WuLpisApi` methods.
