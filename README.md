# Lecture → Telegram (VPS)

Use only for lectures you have permission to download and forward.

## Setup (Ubuntu VPS)

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg screen
unzip lecture_to_telegram.zip -d ~
cd ~/lecture_to_telegram
cp .env.example .env
nano .env
```

Set `API_KEY`, `TG_API_ID`, `TG_API_HASH`, `TG_BOT_TOKEN`, `TG_CHAT_ID` in `.env`. Get Telegram API ID/hash from `my.telegram.org`; make a bot with BotFather. Start the bot and send `/start` to find your own chat ID. The bot must already be able to message the destination chat.

Run:

```bash
screen -dmS lecture-transfer bash -lc 'cd ~/lecture_to_telegram && ./start.sh >> server.log 2>&1'
tail -f ~/lecture_to_telegram/server.log
curl http://127.0.0.1:8094/health
```

Expose `127.0.0.1:8094` to **HTTPS** with your existing reverse proxy/tunnel; don't expose the bare port on the public internet. Secure the public endpoint with TLS and the API key. Use `https://YOUR-HOST/health` to check it is reachable. The service accepts a signed `/m` URL on `video.badassworks.xyz`, gets the manifest, and downloads `/p/<even>` and `/b/<odd>` based on the observed player URL pattern. It validates files with an MP4 `ftyp` header before combining them. URL signing may expire, and exact mapping or concatenation compatibility can change. A response of 200 to the first four resources doesn't guarantee that every job will work.

Install `lecture_to_telegram.user.js` in Tampermonkey (Chrome or Edge) and enable it for the Mini App embedded iframe. Play the lecture briefly so the player has requested its media URL. Click the floating **Send Lecture to Telegram** button. On first use, enter your **HTTPS** API base URL and the `API_KEY` from `.env`. The userscript saves these locally in Tampermonkey storage, so use a browser profile you trust.

Once the server says **job accepted**, the browser can be closed. The VPS handles downloading, merging and uploading independently. The VPS and bot must remain active. Keep an eye on free disk space (input parts plus final output) and Telegram file size limits. Telethon upload limits depend on the account/bot and Telegram's current limits; if rejected, reduce file size or use another permitted upload route. Media is deleted from the VPS once the job completes or fails. The server keeps only in-memory job statuses until restart; one job runs at a time.

## Check job status

```bash
curl -H 'X-API-Key: YOUR_KEY' https://YOUR-HOST/jobs/YOUR_JOB_ID
```

## Operational notes

- The service must not be used to circumvent DRM, access restrictions, or distribution rights.
- Don't share signed video links, Telegram session init data or API keys.
- Make sure `ffmpeg` handles all segments with matching streams. Some MP4 segments cannot be safely concatenated with `-c copy`; verify duration, audio and transitions of the final video.
- Don't change the source hostname or turn off URL validation for untrusted links: arbitrary URLs are an SSRF risk.
- When changing chat IDs or bot credentials, stop and restart the service.
