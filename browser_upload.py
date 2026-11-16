"""Browser-assisted upload for lecture media the user is authorized to transfer.

The browser reads the media on its own network and uploads bytes in resumable
chunks. This service never fetches provider video URLs.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from video_metadata import video_info, create_thumbnail

LOG = logging.getLogger("lecturebot.browser")
CHUNK_LIMIT = 10 * 1024 * 1024
MAX_PART_SIZE = 1024 * 1024 * 1024


class BrowserJobRequest(BaseModel):
    title: str = Field(default="Lecture", max_length=120)
    lecture_id: str = Field(min_length=1, max_length=100)
    total_parts: int = Field(ge=1)


def setup_browser_upload(app, client, api_key, chat_id, data_dir,
                         max_parts, max_total_gb):
    router = APIRouter(prefix="/browser-jobs")
    root = Path(data_dir) / "browser_uploads"
    root.mkdir(parents=True, exist_ok=True)
    locks = {}
    active = {}
    pending = set()
    job_queue = asyncio.Queue()
    queue_worker = None
    max_auto_retries = 2
    max_bytes = int(max_total_gb * 1024**3)

    def auth(key):
        import hmac
        if not key or not hmac.compare_digest(key, api_key):
            raise HTTPException(status_code=401, detail="Invalid API key")

    def directory(job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise HTTPException(status_code=404, detail="Unknown job")
        path = root / job_id
        if not (path / "metadata.json").is_file():
            raise HTTPException(status_code=404, detail="Unknown job")
        return path

    def load(job_id):
        path = directory(job_id)
        return path, json.loads((path / "metadata.json").read_text("utf-8"))

    def save(path, record):
        record["updated"] = datetime.now(timezone.utc).isoformat()
        tmp = path / "metadata.tmp"
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path / "metadata.json")

    def guard(record):
        if record["phase"] not in ("receiving", "failed"):
            raise HTTPException(status_code=409, detail="Upload is not accepting parts")

    def part_path(path, index, count):
        if not (0 <= index < count):
            raise HTTPException(status_code=400, detail="Invalid part index")
        return path / f"part_{index:03}.mp4"

    def part_ok(file):
        if not file.is_file() or file.stat().st_size < 12:
            return False
        with file.open("rb") as stream:
            header = stream.read(12)
        return header[4:8] == b"ftyp"

    def part_state(path, record):
        complete = []
        offsets = {}
        for i in range(record["total_parts"]):
            f = part_path(path, i, record["total_parts"])
            partial = f.with_suffix(".partial")
            if part_ok(f):
                complete.append(i)
            elif partial.exists():
                offsets[str(i)] = partial.stat().st_size
        return complete, offsets

    def public(path, record):
        complete, offsets = part_state(path, record)
        return {
            "job_id": record["id"],
            "title": record["title"],
            "lecture_id": record["lecture_id"],
            "phase": record["phase"],
            "detail": record.get("detail", ""),
            "total_parts": record["total_parts"],
            "completed_parts": complete,
            "offsets": offsets,
            "upload_percent": record.get("upload_percent", 0),
            "queue_length": job_queue.qsize(),
            "upload_speed_mbps": record.get("upload_speed_mbps", 0),
            "telegram_message_id": record.get("telegram_message_id"),
        }

    @router.post("", status_code=201)
    async def create_job(body: BrowserJobRequest,
                         x_api_key: str | None = Header(default=None)):
        auth(x_api_key)
        if body.total_parts > max_parts:
            raise HTTPException(status_code=400, detail="Too many parts")
        job_id = uuid.uuid4().hex
        path = root / job_id
        path.mkdir(mode=0o700)
        title = re.sub(r"[\x00-\x1f]", " ", body.title).strip() or "Lecture"
        record = {
            "id": job_id,
            "title": title,
            "lecture_id": body.lecture_id,
            "total_parts": body.total_parts,
            "phase": "receiving",
            "detail": "",
        }
        save(path, record)
        return public(path, record)

    @router.get("/{job_id}")
    async def get_job(job_id: str, x_api_key: str | None = Header(default=None)):
        auth(x_api_key)
        return public(*load(job_id))

    @router.put("/{job_id}/parts/{index}")
    async def upload_chunk(job_id: str, index: int, request: Request,
                           offset: int,
                           x_api_key: str | None = Header(default=None)):
        auth(x_api_key)
        async with locks.setdefault(job_id, asyncio.Lock()):
            path, record = load(job_id)
            guard(record)
            target = part_path(path, index, record["total_parts"])
            if target.exists() and part_ok(target):
                return {"complete": True, "offset": target.stat().st_size}
            partial = target.with_suffix(".partial")
            actual = partial.stat().st_size if partial.exists() else 0
            if actual != offset:
                raise HTTPException(status_code=409, detail={
                    "expected_offset": actual
                })
            chunk = await request.body()
            if not chunk or len(chunk) > CHUNK_LIMIT:
                raise HTTPException(status_code=413, detail="Invalid chunk size")
            total_now = sum(
                f.stat().st_size for f in path.glob("part_*")
                if f.is_file()
            )
            if total_now + len(chunk) > max_bytes or (
                actual + len(chunk) > MAX_PART_SIZE
            ):
                raise HTTPException(status_code=413, detail="Job size limit exceeded")
            with partial.open("ab") as handle:
                handle.write(chunk)
            return {"offset": actual + len(chunk), "complete": False}

    @router.post("/{job_id}/parts/{index}/finish")
    async def finish_part(job_id: str, index: int,
                          body: dict,
                          x_api_key: str | None = Header(default=None)):
        auth(x_api_key)
        async with locks.setdefault(job_id, asyncio.Lock()):
            path, record = load(job_id)
            guard(record)
            target = part_path(path, index, record["total_parts"])
            if part_ok(target):
                return {"complete": True}
            partial = target.with_suffix(".partial")
            size = body.get("size")
            if not isinstance(size, int) or size < 12:
                raise HTTPException(status_code=400, detail="Invalid expected file size")
            if not partial.exists() or partial.stat().st_size != size:
                raise HTTPException(status_code=409, detail="Part size mismatch")
            if not part_ok(partial):
                raise HTTPException(status_code=422, detail="Part is not an MP4")
            os.replace(partial, target)
            # The final finished part is enough to queue the job.
            # This also works when the browser closes before /complete.
            complete_parts, _ = part_state(path, record)
            auto_queued = False
            if len(complete_parts) == record["total_parts"]:
                auto_queued = enqueue(path, record)
            return {"complete": True, "queued": auto_queued}

    async def finalize(job_id):
        path, record = load(job_id)
        msg = None
        try:
            msg = await client.send_message(
                chat_id, f"🔧 Merging {record['total_parts']} parts: {record['title']}"
            )
            concat = path / "concat.txt"
            concat.write_text("".join(
                f"file 'part_{i:03}.mp4'\n"
                for i in range(record["total_parts"])
            ), encoding="utf-8")
            output = path / "lecture_final.mp4"
            if part_ok(output):
                LOG.info("Reusing previously merged MP4 for job=%s", job_id)
            else:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                    "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-c", "copy", "-movflags", "+faststart", str(output),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, error = await proc.communicate()
                if proc.returncode or not part_ok(output):
                    raise RuntimeError("FFmpeg merge failed: " +
                                       error.decode(errors="replace")[-600:])
            metadata = await video_info(output, path, record["total_parts"])
            # Skip custom thumbnail for now: Pyrogram standalone upload worked without it.
            # Allow Telegram to generate the preview from the MP4.
            LOG.info(
                "Video info job=%s duration=%ss resolution=%sx%s thumbnail=%s",
                job_id, metadata["duration"], metadata["width"],
                metadata["height"], False
            )
            record["duration"] = metadata["duration"]
            record["width"] = metadata["width"]
            record["height"] = metadata["height"]
            record["phase"] = "uploading"
            save(path, record)
            size_mb = output.stat().st_size / (1024**2)
            await msg.edit_text(f"📤 Uploading {record['title']} ({size_mb:.1f} MB)")
            last = [0.0]
            started = time.monotonic()
            loop = asyncio.get_running_loop()

            async def edit_upload_message(pct, done, total, mbps):
                try:
                    await msg.edit_text(
                        f"📤 {record['title']}\n"
                        f"{pct:.1f}% | {done / 1024**2:.1f}/{total / 1024**2:.1f} MB\n"
                        f"{mbps:.2f} MB/s"
                    )
                except Exception as exc:
                    LOG.debug("Progress message edit skipped: %s", exc)

            def progress(done, total):
                # Use a synchronous callback like the known-working CLI test.
                now = time.monotonic()
                if now - last[0] < 3 and done < total:
                    return
                last[0] = now
                pct = round(100 * done / total, 1) if total else 0.0
                mbps = done / (1024**2) / max(now - started, 0.001)
                record["upload_percent"] = pct
                record["upload_mb"] = round(done / 1024**2, 1)
                record["upload_speed_mbps"] = round(mbps, 2)
                save(path, record)
                LOG.info("Telegram upload job=%s %.1f%% %.1f/%.1f MB %.2f MB/s",
                         job_id, pct, done / 1024**2, total / 1024**2, mbps)
                loop.create_task(edit_upload_message(pct, done, total, mbps))

            # Resolve the peer while the client is active, not only when
            # sending the file (fresh Pyrogram peer cache may be empty).
            await client.get_chat(chat_id)
            LOG.info("Starting Pyrogram video send job=%s size=%.1f MB", job_id, size_mb)
            sent = await client.send_video(
                chat_id=chat_id,
                video=str(output),
                caption=f"🎬 {record['title']}",
                duration=metadata["duration"],
                width=metadata["width"],
                height=metadata["height"],
                supports_streaming=True,
                progress=progress,
            )
            record["telegram_message_id"] = sent.id
            record["upload_percent"] = 100
            record["phase"] = "complete"
            record["detail"] = f"Sent {size_mb:.1f} MB to Telegram"
            save(path, record)
            # Delivery is already durable here; cosmetic failures must not resend it.
            try:
                await msg.edit_text(f"✅ Sent: {record['title']} ({size_mb:.1f} MB)")
            except Exception as exc:
                LOG.warning("Post-send edit failed for %s: %s", job_id, exc)
            try:
                for f in path.glob("part_*"):
                    f.unlink(missing_ok=True)
                output.unlink(missing_ok=True)
                concat.unlink(missing_ok=True)
                (path / "telegram_thumb.jpg").unlink(missing_ok=True)
            except OSError as exc:
                LOG.warning("Post-send media cleanup failed for %s: %s", job_id, exc)
        except Exception as exc:
            LOG.exception("Browser upload job %s failed", job_id)
            record["phase"] = "failed"
            record["detail"] = str(exc)[:500]
            save(path, record)
            if msg:
                try:
                    await msg.edit_text(f"❌ {record['title']} failed: {str(exc)[:400]}")
                except Exception:
                    pass
        finally:
            active.pop(job_id, None)

    def is_ready(path, record):
        output = path / "lecture_final.mp4"
        if part_ok(output):
            return True
        complete_parts, _ = part_state(path, record)
        return len(complete_parts) == record["total_parts"]

    def enqueue(path, record):
        jid = record["id"]
        if jid in pending or jid in active:
            return False
        if record["phase"] == "complete":
            return False
        record["phase"] = "queued"
        record["detail"] = "Waiting for sequential Telegram upload"
        record["upload_percent"] = 0
        save(path, record)
        pending.add(jid)
        job_queue.put_nowait(jid)
        LOG.info("Browser queue: queued job=%s pending=%s", jid, job_queue.qsize())
        return True

    async def run_queue():
        while True:
            jid = await job_queue.get()
            pending.discard(jid)
            try:
                path, record = load(jid)
                if record["phase"] == "complete":
                    continue
                if not is_ready(path, record):
                    record["phase"] = "failed"
                    record["detail"] = "Not all media parts are present"
                    save(path, record)
                    continue
                record["phase"] = "merging"
                record["detail"] = ""
                save(path, record)
                LOG.info("Browser queue: starting job=%s", jid)
                active[jid] = asyncio.current_task()
                try:
                    await finalize(jid)
                finally:
                    active.pop(jid, None)
                _, result = load(jid)
                if result["phase"] == "failed":
                    attempts = int(result.get("auto_retry_count", 0))
                    if attempts < max_auto_retries:
                        result["auto_retry_count"] = attempts + 1
                        save(path, result)
                        LOG.warning(
                            "Browser queue: retrying failed job=%s attempt=%s/%s",
                            jid, attempts + 1, max_auto_retries
                        )
                        enqueue(path, result)
                else:
                    LOG.info("Browser queue: finished job=%s phase=%s",
                             jid, result["phase"])
            except asyncio.CancelledError:
                LOG.warning("Browser queue worker stopped; unfinished job=%s "
                            "will be recovered on startup", jid)
                raise
            except Exception:
                LOG.exception("Browser queue: unexpected failure job=%s", jid)
            finally:
                job_queue.task_done()

    async def startup():
        nonlocal queue_worker
        if queue_worker and not queue_worker.done():
            return
        queue_worker = asyncio.create_task(run_queue(), name="browser-upload-queue")
        recovered = []
        for meta in root.glob("*/metadata.json"):
            try:
                path = meta.parent
                record = json.loads(meta.read_text("utf-8"))
                phase = record.get("phase")
                if record.get("telegram_message_id"):
                    if phase != "complete":
                        record["phase"] = "complete"
                        record["detail"] = "Already delivered to Telegram"
                        save(path, record)
                    continue
                if phase == "complete":
                    continue
                if phase not in ("receiving", "queued", "merging", "uploading", "failed"):
                    continue
                if not is_ready(path, record):
                    continue
                retries = int(record.get("auto_retry_count", 0))
                if phase in ("failed", "merging", "uploading") and retries >= max_auto_retries:
                    LOG.warning("Browser queue: job=%s reached retry limit", record["id"])
                    continue
                recovered.append((phase not in ("uploading", "merging"),
                                  record.get("updated", ""), path, record))
            except Exception:
                LOG.exception("Browser queue: cannot recover %s", meta)
        recovered.sort(key=lambda x: (x[0], x[1]))
        for _, _, path, record in recovered:
            if record["phase"] in ("failed", "merging", "uploading"):
                record["auto_retry_count"] = int(record.get("auto_retry_count", 0)) + 1
            enqueue(path, record)
        LOG.info("Browser queue started: %s recovered jobs", len(recovered))

    async def shutdown():
        nonlocal queue_worker
        if queue_worker:
            queue_worker.cancel()
            try:
                await queue_worker
            except asyncio.CancelledError:
                pass
            queue_worker = None

    @router.post("/{job_id}/complete", status_code=202)
    async def complete(job_id: str, x_api_key: str | None = Header(default=None)):
        auth(x_api_key)
        async with locks.setdefault(job_id, asyncio.Lock()):
            path, record = load(job_id)
            if record["phase"] in ("queued", "merging", "uploading", "complete"):
                return public(path, record)
            if not is_ready(path, record):
                complete_parts, _ = part_state(path, record)
                raise HTTPException(status_code=409, detail=(
                    f"Only {len(complete_parts)}/{record['total_parts']} parts uploaded"
                ))
            record["auto_retry_count"] = 0
            enqueue(path, record)
            return public(path, record)

    app.include_router(router)
    return startup, shutdown
