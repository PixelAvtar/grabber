# Grabber — personal YT → MP4 downloader

A private tool. Paste a link, pick a quality, get an MP4. Built for speed
(parallel fragment downloads, chunked HTTP, hard retries) and to fail with
useful messages instead of silent timeouts.

## Run it locally

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:5050 — no password needed for local use.

## Put it on the web (so you can use it from your phone too)

Set two environment variables before deploying anywhere, or it's wide open
to anyone who finds the URL:

```
APP_USERNAME=pick-a-username
APP_PASSWORD=pick-a-strong-password
```

### Option A — Render
1. Push this folder to a GitHub repo.
2. Render dashboard → New → Web Service → connect the repo.
3. Render auto-detects the `Dockerfile`. Add the two env vars above under
   Environment.
4. Deploy. Render gives you a `https://your-app.onrender.com` URL.

### Option B — Replit
1. Create a Python Repl, upload these files.
2. Add `ffmpeg` via the Nix/dependency panel (or Replit's package search).
3. Add `APP_USERNAME` / `APP_PASSWORD` under Secrets.
4. Run, then hit Publish. Free tier links expire after ~30 days of
   inactivity — fine for personal use, just re-publish if it lapses.

## Notes

- Files are temporary: anything finished gets deleted automatically after
  30 minutes (see `FILE_TTL` in `app.py`) so disk doesn't fill up.
- `quality` can be `best`, a height like `1080`/`720`, or `audio` (pulls an
  MP3 instead of a video).
- If YouTube changes something and downloads start failing, the usual fix
  is `pip install -U yt-dlp` — it ships near-daily updates to track
  YouTube's changes.
- This is for content you have the right to download — your own uploads,
  licensed material, anything you're personally authorized to grab. Not
  built for, and shouldn't be turned into, a public download service.
