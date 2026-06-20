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
        return "YouTube blocked the request (403). Usually fixed by retrying — try again."
    return f"Download failed: {msg[:200]}"


def pick_format_string(quality: str) -> str:
    """
    quality is either 'best', 'audio', or a height like '1080', '720'.
    Always targets a single mp4 output, merging if needed.
    """
    if quality == "audio":
        return "bestaudio[ext=m4a]/bestaudio/best"
    if quality == "best":
        return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best"
    # specific height
    h = re.sub(r"\D", "", quality) or "1080"
    return (
        f"bestvideo[ext=mp4][height<={h}]+bestaudio[ext=m4a]/"
        f"best[ext=mp4][height<={h}]/"
        f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
    )


def base_ydl_opts(extra=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # speed: pull fragments/chunks in parallel instead of serially
        "concurrent_fragment_downloads": 8,
        "http_chunk_size": 10 * 1024 * 1024,  # 10MB chunks
        # resilience: YouTube throttles/blips constantly, retry hard
        "retries": 15,
        "fragment_retries": 15,
        "retry_sleep_functions": {"http": lambda n: min(4, 0.5 * (2 ** n))},
        "socket_timeout": 20,
        "nocheckcertificate": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if extra:
        opts.update(extra)
    return opts


def cleanup_old_files():
    now = time.time()
    with JOBS_LOCK:
        stale = [jid for jid, j in JOBS.items()
                 if j.get("status") in ("finished", "error")
                 and now - j.get("done_at", now) > FILE_TTL]
        for jid in stale:
            j = JOBS.pop(jid)
            fp = j.get("filepath")
            if fp and os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError:
                    pass


# -------------------------------------------------------------- routes ----
@app.route("/")
@requires_auth
def index():
    return render_template("index.html")


@app.route("/api/info", methods=["POST"])
@requires_auth
def get_info():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Paste a YouTube URL first."}), 400

    try:
        with yt_dlp.YoutubeDL(base_ydl_opts({"skip_download": True})) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify({"error": friendly_error(e)}), 400

    if info.get("is_live"):
        return jsonify({"error": "That's a live stream — wait until it ends to download the VOD."}), 400

    heights = sorted({
        f.get("height") for f in info.get("formats", [])
        if f.get("vcodec") not in (None, "none") and f.get("height")
    }, reverse=True)

    return jsonify({
        "title": info.get("title"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
        "qualities": [str(h) for h in heights[:6]],  # cap the list, top 6
    })


@app.route("/api/download", methods=["POST"])
@requires_auth
def start_download():
    cleanup_old_files()
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    quality = (data.get("quality") or "best").strip()
    if not url:
        return jsonify({"error": "Missing URL."}), 400

    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "starting", "percent": 0, "speed": None, "eta": None}

    thread = threading.Thread(target=run_download, args=(job_id, url, quality), daemon=True)
    thread.start()
    return jsonify({"job_id": job_id})


def run_download(job_id, url, quality):
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    outtmpl = os.path.join(job_dir, "%(title).150B.%(ext)s")

    def hook(d):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                return
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                downloaded = d.get("downloaded_bytes", 0)
                job["percent"] = round(downloaded / total * 100, 1) if total else job.get("percent", 0)
                job["speed"] = d.get("speed")
                job["eta"] = d.get("eta")
                job["status"] = "downloading"
            elif d["status"] == "finished":
                job["status"] = "merging"  # ffmpeg mux/post-process step
                job["percent"] = 99

    ydl_opts = base_ydl_opts({
        "outtmpl": outtmpl,
        "format": pick_format_string(quality),
        "merge_output_format": "mp4" if quality != "audio" else None,
        "postprocessors": (
            [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}]
            if quality != "audio" else
            [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        ),
        "progress_hooks": [hook],
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            # postprocessor may have changed extension (e.g. -> mp4/mp3); find actual file
            if not os.path.exists(filepath):
                base, _ = os.path.splitext(filepath)
                for ext in (".mp4", ".mp3", ".m4a", ".webm"):
                    if os.path.exists(base + ext):
                        filepath = base + ext
                        break
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "finished",
                "percent": 100,
                "filepath": filepath,
                "filename": os.path.basename(filepath),
                "title": info.get("title"),
                "done_at": time.time(),
            })
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "error",
                "error": friendly_error(e),
                "done_at": time.time(),
            })


@app.route("/api/progress/<job_id>")
@requires_auth
def progress(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "Unknown job"}), 404
        return jsonify(job)


@app.route("/api/file/<job_id>")
@requires_auth
def get_file(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or job.get("status") != "finished":
        return jsonify({"error": "File not ready"}), 404
    return send_file(job["filepath"], as_attachment=True, download_name=job["filename"])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\nDownloads (temp) saved under: {DOWNLOAD_DIR}")
    if not APP_PASSWORD:
        print("Running with NO password — fine for localhost, set APP_USERNAME/APP_PASSWORD before deploying.\n")
    app.run(debug=False, host="0.0.0.0", port=port, threaded=True)
