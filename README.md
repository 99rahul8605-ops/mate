# Lecture → Telegram: browser-assisted transfer

This workflow is for media you are permitted to download and forward.

## Why browser upload is used

The source issues IP/network-locked media URLs. URLs created for a browser may receive HTTP 403 when fetched from Railway. The browser fetches the MP4 parts through its own authorized connection, uploads them to the API, and Railway only merges and uploads them to Telegram.

**Keep the Mini App browser page open until ALL parts have been uploaded.** After that the VPS continues FFmpeg merging and Telegram uploading without the browser.

## Deploy on Railway

In the Railway service working directory:

```bash
cd /root/mate
git pull
venv/bin/python -m py_compile server.py browser_upload.py
screen -ls
screen -S mate-api -X quit
screen -dmS mate-api bash -lc 'cd /root/mate && ./start.sh >> server.log 2>&1'
curl http://127.0.0.1:8094/health
curl https://ubuntu-desktop-production-1c3377bc.up.railway.app/health
```

The app binds to `0.0.0.0:8094`. Keep the Railway domain targeting port 8094. Keep secrets in `.env`, never in Git. Values: `API_KEY`, `TG_API_ID`, `TG_API_HASH`, `TG_BOT_TOKEN`, `TG_CHAT_ID`; optional `MAX_TOTAL_GB`, `MAX_PARTS`, `DATA_DIR`.

Install `lecture_to_telegram.user.js` as a Tampermonkey userscript, replacing the old version. Enable user scripts and site access for `foundation.badassworks.xyz`. The userscript works inside the Telegram Web Mini App iframe.

1. Play the lecture so its actual `/m` manifest request appears in performance entries.
2. Press **API Key** once and enter the `API_KEY` value.
3. Press **Upload Lecture**. The browser fetches each part and POSTs it to Railway in 2 MiB chunks.
4. Keep Telegram Web open during transfer. A signed URL may expire after a short time; if that happens, reopen/replay the lecture to obtain a fresh link, then click **Upload Lecture** again to resume the same job.
5. On the last part, the browser requests merge. The server merges parts, uploads to your Telegram chat and reports progress. At this point you can close the Mini App.

Uploads are sequential; existing MP4 files and partial byte offsets are retained across a reconnect. Jobs are stored under `jobs/browser_uploads/`. A job is identified by the lecture ID in Tampermonkey storage. Uploading again resumes it. Keep sufficient free storage for all parts plus merged output. The provider's permissions still apply.

## Browser-upload API (API key required)

- `POST /browser-jobs`: create upload, payload `{"lecture_id":"...", "title":"...", "total_parts":48}`
- `GET /browser-jobs/{job_id}`: get completed part indexes and current partial offsets
- `PUT /browser-jobs/{job_id}/parts/{index}?offset=N`: upload up to 4 MiB of raw bytes (script sends 2 MiB)
- `POST /browser-jobs/{job_id}/parts/{index}/finish`: payload `{"size":12345}`
- `POST /browser-jobs/{job_id}/complete`: queue final FFmpeg merge and Telegram upload

Old `POST /jobs` and `POST /diagnose` remain for compatibility but cannot circumvent provider IP restrictions.

**Limitations:** each part must be a standalone MP4, compatible with FFmpeg concat stream-copy; live signed URLs can expire; rate limits and source rules apply; API only validates basic MP4 headers; if the browser or VPS crashes during final merge/upload, restart-recovery of that phase is not automated. Do not expose the API key, signed URLs, or Telegram WebApp auth data.
