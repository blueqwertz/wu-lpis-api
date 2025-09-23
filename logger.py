import os
import sys
import atexit
import requests
from loguru import logger

# ─── CONFIG (env overrides recommended) ────────────────────────────────────────
LOGS_URL = "https://logs-prod-039.grafana.net/loki/api/v1/push"
LOGS_USERID = "1283091"
LOGS_API_KEY = "glc_eyJvIjoiMTQ4OTkxNCIsIm4iOiJzdGFjay0xMzI1Mjk3LWhsLXdyaXRlLWxwaXMtYXBpLWxvZ3MiLCJrIjoiN0E2MjdlZWlpTTcwbThpWTIwZFAxcEROIiwibSI6eyJyIjoicHJvZC1ldS1jZW50cmFsLTAifX0="
JOB_NAME = "wu-lpis-api"
USER_NAME = "unknown"

def set_user_name(name: str):
    global USER_NAME
    USER_NAME = name

# ─── BUFFER FOR LOGS (timestamp, message) ────────────────────────────────────
_logs_buffer = []  # each item: (ts_ns_str, message_str)


def _log_sink(message):
    """Collect each log with its original timestamp (ns) and raw message."""
    rec = message.record  # dict with loguru metadata
    # rec["time"] is a timezone-aware datetime; convert to nanoseconds epoch
    ts_ns = str(int(rec["time"].timestamp() * 1_000_000_000))
    # Use the unformatted message text to avoid duplicates of time/level
    text = rec["message"]
    _logs_buffer.append((ts_ns, text))


def _flush_logs():
    """Send buffered logs to Loki/Grafana with their original timestamps."""
    # if input("send logs to server? [Y/n] ").lower() == "n":
    #     return
    # logger.opt(colors=True).info("<italic>sending logs to server...</italic>")
    if not _logs_buffer:
        return

    # If credentials or URL are missing, don't attempt to send
    if not (LOGS_URL and LOGS_USERID and LOGS_API_KEY):
        return
    
    # generate random trace id
    trace_id = os.urandom(8).hex()

    payload = {
        "streams": [
            {
                "stream": {"job": JOB_NAME},
                "values": [[ts, msg, {"user": USER_NAME, "trace": trace_id}] for ts, msg in _logs_buffer],
            }
        ]
    }

    try:
        resp = requests.post(
            url=LOGS_URL,
            auth=(LOGS_USERID, LOGS_API_KEY),
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        # If remote push fails, write a single-line error; never raise within atexit
        sys.stderr.write(f"Failed to push logs to Loki: {e}\n")
    finally:
        # Clear buffer so repeated atexit calls (if any) don't resend
        _logs_buffer.clear()


# Register exit hook
atexit.register(_flush_logs)

# ─── LOGGER SETUP ────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

logger.remove()
logger.add(sys.stdout, format="{message}", level="INFO", colorize=True)
logger.add(_log_sink, level="TRACE")