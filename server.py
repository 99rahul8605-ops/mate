
import asyncio
import hashlib
import hmac
import logging
import os
import re
import shutil
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

API_KEY = os.getenv("API_KEY", "")
VIDEO_HOST = os.getenv(
    "VIDEO_HOST", "video.badassworks.xyz"
).lower()

TG_API_ID = int(os.getenv("TG_API_ID", "0"))
TG_API_HASH = os.getenv("TG_API_HASH", "")
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = int(os.getenv("TG_CHAT_ID", "0"))

MAX_PARTS = int(os.getenv("MAX_PARTS", "300"))
MAX_TOTAL_GB = float(os.getenv("MAX_TOTAL_GB", "3.5"))
DATA_DIR = Path(os.getenv("DATA_DIR", "./jobs")).resolve()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
LOG = logging.getLogger("lecturebot")

if not all((
    API_KEY, TG_API_ID, TG_API_HASH,
    TG_BOT_TOKEN, TG_CHAT_ID
)):
    raise RuntimeError("Missing required .env values")

DATA_DIR.mkdir(parents=True, exist_ok=True)

client = TelegramClient(
    str(DATA_DIR / "bot_session"),
    TG_API_ID,
    TG_API_HASH
)

queue = asyncio.Queue()
records = {}
worker_task = None


class JobRequest(BaseModel):
    manifest_url: str = Field(
        min_length=20, max_length=2048
    )
    title: str = Field(
        default="Lecture", max_length=120
    )


class DiagnoseRequest(BaseModel):
    manifest_url: str = Field(
        min_length=20, max_length=2048
    )


def authenticate(token):
    if not token or not hmac.compare_digest(
        token, API_KEY
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )


def normalize_manifest(value):
    url = urlsplit(value)

    if (
        url.scheme != "https"
        or url.hostname != VIDEO_HOST
        or url.port not in (None, 443)
    ):
        raise ValueError("Invalid video host")

    if (
        url.username
        or url.password
        or url.path != "/m"
        or url.fragment
    ):
        raise ValueError("Expected exact /m URL")

    params = parse_qs(url.query)

    if not all(
        len(params.get(k, [])) == 1
        for k in ("l", "e", "s")
    ):
        raise ValueError("Invalid signed URL parameters")

    try:
        expiry = int(params["e"][0])
    except ValueError:
        raise ValueError("Invalid expiry timestamp")

    if expiry <= time.time():
        raise ValueError("Signed URL has expired")

    return urlunsplit((
        "https", VIDEO_HOST, "/m", url.query, ""
    ))


def url_info(value):
    url = urlsplit(value)
    params = parse_qs(url.query)

    expiry = int(params["e"][0])

    return {
        "lecture_id": params["l"][0],
        "expiry_utc": datetime.fromtimestamp(
            expiry, timezone.utc
        ).isoformat(),
        "seconds_remaining": round(
            expiry - time.time()
        )
    }


def job_status(job, phase, detail=""):
    job["phase"] = phase
    job["detail"] = detail
    job["updated"] = datetime.now(
        timezone.utc
    ).isoformat()


def safe_error(status_code):
    if status_code == 403:
        return (
            "Video server returned HTTP 403. "
            "Possible session/IP authorization restriction, "
            "invalid signature, or expired URL."
        )

    if status_code == 401:
        return "Video server requires authorization"

    if status_code == 404:
        return "Video resource not found"

    return f"Video server HTTP {status_code}"


def make_session():
    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=25,
        sock_read=120
    )

    return aiohttp.ClientSession(
        timeout=timeout,
        headers={
            "User-Agent": "LectureTransfer/1.1",
            "Accept": "application/json, video/mp4, */*"
        }
    )


async def inspect_manifest(session, url):
    info = url_info(url)

    result = {
        **info,
        "checked_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "http_status": None,
        "content_type": None,
        "parts": None,
        "accessible": False
    }

    async with session.get(
        url,
        allow_redirects=False,
        timeout=aiohttp.ClientTimeout(total=45)
    ) as response:

        result["http_status"] = response.status
        result["content_type"] = response.headers.get(
            "Content-Type"
        )

        if response.status != 200:
            result["error"] = safe_error(
                response.status
            )

            LOG.warning(
                "Manifest rejected: lecture=%s "
                "status=%s seconds_remaining=%s",
                info["lecture_id"],
                response.status,
                info["seconds_remaining"]
            )

            return result, None

        try:
            data = await response.json(
                content_type=None
            )
        except Exception:
            result["error"] = (
                "Manifest response is not valid JSON"
            )
            return result, None

        parts = data.get("parts") if isinstance(
            data, dict
        ) else None

        if not isinstance(parts, list):
            result["error"] = "Missing parts array"
            return result, None

        result["parts"] = len(parts)
        result["accessible"] = True

        return result, data


async def send_progress(msg, message, last_edit):
    if time.monotonic() - last_edit[0] < 5:
        return

    try:
        await msg.edit(message[:4000])
        last_edit[0] = time.monotonic()
    except Exception as exc:
        LOG.warning(
            "Progress edit failed: %s",
            type(exc).__name__
        )


def valid_mp4(path):
    if not path.is_file():
        return False

    if path.stat().st_size < 12:
        return False

    with path.open("rb") as f:
        header = f.read(12)

    return header[4:8] == b"ftyp"


async def download_file(
    session, url, path, total_counter
):
    if valid_mp4(path):
        return path.stat().st_size

    tmp = path.with_suffix(".partial")

    for attempt in range(1, 4):
        written = 0

        try:
            if tmp.exists():
                tmp.unlink()

            async with session.get(
                url,
                allow_redirects=False
            ) as response:

                if response.status != 200:
                    raise RuntimeError(
                        safe_error(response.status)
                    )

                with tmp.open("wb") as output:
                    async for chunk in response.content.iter_chunked(
                        1024 * 1024
                    ):
                        written += len(chunk)

                        if (
                            total_counter[0] + written
                            > MAX_TOTAL_GB * 1024**3
                        ):
                            raise RuntimeError(
                                "Maximum download size exceeded"
                            )

                        output.write(chunk)

            if not valid_mp4(tmp):
                raise RuntimeError(
                    "Downloaded resource is not a standalone MP4"
                )

            tmp.replace(path)
            total_counter[0] += written
            return written

        except Exception:
            if tmp.exists():
                tmp.unlink()

            if attempt == 3:
                raise

            await asyncio.sleep(attempt * 2)


async def process(job):
    info = url_info(job["manifest_url"])

    # Keep partial downloads on failure so a later
    # submission for the same lecture can reuse them.
    cache_id = hashlib.sha256(
        info["lecture_id"].encode()
    ).hexdigest()[:20]

    root = DATA_DIR / "cache" / cache_id
    root.mkdir(parents=True, exist_ok=True)

    msg = None
    last_edit = [0.0]
    success = False

    try:
        msg = await client.send_message(
            TG_CHAT_ID,
            f"📥 Queued: {job['title']}\n"
            f"Job: {job['id']}"
        )

        job_status(job, "manifest")

        async with make_session() as session:
            result, manifest = await inspect_manifest(
                session,
                job["manifest_url"]
            )

            if not result["accessible"]:
                raise RuntimeError(
                    result.get(
                        "error",
                        "Manifest unavailable"
                    )
                )

            parts = manifest["parts"]

            if not (
                1 <= len(parts) <= MAX_PARTS
            ):
                raise RuntimeError(
                    "Invalid or oversized parts list"
                )

            source = urlsplit(
                job["manifest_url"]
            )

            query = source.query
            total_counter = [0]

            job["total_parts"] = len(parts)

            # Count already downloaded MP4 files.
            for i in range(len(parts)):
                path = root / f"part_{i:03}.mp4"

                if valid_mp4(path):
                    total_counter[0] += path.stat().st_size

            if total_counter[0] > MAX_TOTAL_GB * 1024**3:
                raise RuntimeError(
                    "Cached download exceeds size limit"
                )

            job_status(job, "downloading")

            for i in range(len(parts)):
                endpoint = (
                    f"/p/{i}"
                    if i % 2 == 0
                    else f"/b/{i}"
                )

                url = urlunsplit((
                    "https",
                    VIDEO_HOST,
                    endpoint,
                    query,
                    ""
                ))

                filename = root / (
                    f"part_{i:03}.mp4"
                )

                await download_file(
                    session,
                    url,
                    filename,
                    total_counter
                )

                job["done_parts"] = i + 1
                job["download_mb"] = round(
                    total_counter[0] / 1024**2, 2
                )

                await send_progress(
                    msg,
                    f"📥 {job['title']}\n"
                    f"Downloaded {i+1}/{len(parts)} parts\n"
                    f"{job['download_mb']} MB",
                    last_edit
                )

        job_status(job, "merging")

        await msg.edit(
            f"🔧 Merging {len(parts)} parts\n"
            f"{job['title']}"
        )

        concat = root / "concat.txt"

        concat.write_text(
            "".join(
                f"file 'part_{i:03}.mp4'\n"
                for i in range(len(parts))
            ),
            encoding="utf-8"
        )

        output = root / "lecture_final.mp4"

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel", "error",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )

        _, stderr = await proc.communicate()

        if (
            proc.returncode != 0
            or not valid_mp4(output)
        ):
            raise RuntimeError(
                "FFmpeg merge failed: "
                + stderr.decode(
                    errors="replace"
                )[-800:]
            )

        job_status(job, "uploading")

        size_mb = output.stat().st_size / 1024**2

        await msg.edit(
            f"📤 Uploading {job['title']}\n"
            f"{size_mb:.1f} MB"
        )

        upload_state = {"last": 0.0}

        async def upload_progress(current, total):
            job["upload_percent"] = (
                round(current * 100 / total, 1)
                if total else 0
            )

            if (
                time.monotonic()
                - upload_state["last"] > 8
            ):
                upload_state["last"] = time.monotonic()

                await send_progress(
                    msg,
                    f"📤 {job['title']}\n"
                    f"Uploading: {job['upload_percent']}%",
                    last_edit
                )

        await client.send_file(
            TG_CHAT_ID,
            str(output),
            caption=f"🎬 {job['title']}",
            supports_streaming=True,
            progress_callback=upload_progress,
            force_document=False
        )

        job_status(
            job,
            "complete",
            f"Sent {size_mb:.1f} MB to Telegram"
        )

        await msg.edit(
            f"✅ Sent: {job['title']}\n"
            f"{size_mb:.1f} MB"
        )

        success = True

    except Exception as exc:
        job_status(
            job,
            "failed",
            str(exc)[:900]
        )

        LOG.exception(
            "Job %s failed", job["id"]
        )

        if msg:
            try:
                await msg.edit(
                    f"❌ {job['title']} failed:\n"
                    f"{str(exc)[:500]}"
                )
            except Exception:
                pass

    finally:
        if success:
            shutil.rmtree(
                root, ignore_errors=True
            )


async def worker():
    while True:
        job = await queue.get()

        try:
            await process(job)
        finally:
            queue.task_done()


@client.on(events.NewMessage(
    pattern=r"^/start(?:\s|$)"
))
async def start_message(event):
    await event.respond(
        "Lecture receiver online.\n"
        f"Your chat ID: {event.chat_id}"
    )


@asynccontextmanager
async def lifespan(app):
    global worker_task

    await client.start(
        bot_token=TG_BOT_TOKEN
    )

    worker_task = asyncio.create_task(
        worker()
    )

    yield

    worker_task.cancel()

    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    await client.disconnect()


app = FastAPI(
    title="Lecture to Telegram",
    lifespan=lifespan
)


@app.get("/health")
async def health():
    return {
        "ok": True,
        "worker": (
            not worker_task.done()
            if worker_task else False
        )
    }


@app.post("/diagnose")
async def diagnose(
    body: DiagnoseRequest,
    x_api_key: str | None = Header(default=None)
):
    authenticate(x_api_key)

    try:
        url = normalize_manifest(
            body.manifest_url
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    async with make_session() as session:
        result, _ = await inspect_manifest(
            session, url
        )

    return result


@app.post("/jobs", status_code=202)
async def create_job(
    body: JobRequest,
    x_api_key: str | None = Header(default=None)
):
    authenticate(x_api_key)

    try:
        manifest_url = normalize_manifest(
            body.manifest_url
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        )

    if queue.qsize() >= 10:
        raise HTTPException(
            status_code=429,
            detail="Queue full"
        )

    jid = uuid.uuid4().hex[:12]

    title = re.sub(
        r"[\x00-\x1f]",
        " ",
        body.title
    ).strip() or "Lecture"

    job = {
        "id": jid,
        "title": title,
        "manifest_url": manifest_url,
        "phase": "queued",
        "done_parts": 0,
        "total_parts": 0,
        "detail": ""
    }

    records[jid] = job

    await queue.put(job)

    return {
        "job_id": jid,
        "phase": "queued",
        "queue_length": queue.qsize()
    }


@app.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    x_api_key: str | None = Header(default=None)
):
    authenticate(x_api_key)

    job = records.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Unknown job"
        )

    return {
        key: value
        for key, value in job.items()
        if key != "manifest_url"
    }
