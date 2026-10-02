FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/trading_bot:/opt/Code_base:/opt/Code_base/microkernel_core:/opt/Code_base/plugin_manager

WORKDIR /opt/trading_bot

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

COPY . .
RUN addgroup --system trading && \
    adduser --system --ingroup trading --home /opt/trading_bot trading && \
    chown -R trading:trading /opt/trading_bot

# The application architecture uses a sibling Code_base tree. Mount its
# production build read-only at /opt/Code_base when starting this image.
VOLUME ["/opt/Code_base"]

USER trading

# Probe the *engine*, not the process table. `--state-file` is written by the
# ops monitor heartbeat (engine/health_state.py) and covers runtime presence,
# feed staleness and halt state; the previous command only verified that PID 1
# was still `host/main.py`, so a halted book or a dead feed stayed "healthy".
# `--max-age` is ~4x the 10 s heartbeat so a single slow cycle is not a failure.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --reties=3 \
    CMD ["python", "tools/healthcheck.py", \
         "--state-file", "data/health.json", \
         "--max-age", "45", \
         "--pid", "1", "--process-pattern", "host/main.py"]

CMD ["python", "host/main.py", "--headless"]
