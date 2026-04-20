# ─────────────────────────────────────────────────────────────────────────────
# Gunicorn production configuration
# Usage: gunicorn -c gunicorn.conf.py app.main:app
# ─────────────────────────────────────────────────────────────────────────────
import multiprocessing
import os

# ── Worker settings ───────────────────────────────────────────────────────────
# Uvicorn worker class enables asyncio (required for FastAPI)
worker_class = "uvicorn.workers.UvicornWorker"

# (2 × CPU cores) + 1 is the standard formula for I/O-bound apps
workers = int(os.getenv("GUNICORN_WORKERS", (2 * multiprocessing.cpu_count()) + 1))

# Max concurrent connections per worker (asyncio handles these well)
worker_connections = 1000

# ── Timeouts ──────────────────────────────────────────────────────────────────
# Give the summarize endpoint enough headroom (AI + Graph can take ~10s)
timeout = int(os.getenv("GUNICORN_TIMEOUT", 60))
graceful_timeout = 30
keepalive = 5

# ── Binding ───────────────────────────────────────────────────────────────────
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# ── Logging ───────────────────────────────────────────────────────────────────
accesslog = "-"   # stdout
errorlog = "-"    # stderr
loglevel = os.getenv("LOG_LEVEL", "info").lower()
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sµs'
)

# ── Process naming ────────────────────────────────────────────────────────────
proc_name = "onedrive-agent-api"
