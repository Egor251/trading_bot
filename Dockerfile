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

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "tools/healthcheck.py", "--pid", "1", "--process-pattern", "host/main.py"]

CMD ["python", "host/main.py", "--headless"]
