# Shared Render PoC modes

Start command remains `python -m uvicorn server.main:app --host 0.0.0.0 --port $PORT --workers 1`.

Common settings:

```dotenv
POC_ENABLED=true
POC_SECURE_COOKIES=true
POC_ALLOWED_ORIGINS=https://YOUR-POC.vercel.app
POC_ADMIN_TOKEN=YOUR_32_OR_MORE_CHARACTER_SECRET
POC_DB_PATH=/var/data/aimi-poc/poc.sqlite3
POC_MEDIA_PATH=/var/data/aimi-poc/images
```

The paths above require an existing persistent disk mounted at `/var/data`.
For rehearsal set `POC_TEST_MODE=true` (TEST participant IDs). For real collection set
`POC_TEST_MODE=false` and explicitly approve the selected target with `POC_TARGET_APPROVED=true`.

## Gemini only

```dotenv
POC_GPU_MODE=gemini
```

Uses `POC_GEMINI_API_KEY` if defined, otherwise existing `GEMINI_API_KEY`.
No RunPod URL, GPU key, GPU startup, or runtime registration is required.
Invalid legacy PoC GPU settings are ignored in this mode. The RunPod profile is unavailable.
Optional model overrides: `POC_GEMINI_IMAGE_MODEL`, `POC_GEMINI_ROUTER_MODEL`.

## RunPod with optional Gemini selection

```dotenv
POC_GPU_MODE=runpod
POC_GPU_API_URL=https://YOUR-POD-8000.proxy.runpod.net
POC_GPU_API_KEY=EXACT_KEY_FROM_POC_WORKER
```

GPU key must be at least 32 characters. Use the exact worker key, not the SSH public key,
SSH fingerprint, or an independently generated replacement. Gemini remains selectable when
its key is configured. Participant profile is locked after starting.

## Check

- `/health`: `poc_enabled: true`.
- `/poc/health`: `mode: gemini` or `mode: runpod`.
- `/poc/api/poc/config`: appropriate profile availability.

Frontend Vercel rewrites must proxy `/api/*`, `/content/*`, and `/health` to the Render
`/poc` prefix. Existing campaign model and origin settings stay unchanged.
