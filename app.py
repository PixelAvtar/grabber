"""
YT -> MP4 Downloader (personal use)
------------------------------------
A private tool for downloading videos you have the right to download
(your own uploads, content you own/license, or anything else you're
personally authorized to grab). Not built or intended for public/shared use.

Run locally:
    python app.py
Then open http://localhost:5050

Deploy (Render/Replit etc.): set APP_USERNAME and APP_PASSWORD env vars
so the tool isn't open to randoms who find the URL.
"""

import os
import re
import time
import uuid
import shutil
import tempfile
import threading
from functools import wraps

from flask import Flask, render_template, request, jsonify, send_file, Response
import yt_dlp

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# Render (and most cloud hosts) mount "Secret Files" at /etc/secrets/<filename>,
# but that mount is read-only — yt-dlp needs to WRITE to the cookie file to
# refresh session tokens as it works. So we copy it to a writable temp path
# once at startup and point yt-dlp there instead.
COOKIES_SOURCE = os.environ.get("COOKIES_SOURCE", "/etc/secrets/cookies.txt")
COOKIES_PATH = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
if os.path.exists(COOKIES_SOURCE):
    try:
        shutil.copy(COOKIES_SOURCE, COOKIES_PATH)
    except OSError:
        pass

# job_id -> state dict
JOBS = {}
JOBS_LOCK = threading.Lock()

# How long a finished file sticks around before auto-cleanup (seconds)
FILE_TTL = 60 * 30  # 30 minutes


# ---------------------------------------------------------------- auth ----
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not APP_PASSWORD:
            # No password configured -> assume local/dev use, skip auth
            return f(*args, **kwargs)
        auth = request.authorization
        if not auth or auth.username != APP_USERNAME or auth.password != APP_PASSWORD:
            return Response(
                "Login required", 401,
                {"WWW-Authenticate": 'Basic realm="YT Downloader"'},
            )
        return f(*args, **kwargs)
    return decorated


# ------------------------------------------------------------- helpers ----
def friendly_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "private video" in low:
        return "That video is private — you need to be logged in as the owner to grab it."
    if "sign in to confirm your age" in low or "age" in low and "restrict" in low:
        return "Age-restricted video — YouTube is blocking this without a logged-in session."
    if "video unavailable" in low:
        return "That video isn't available (deleted, region-locked, or never existed)."
    if "members-only" in low or "join this channel" in low:
        return "Members-only content — can't be fetched without a subscriber session."
    if "live event will begin" in low or "premieres in" in low:
        return "That's a scheduled premiere/live event that hasn't started yet."
    if "this live stream" in low or "live" in low and "stream" in low:
        return "Live streams can't be downloaded until they finish and YouTube finishes processing the VOD."
    if "unsupported url" in low:
        return "That doesn't look like a URL this tool can read."
    if "http error 403" in low or "forbidden" in low:
