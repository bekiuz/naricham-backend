# Nari Backend

Nari Backend is a small FastAPI service that acts as a secure middle layer between Nari clients and the official Manus API v2. The backend accepts a client message at `POST /api/chat`, keeps the Manus API key on the server side, and returns the safe task envelope received from Manus.

## Architecture

```text
nari-backend/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── manus.py
│   ├── schemas.py
│   └── routes/
│       ├── __init__.py
│       ├── health.py
│       └── chat.py
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

`app/config.py` loads configuration from process environment variables using Pydantic Settings. It does not contain or print credentials. `app/manus.py` is the only module that knows the Manus API v2 URL, authentication header, task endpoints, request payload, response envelope and error normalization. `app/routes/chat.py` exposes the client-facing endpoint, while `app/routes/health.py` exposes a local health check. `app/main.py` configures CORS, the shared async HTTP client lifecycle and the generic server-error response.

## Official Manus API v2 contract used

The integration follows the official Manus API v2 documentation. It uses `https://api.manus.ai` as the default base URL, sends the API key in the `x-manus-api-key` header, and uses JSON requests. If `MANUS_TASK_ID` is set, the backend calls `POST /v2/task.sendMessage` with a `task_id` and a message containing a text content item. If `MANUS_TASK_ID` is not set, it calls `POST /v2/task.create` and optionally includes `project_id` when `MANUS_PROJECT_ID` is configured.

The success response is validated against the documented `ok`, `request_id`, and `task_id` fields, with optional task metadata such as `task_title`, `task_url`, `share_url`, and `share_visibility`. The backend never forwards the API key to the client and does not include it in logs or exception messages.

## Configuration

Copy `.env.example` to `.env` for local development, or provide the value through the process environment or a deployment secret store. The application reads environment configuration through Pydantic Settings; process environment variables take precedence over the local `.env` file. The real key must never be placed in source code, `.env.example`, Git, client-side JavaScript, or API responses.

| Variable | Required | Description |
|---|---:|---|
| `MANUS_API_KEY` | Yes for chat | Manus API key. Kept server-side and sent only as `x-manus-api-key`. |
| `MANUS_TASK_ID` | No | Existing Manus task or the documented `agent-default-main_task` shortcut. When set, chat uses `task.sendMessage`. |
| `MANUS_PROJECT_ID` | No | Project ID passed to `task.create` when no task ID is configured. |
| `MANUS_API_BASE_URL` | No | Defaults to `https://api.manus.ai`; useful only for a controlled compatible proxy. |
| `MANUS_API_TIMEOUT_SECONDS` | No | Per-request HTTP timeout from 0 to 300 seconds; defaults to 30. |
| `MANUS_RESPONSE_TIMEOUT_SECONDS` | No | Maximum time to poll for the assistant reply from 0 to 300 seconds; defaults to 60. |
| `MANUS_POLL_INTERVAL_SECONDS` | No | Task event polling interval from 0 to 5 seconds; defaults to 0.75. |
| `CORS_ORIGINS` | No | Comma-separated React origins; defaults to `http://localhost:3000,http://localhost:5173`. |

When both `MANUS_TASK_ID` and `MANUS_PROJECT_ID` are present, the existing task takes precedence and the project ID is not sent. When neither is present, a new Manus task is created without a project association.

## Endpoints

### `GET /health`

Returns a local health result without calling Manus:

```json
{
  "status": "ok",
  "manus_api_configured": true
}
```

The boolean only indicates whether a non-empty API key is configured; the key itself is never returned.

### `POST /api/chat`

Request:

```json
{
  "message": "Explain this project architecture."
}
```

The backend sends the message with `task.create` or `task.sendMessage`, then polls the official `task.listMessages` endpoint until it finds the assistant message following the submitted user message. Response on a successful request:

```json
{
  "ok": true,
  "request_id": "request-id-from-manus",
  "task_id": "task-id-from-manus",
  "task_title": "Optional title",
  "task_url": "https://manus.im/app/task-id",
  "share_url": null,
  "share_visibility": "private",
  "response": "Hello from the Manus agent."
}
```

The API returns `422` for invalid request validation, `503` when `MANUS_API_KEY` is absent, `504` for Manus request or response-polling timeouts, `502` for Manus transport/API/invalid-response failures, and `500` for unexpected server errors. Error responses contain stable public error codes and do not contain secrets.

## Railway deployment

The repository includes `railway.toml` and `runtime.txt` for Railway. Railpack detects `requirements.txt`, installs Python 3.12.3, and starts the existing FastAPI entry point with `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`. Railway injects `PORT`; the command uses it and falls back to `8000` only for local execution. The deployment healthcheck calls `GET /health` and waits for a `200` response before activating the deployment.

In the Railway service **Variables** panel, set `MANUS_API_KEY` as a secret value. Add either `MANUS_TASK_ID` or `MANUS_PROJECT_ID` according to the desired chat routing mode, and set `CORS_ORIGINS` to the exact deployed frontend origins, separated by commas. Do not upload, commit, import, or paste the local `.env` file into the repository. `.gitignore` and `.railwayignore` both exclude it.

To deploy from an existing GitHub repository, create a Railway project, select **Deploy from GitHub repo**, choose the separate `nari-backend` repository, review the service variables, and generate a public domain after the deployment is healthy. Alternatively, after linking the Railway CLI to the service, run `railway up` from this backend directory. Deployment itself is intentionally not performed by this project setup.

## Windows setup

Open PowerShell in the project directory and create a virtual environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

For local development, copy the template and edit the ignored file manually:

```powershell
Copy-Item .env.example .env
notepad .env
```

Place your real `MANUS_API_KEY` only in that local `.env` file, or set it for the current PowerShell session instead:

```powershell
$env:MANUS_API_KEY = "paste-your-real-manus-api-key-here"
$env:MANUS_TASK_ID = "agent-default-main_task"
$env:CORS_ORIGINS = "http://localhost:3000,http://localhost:5173"
```

Start the development server:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Check health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Send a chat request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/chat `
  -ContentType "application/json" `
  -Body '{"message":"Hello from Nari"}'
```

For a persistent Windows installation, configure the environment variables in the service manager or deployment secret store rather than committing them to a file. If PowerShell execution policy blocks virtual-environment activation, run the commands through `\.venv\Scripts\python.exe` directly or use the approved local policy for your machine.

## Local validation

From the project directory, run:

```powershell
python -m compileall app
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The server can start without an API key so that `/health` remains usable. Calling `/api/chat` without `MANUS_API_KEY` returns a controlled `503` response instead of attempting an unauthenticated request.

## Security notes

The service is intentionally server-side: clients never receive the Manus API key. CORS is restricted to explicit origins and does not enable credentialed wildcard access. The HTTP client uses an explicit timeout, the API response is validated before it is returned, transport failures are normalized, and unexpected errors return a generic message. Do not log request headers, environment values, raw exception representations that may contain headers, or full Manus payloads in production.

This project does not commit or push changes automatically. Review the files and run your own secret scanning before publishing the repository.

## References

The implementation follows the official [Manus API v2 authentication documentation](https://open.manus.ai/docs/v2/authentication), [task.create documentation](https://open.manus.ai/docs/v2/task.create), [task.sendMessage documentation](https://open.manus.ai/docs/v2/task.sendMessage), and [agent shortcut documentation](https://open.manus.ai/docs/v2/agents-overview).
