FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY server/requirements.txt ./server/requirements.txt
RUN pip install -r server/requirements.txt

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app server ./server
USER app
EXPOSE 10000
# No ML runtime/model downloads. exec forwards shutdown signals.
CMD ["sh", "-c", "exec python -m uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1"]
