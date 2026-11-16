"""Authorized lecture-to-Telegram transfer service.
Only accepts URLs on the explicitly configured video host.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from telethon import TelegramClient, events

load_dotenv()
API_KEY = os.environ.get('API_KEY', '')
VIDEO_HOST = os.environ.get('VIDEO_HOST', 'video.badassworks.xyz').lower()
TG_API_ID = int(os.environ.get('TG_API_ID', '0'))
TG_API_HASH = os.environ.get('TG_API_HASH', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = int(os.environ.get('TG_CHAT_ID', '0'))
PORT = int(os.environ.get('PORT', '8094'))
MAX_PARTS = int(os.environ.get('MAX_PARTS', '300'))
MAX_TOTAL_GB = float(os.environ.get('MAX_TOTAL_GB', '3.5'))
DATA_DIR = Path(os.environ.get('DATA_DIR', './jobs')).resolve()
LOG = logging.getLogger('lecturebot')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
if not all((API_KEY, TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, TG_CHAT_ID)):
    raise RuntimeError('Set API_KEY, TG_API_ID, TG_API_HASH, TG_BOT_TOKEN, TG_CHAT_ID in .env')
DATA_DIR.mkdir(parents=True, exist_ok=True)
client = TelegramClient(str(DATA_DIR / 'bot_session'), TG_API_ID, TG_API_HASH)
queue: asyncio.Queue = asyncio.Queue()
records = {}
worker_task = None

@client.on(events.NewMessage(pattern=r'^/start(?:\s|$)'))
async def start_message(event):
    await event.respond('Lecture receiver online. Your chat ID: ' + str(event.chat_id) + '\nSet TG_CHAT_ID to this value if you want deliveries here.')

class JobRequest(BaseModel):
    manifest_url: str = Field(min_length=20, max_length=2048)
    title: str = Field(default='Lecture', max_length=120)

def normalize_manifest(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != 'https' or url.hostname != VIDEO_HOST or url.port not in (None, 443):
        raise ValueError('Only HTTPS URLs on configured video host are accepted')
    if url.username or url.password or url.path not in ('/m',) or url.fragment:
        raise ValueError('Expected exact manifest path /m')
    params = parse_qs(url.query, keep_blank_values=False)
    if not all(params.get(k) for k in ('l', 'e', 's')):
        raise ValueError('Missing signed URL parameters (l, e, s)')
    if any(len(v) != 1 for v in params.values()):
        raise ValueError('Repeated query parameter')
    return urlunsplit(('https', VIDEO_HOST, '/m', url.query, ''))

def status(job, phase, detail=''):
    job['phase'] = phase
    job['detail'] = detail
    job['updated'] = datetime.now(timezone.utc).isoformat()

async def send_progress(msg, message, last_edit):
    if time.monotonic() - last_edit[0] < 5:
        return
    try:
        await msg.edit(message[:4000])
        last_edit[0] = time.monotonic()
    except Exception as ex:
        LOG.warning('Progress edit skipped: %s', type(ex).__name__)

async def download_file(session, url, path, total_counter, job):
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=25, sock_read=120)
    for attempt in range(1, 4):
        try:
            async with session.get(url, timeout=timeout, allow_redirects=False) as response:
                if response.status != 200:
                    raise RuntimeError(f'HTTP {response.status}')
                size = 0
                tmp = path.with_suffix('.partial')
                with tmp.open('wb') as output:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        size += len(chunk)
                        total_counter[0] += len(chunk)
                        if total_counter[0] > MAX_TOTAL_GB * (1024**3):
                            raise RuntimeError('Maximum job download size exceeded')
                        output.write(chunk)
                with tmp.open('rb') as f:
                    header = f.read(12)
                if size < 12 or header[4:8] != b'ftyp':
                    raise RuntimeError('Resource is not a standalone MP4 file')
                tmp.replace(path)
                return size
        except Exception:
            if attempt == 3:
                raise
            # Reset counter for any partial bytes that will be re-downloaded.
            tmp = path.with_suffix('.partial')
            if tmp.exists():
                total_counter[0] -= tmp.stat().st_size
                tmp.unlink()
            await asyncio.sleep(attempt * 2)

async def process(job):
    root = DATA_DIR / job['id']
    root.mkdir(parents=True, exist_ok=True)
    msg = None
    last_edit = [0.0]
    try:
        msg = await client.send_message(TG_CHAT_ID, f"📥 Queued: {job['title']}\nJob: {job['id']}")
        status(job, 'manifest')
        source = urlsplit(job['manifest_url'])
        async with aiohttp.ClientSession(headers={'User-Agent': 'LectureTransfer/1.0'}) as session:
            async with session.get(job['manifest_url'], allow_redirects=False, timeout=aiohttp.ClientTimeout(total=45)) as response:
                if response.status != 200:
                    raise RuntimeError(f'Manifest HTTP {response.status}; URL may have expired')
                manifest = await response.json(content_type=None)
            parts = manifest.get('parts')
            if not isinstance(parts, list) or not (1 <= len(parts) <= MAX_PARTS):
                raise RuntimeError('Invalid or oversized parts list')
            # This endpoint mapping was observed for this particular player; verify for each new version.
            query = source.query
            total_counter = [0]
            job['total_parts'] = len(parts)
            status(job, 'downloading')
            for i in range(len(parts)):
                segment_endpoint = f"/p/{i}" if i % 2 == 0 else f"/b/{i}"
                url = urlunsplit(('https', VIDEO_HOST, segment_endpoint, query, ''))
                filename = root / f'part_{i:03}.mp4'
                await download_file(session, url, filename, total_counter, job)
                job['done_parts'] = i + 1
                job['download_mb'] = round(total_counter[0] / (1024**2), 2)
                await send_progress(msg, f"📥 {job['title']}\nDownloaded {i+1}/{len(parts)} parts ({job['download_mb']} MB)", last_edit)
        status(job, 'merging')
        await msg.edit(f"🔧 Merging {len(parts)} parts: {job['title']}")
        concat = root / 'concat.txt'
        concat.write_text(''.join(f"file 'part_{i:03}.mp4'\n" for i in range(len(parts))), encoding='utf-8')
        output = root / 'lecture_final.mp4'
        proc = await asyncio.create_subprocess_exec('ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(concat), '-c', 'copy', '-movflags', '+faststart', str(output), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode or not output.exists() or output.stat().st_size < 1024:
            raise RuntimeError('FFmpeg merge failed: ' + err.decode(errors='replace')[-900:])
        status(job, 'uploading')
        size_mb = output.stat().st_size / (1024**2)
        await msg.edit(f"📤 Uploading {job['title']} ({size_mb:.1f} MB)")
        upload_state = {'last': 0.0}
        async def progress(current, total):
            job['upload_percent'] = round(current * 100 / total, 1) if total else 0
            if time.monotonic() - upload_state['last'] > 8:
                upload_state['last'] = time.monotonic()
                await send_progress(msg, f"📤 {job['title']}\nUploading: {job['upload_percent']}%", last_edit)
        await client.send_file(TG_CHAT_ID, str(output), caption=f"🎬 {job['title']}", supports_streaming=True, progress_callback=progress, force_document=False)
        status(job, 'complete', f'Sent {size_mb:.1f} MB to Telegram')
        await msg.edit(f"✅ Sent: {job['title']}\n{size_mb:.1f} MB")
    except Exception as e:
        status(job, 'failed', str(e)[:900])
        LOG.exception('Job %s failed', job['id'])
        if msg:
            try:
                await msg.edit(f"❌ {job['title']} failed: {str(e)[:500]}")
            except Exception:
                pass
    finally:
        # Temporary files are removed after successful sending or failure.
        shutil.rmtree(root, ignore_errors=True)

async def worker():
    while True:
        job = await queue.get()
        try:
            await process(job)
        finally:
            queue.task_done()

@asynccontextmanager
async def lifespan(app):
    await client.start(bot_token=TG_BOT_TOKEN)
    global worker_task
    worker_task = asyncio.create_task(worker())
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    await client.disconnect()

app = FastAPI(title='Lecture to Telegram', lifespan=lifespan)

def authenticate(token):
    import hmac
    if not token or not hmac.compare_digest(token, API_KEY):
        raise HTTPException(status_code=401, detail='Invalid API key')

@app.get('/health')
async def health():
    return {'ok': True, 'worker': not worker_task.done() if worker_task else False}

@app.post('/jobs', status_code=202)
async def create_job(body: JobRequest, x_api_key: str | None = Header(default=None)):
    authenticate(x_api_key)
    try:
        manifest_url = normalize_manifest(body.manifest_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if queue.qsize() >= 10:
        raise HTTPException(status_code=429, detail='Queue full')
    jid = uuid.uuid4().hex[:12]
    title = re.sub(r'[\x00-\x1f]', ' ', body.title).strip() or 'Lecture'
    job = {'id': jid, 'title': title, 'manifest_url': manifest_url, 'phase': 'queued', 'done_parts': 0, 'total_parts': 0, 'detail': ''}
    records[jid] = job
    await queue.put(job)
    return {'job_id': jid, 'phase': 'queued', 'queue_length': queue.qsize()}

@app.get('/jobs/{job_id}')
async def job_status(job_id: str, x_api_key: str | None = Header(default=None)):
    authenticate(x_api_key)
    job = records.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Unknown job')
    return {key: val for key, val in job.items() if key != 'manifest_url'}
