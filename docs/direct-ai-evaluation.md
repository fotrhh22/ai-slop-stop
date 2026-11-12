# Direct AI evaluation

Challenge and standalone evaluation now send the target and generated image directly to the selected Gemini/Qwen judge. CLIP cosine measurement, worker waits and threshold rejection are not used, and the web UI no longer displays similarity. Generation and the Try action remain unchanged.

Render: deploy this commit. No environment-variable change is required: legacy CLIP_* settings are ignored by load_settings, and /health reports clip_mode=off. Before deploying this commit, CLIP_MODE=off can disable the previous CLIP path (save and deploy the existing build). CLIP_WORKER_TOKEN, CLIP_TIMEOUT_SECONDS, CLIP_FAILURE_POLICY and CLIP_REJECT_BELOW are no longer needed by the application. Other model/API settings remain unchanged. The local CLIP worker may be stopped.

Legacy worker code/routes remain for compatibility with offline diagnostics; the live evaluation paths never enqueue work there.
