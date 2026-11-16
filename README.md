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
screen -dmS mate-api bash -lc 'cd /root/mate && bash start.sh >> server.log 2>&1'
curl http://127.0.0.1:8094/health
curl https://ubuntu-desktop-production-1c3377bc.up.railway.app/health
```

The Telegram sender uses **Pyrogram bot session** (not Telethon). Its session file is `jobs/bot_session_pyrogram.session`, distinct from the older Telethon session. TgCrypto accelerates Pyrogram crypto; upload speed is still dependent on network and Telegram server limits. Do not restart while an upload/merge is running.\n\nThe app binds to `0.0.0.0:8094`. Keep the Railway domain targeting port 8094. Keep secrets in `.env`, never in Git. Values: `API_KEY`, `TG_API_ID`, `TG_API_HASH`, `TG_BOT_TOKEN`, `TG_CHAT_ID`; optional `MAX_TOTAL_GB`, `MAX_PARTS`, `DATA_DIR`.

Install `lecture_to_telegram.user.js` as a Tampermonkey userscript, replacing the old version. Enable user scripts and site access for `foundation.badassworks.xyz`. The userscript works inside the Telegram Web Mini App iframe.

1. Play the lecture so its actual `/m` manifest request appears in performance entries.
2. Press **API Key** once and enter the `API_KEY` value.
3. Press **Upload Lecture**. The browser fetches up to 5 parts concurrently and uploads each in up to 10 MiB chunks.
4. Keep Telegram Web open during transfer. A signed URL may expire after a short time; if that happens, reopen/replay the lecture to obtain a fresh link, then click **Upload Lecture** again to resume the same job.
5. On the last part, the browser requests merge. The server merges parts, uploads to your Telegram chat using Pyrogram + TgCrypto, and reports progress. At this point you can close the Mini App.

Parts upload in parallel (5 workers); existing MP4 files and partial byte offsets are retained across a reconnect. Jobs are stored under `jobs/browser_uploads/`. A job is identified by the lecture ID in Tampermonkey storage. Uploading again resumes it. Keep sufficient free storage for all parts plus merged output. The provider's permissions still apply.

## Browser-upload API (API key required)

- `POST /browser-jobs`: create upload, payload `{"lecture_id":"...", "title":"...", "total_parts":48}`
- `GET /browser-jobs/{job_id}`: get completed part indexes and current partial offsets
- `PUT /browser-jobs/{job_id}/parts/{index}?offset=N`: upload up to 10 MiB of raw bytes (script sends 10 MiB)
- `POST /browser-jobs/{job_id}/parts/{index}/finish`: payload `{"size":12345}`
- `POST /browser-jobs/{job_id}/complete`: queue final FFmpeg merge and Telegram upload

Old `POST /jobs` and `POST /diagnose` remain for compatibility but cannot circumvent provider IP restrictions.

**Limitations:** each part must be a standalone MP4, compatible with FFmpeg concat stream-copy; live signed URLs can expire; rate limits and source rules apply; API only validates basic MP4 headers; if the browser or VPS crashes during final merge/upload, restart-recovery of that phase is not automated. Do not expose the API key, signed URLs, or Telegram WebApp auth data.


## Telegram video metadata and thumbnail

Telegram video messages now receive explicit FFprobe duration, frame width,
frame height, and a generated JPEG preview. The browser-upload sender uses a
synchronous Pyrogram progress callback and records upload percentage/MB/s
in job metadata. A final MP4 with invalid stream information is rejected with
an explanatory error instead of being posted as a broken 00:00 video.

Check a previously merged file before retrying:

```bash
ffprobe -v error -show_entries format=duration:stream=codec_name,codec_type,width,height \
  -of json /root/mate/jobs/browser_uploads/JOB_ID/lecture_final.mp4
```

This improves newly sent Telegram messages; it cannot edit the duration or
thumbnail of a video message that has already been delivered. Re-upload a
verified MP4 to replace the bad presentation, and remove the old message manually
if appropriate. No retranscoding is performed by default; if the source MP4 has
broken playback timestamps, fix/re-encode the media separately.


## Pyrogram upload troubleshooting (November 2026)

For a large MP4 that already passed FFprobe, the Telegram upload uses a
synchronous progress callback and does not send a custom JPEG thumbnail. The
previous approach could stall near 0.1%; the standalone CLI send without a
custom thumbnail had successfully completed. Duration, frame width, and height
are still passed explicitly to Pyrogram, with Telegram generating the preview.

On retries, a valid existing `lecture_final.mp4` is reused, without remuxing
again. Progress is logged every few seconds and stored in job metadata. Do not
restart an active upload. Before retrying a job stuck with `phase=uploading`
after a restart, set its metadata phase to `failed`; only do this when no
active upload for that job remains. The `/complete` endpoint then retries
using the existing parts and merged MP4. First verify the output video's
FFprobe duration and dimensions; if Telegram still shows 00:00 after a new
send, its playback/file content requires further investigation.


## Automatic browser-job upload queue

The browser upload now uses a **single sequential worker** (one Telegram video
upload at a time). Finishing the last part automatically queues the job, so
closing the browser before its final /complete request no longer leaves a
fully uploaded lecture idle. /complete is idempotent and returns the current
job phase (including `queued`).

On API startup the worker scans on-disk `jobs/browser_uploads/*/metadata.json`
and queues complete-but-unfinished jobs, prioritizing interrupted transfers.
The merged `lecture_final.mp4` is reused where available. Failed or
interrupted jobs are retried at most twice automatically; then their source
files are kept and the job remains `failed` for manual inspection/retry.
The API reports queue length, percentage and speed in the job status.

After Telegram confirms send_video, the returned message ID and `complete`
phase are persisted **before** deleting media. Completed jobs do not
re-enqueue on restart. A hard crash between a successful Telegram delivery
and receipt/persistence of its acknowledgment is fundamentally ambiguous;
in that rare window, a recovery retry could send a duplicate video.
Avoid restarting while a video is in flight, and verify Telegram before
manually retrying an uncertain job.

Deployment: let any active upload finish before `git pull` and restarting
the API. Run `venv/bin/python -m py_compile server.py browser_upload.py`
then restart as usual. Follow `server.log` for
`Browser queue: queued`, `starting`, `retrying`, and `finished` entries.

## Download the next lecture while Telegram uploads the previous one

Tampermonkey userscript v3.4.0 now treats the browser transfer and Telegram
upload as independent stages:

1. Play a lecture and click `Upload Lecture`.
2. Once **all parts are saved on the VPS**, the server queues that job and the
   userscript immediately releases the upload button. There is no need to wait
   for FFmpeg or Telegram.
3. Navigate to the next lecture in the same page/browser tab and click
   `Upload Lecture` again. The browser downloads its parts while the
   server's sequential queue is busy with the prior lecture.
4. The new `Queue` button shows saved jobs and their phase/progress (refreshed
   automatically every 20 seconds while the transfer button is idle).

Do not navigate away while the **browser-to-VPS transfer** is still active.
Once its all-parts-saved confirmation appears, navigating away is safe.
The VPS needs enough free disk space to retain multiple lecture videos while
the Telegram worker uploads them. The queue lives on the VPS; the Tampermonkey
queue list is only a convenience display for jobs created by that browser.
