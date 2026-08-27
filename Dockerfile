FROM python:3.12-slim

WORKDIR /app

# requirements-postgres.txt is included (not just requirements.txt) so this
# image can talk to a production PostgreSQL deployment (DRONE_DATABASE_URL)
# out of the box, without needing a custom image just to add psycopg2.
COPY requirements.txt requirements-postgres.txt .
RUN pip install --no-cache-dir -r requirements-postgres.txt

COPY . .

# 127.0.0.1 (the app's own default) isn't reachable from outside the
# container, so bind to all interfaces here. Set DRONE_API_KEY when running
# this image anywhere it's reachable beyond your own machine.
ENV DRONE_HOST=0.0.0.0
ENV DRONE_PORT=8000

EXPOSE 8000

VOLUME ["/app/data"]

# Runs as an unprivileged user rather than the container default root --
# this process only ever needs to bind a port above 1024 and write to
# /app/data, neither of which needs root.
RUN useradd --create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app
USER appuser

# Checks the same readiness endpoint a load balancer/orchestrator would --
# reports unhealthy (and lets Docker/Kubernetes restart or route around
# this container) if the database becomes unreachable, not just if the
# process has crashed outright.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
