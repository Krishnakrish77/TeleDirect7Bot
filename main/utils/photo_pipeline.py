"""Photo ingest pipeline — EXIF, sha256, thumbnails.

Runs entirely from bytes in memory:
  * probe dimensions/duration (Pillow for images, ffprobe for video)
  * EXIF taken-time / camera / GPS (piexif; pillow-heif for HEIC)
  * sha256 for per-user dedup
  * two webp thumbs (grid ~400px, preview ~1600px; ffmpeg poster for video)

CPU-bound work is shielded from the event loop with run_in_executor.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from main import Var
from main.utils import photo_store

try:
    from pyrogram.errors import FloodWait
except ImportError:  # pragma: no cover
    FloodWait = Exception  # type: ignore[assignment,misc]

log = logging.getLogger("photos.pipeline")


# ── Image probing ─────────────────────────────────────────────────────────


def _piexif_exif(data: bytes) -> dict:
    """Return flat EXIF dict {name: value} for JPEG/HEIF-embedded Exif."""
    try:
        import piexif
        exif_dict = piexif.load(data)
    except Exception as exc:
        log.debug("piexif exif load failed: %r", exc)
        return {}
    flat: Dict[str, Any] = {}
    for ifd_name in ("0th", "Exif", "GPS"):
        ifd = exif_dict.get(ifd_name) or {}
        for tag_id, value in ifd.items():
            try:
                name = piexif.TAGS[ifd_name][tag_id].get("name", "") if ifd_name != "GPS" else f"GPS{tag_id}"
            except Exception:
                name = ""
            if name:
                flat[name] = value
    return flat


def _parse_exif_datetime(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        # EXIF format: "YYYY:MM:DD HH:MM:SS"
        dt = datetime.strptime(text.strip()[:19], "%Y:%m:%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _image_probe(data: bytes, mime: str, file_name: str) -> dict:
    """Probe + EXIF + thumbnails for an image. Returns pipeline fields."""
    out: Dict[str, Any] = {}
    try:
        from PIL import Image
        try:
            import pillow_heif  # noqa: F401
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        img = Image.open(io.BytesIO(data))
        img.load()
        out["width"], out["height"] = img.size
        fmt = (img.format or "").upper()
        is_heic = fmt in ("HEIF", "AVIF", "MPO") or mime in ("image/heic", "image/heif")

        # EXIF (Pillow's own reader covers most; piexif covers odd JPEGs).
        exif_data = {}
        try:
            raw_exif = img.getexif()
            if raw_exif:
                from PIL.ExifTags import TAGS, GPSTAGS
                exif_data = {
                    TAGS.get(k, str(k)): v for k, v in raw_exif.items()
                }
                # IFD sub-blocks: DateTimeOriginal/camera live in the Exif
                # IFD, coordinates in the GPS IFD. Both hang off IFD0 as
                # numeric pointers (0x8769 / 0x8825) — PIL.Image.ExifID does
                # NOT exist, so the previous form raised AttributeError and
                # silently dropped capture time + GPS for every image.
                try:
                    from PIL.ExifTags import TAGS as _T2
                    exif_ifd = raw_exif.get_ifd(0x8769)
                    for k, v in exif_ifd.items():
                        exif_data[_T2.get(k, str(k))] = v
                    gps_pairs = {
                        GPSTAGS.get(k, str(k)): v
                        for k, v in raw_exif.get_ifd(0x8825).items()
                    }
                    if gps_pairs:
                        exif_data["GPSInfo"] = gps_pairs
                except Exception as exc:
                    # Hostile/malformed EXIF must not fail the probe, but it
                    # must not be silent either — a swallowed error here is
                    # how the capture-time/GPS regression went unnoticed.
                    log.warning("exif sub-ifd parse failed: %s", exc)
        except Exception:
            pass

        taken = _parse_exif_datetime(
            (exif_data.get("DateTimeOriginal") or exif_data.get("DateTime") or b"")
        )
        if not taken and is_heic:
            # pillow-heif surfaces EXIF through info; try raw piexif path.
            flat = _piexif_exif(data)
            taken = _parse_exif_datetime(flat.get("DateTimeOriginal"))
        if taken:
            out["taken_at"] = taken
        make = exif_data.get("Make")
        model = exif_data.get("Model")
        if make or model:
            out["camera"] = " ".join(str(x) for x in (make, model) if x).strip()
        gps = exif_data.get("GPSInfo") or {}
        try:
            lat = _dms_to_deg(gps.get("GPSLatitude"), gps.get("GPSLatitudeRef", "N"))
            lon = _dms_to_deg(gps.get("GPSLongitude"), gps.get("GPSLongitudeRef", "E"))
            if lat is not None and lon is not None:
                out["gps"] = {"lat": round(lat, 6), "lon": round(lon, 6)}
        except Exception:
            pass

        # Thumbnails. thumbnail() mutates in place — copy the source image
        # per size so the grid thumb doesn't shrink the preview input.
        grid = _image_thumb(_copy_image(img), Var.PHOTO_THUMB_GRID)
        preview = _image_thumb(_copy_image(img), Var.PHOTO_THUMB_PREVIEW)
        if grid:
            out["thumb_grid"] = grid
        if preview:
            out["thumb_preview"] = preview
    except Exception as exc:
        log.warning("image probe failed (%s, %s): %r", mime, file_name, exc, exc_info=True)
    return out


def _dms_to_deg(dms, ref) -> Optional[float]:
    if not dms:
        return None
    try:
        vals = [float(v) for v in dms] if not isinstance(dms, bytes) else []
        if len(vals) != 3:
            return None
        deg = vals[0] + vals[1] / 60 + vals[2] / 3600
        ref = (ref.decode() if isinstance(ref, bytes) else str(ref)).upper().strip()
        return -deg if ref in ("S", "W") else deg
    except Exception:
        return None


def _copy_image(img):
    """Fresh decode-preserving copy — Image.thumbnail mutates in place, so
    each thumbnail size needs its own instance."""
    clone = img.copy()
    clone.load()
    return clone


def _image_thumb(img, edge: int) -> Optional[bytes]:
    try:
        from PIL import ImageOps
        im = img
        if getattr(im, "is_animated", False):
            im.seek(0)
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        im.thumbnail((edge, edge))
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=82, method=4)
        return buf.getvalue()
    except Exception as exc:
        log.warning("thumb gen failed (edge=%d): %s", edge, exc)
        return None


# ── Video probing ─────────────────────────────────────────────────────────


def _video_probe(data: bytes, mime: str, file_name: str) -> dict:
    """Probe duration + generate a poster frame for video.

    Bytes are piped through ffmpeg/ffprobe stdin — originals never touch
    disk (feature contract). mp4/mov demuxers need seekable input, so for
    those we accept the one-pass limitation: probing works, poster falls
    back to inputseek 0.
    """
    out: Dict[str, Any] = {}
    try:
        info = _ffprobe_bytes(data)
        vstream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
        duration = float(info.get("format", {}).get("duration") or vstream.get("duration") or 0)
        if duration:
            out["duration"] = round(duration, 3)
        if vstream:
            out["width"] = int(vstream.get("width") or 0) or None
            out["height"] = int(vstream.get("height") or 0) or None
        poster = _video_poster_stdin(data, Var.PHOTO_THUMB_PREVIEW)
        if poster:
            out["thumb_preview"] = poster
            if out.get("width"):
                out["thumb_grid"] = _webp_resize(poster, Var.PHOTO_THUMB_GRID) or poster
    except Exception as exc:
        log.warning("video probe failed: %s", exc)
    return out


def _ffprobe_bytes(data: bytes) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", "-"],
        input=data, capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        log.warning(
            "ffprobe failed (rc=%d): %s",
            result.returncode,
            (result.stderr or b"").decode("utf-8", "replace").strip()[:400] or "no stderr",
        )
        return {}
    import json
    return json.loads(result.stdout or b"{}")


def _video_poster_stdin(data: bytes, edge: int) -> Optional[bytes]:
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "quiet",
                "-i", "-",               # bytes from stdin
                "-frames:v", "1",
                "-vf", f"scale='min({edge},iw)':-2",
                "-f", "image2pipe", "-vcodec", "png", "-",
            ],
            input=data, capture_output=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout:
            return _webp_resize(result.stdout, edge) or result.stdout
        log.warning(
            "ffmpeg poster failed (rc=%d): %s",
            result.returncode,
            (result.stderr or b"").decode("utf-8", "replace").strip()[:400] or "no stderr",
        )
    except Exception as exc:
        log.warning("video poster failed: %r", exc, exc_info=True)
    return None


def _webp_resize(data: bytes, edge: int) -> Optional[bytes]:
    try:
        from io import BytesIO
        from PIL import Image, ImageOps
        img = Image.open(BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        img.thumbnail((edge, edge))
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=82, method=4)
        return buf.getvalue()
    except Exception as exc:
        log.debug("webp resize failed (edge=%d): %r", edge, exc)
        return None


# ── Public entry points ───────────────────────────────────────────────────


_pipeline_exec: Optional[concurrent.futures.ThreadPoolExecutor] = None
_download_sem: Optional[asyncio.Semaphore] = None


def download_slot() -> asyncio.Semaphore:
    """Shared cap on simultaneously-buffered originals.

    An original is held in RAM from download through thumbnailing, and
    ingest runs one worker per bound channel, so without this N channels can
    buffer N files at once (``PHOTOS_UPLOAD_MAX_FILE`` each). Callers await
    the slot; ingest is queue-paced, so waiting only delays the next photo.
    """
    global _download_sem
    if _download_sem is None:
        _download_sem = asyncio.Semaphore(Var.PHOTOS_FETCH_CONCURRENCY)
    return _download_sem


def _pipeline_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Bounded executor for decode/probe/thumbnail work.

    Ingest runs one worker per bound channel and the thumb route can ask for
    regeneration on demand, so an unbounded pool lets concurrent users run
    that many Pillow/ffmpeg jobs — each holding a fully decoded image.
    ``Var.PHOTOS_PIPELINE_WORKERS`` caps the real concurrency.
    """
    global _pipeline_exec
    if _pipeline_exec is None:
        _pipeline_exec = concurrent.futures.ThreadPoolExecutor(
            max_workers=Var.PHOTOS_PIPELINE_WORKERS,
            thread_name_prefix="photopipe",
        )
    return _pipeline_exec


def process_sync(data: bytes, mime: str, file_name: str) -> dict:
    """Synchronous pipeline step (runs in the pipeline executor)."""
    kind = "video" if (mime or "").startswith("video/") else "image"
    if kind == "image":
        result = _image_probe(data, mime, file_name)
    else:
        result = _video_probe(data, mime, file_name)
    import hashlib
    result["sha256"] = hashlib.sha256(data).hexdigest()
    result["kind"] = kind
    return result


async def process(data: bytes, mime: str, file_name: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _pipeline_executor(), process_sync, data, mime, file_name
    )


async def generate_thumbs_for(owner_user_id: int, channel_id: int,
                              message_id: int, size: str) -> Optional[bytes]:
    """Regenerate one thumb on demand. Requires an original fetch from
    Telegram (rare — only when ingest-time generation failed)."""
    doc = await photo_store.get_photo(owner_user_id, message_id, channel_id=channel_id)
    if not doc:
        log.info("thumb regen: no photo doc mid=%d cid=%d size=%s", message_id, channel_id, size)
        return None
    from main.bot import multi_clients
    client = multi_clients.get(0)
    if client is None:
        log.warning("thumb regen: no bot client available mid=%d", message_id)
        return None
    from pyrogram.file_id import FileId
    try:
        FileId.decode(doc["file_id"])
    except Exception as exc:
        log.warning("thumb regen: stored file id undecodable mid=%d: %r", message_id, exc)
        return None
    try:
        # Same memory bound as ingest: this buffers a whole original.
        async with download_slot():
            client_msg = await client.get_messages(channel_id, message_id)
            if not client_msg:
                log.warning(
                    "thumb regen: channel message missing mid=%d cid=%d", message_id, channel_id
                )
                return None
            data = await client_msg.download(in_memory=True)
            # download(in_memory=True) returns BytesIO, not bytes.
            if isinstance(data, io.BytesIO):
                data = data.getvalue()
            if not data:
                return None
            result = await process(data, doc.get("mime") or "", doc.get("file_name") or "")
        thumb = result.get("thumb_preview" if size == "preview" else "thumb_grid")
        if thumb:
            await photo_store.put_thumb(
                owner_user_id, photo_store.thumb_key(channel_id, message_id, size), thumb
            )
            await photo_store.set_thumb_flags(
                owner_user_id, channel_id, message_id,
                grid=size == "grid", preview=size == "preview",
            )
        return thumb
    except Exception as exc:
        log.warning("thumb regen failed mid=%d size=%s: %r", message_id, size, exc, exc_info=True)
        return None


async def ingest_message(owner_user_id: int, channel_id: int, message) -> None:
    """Full ingest for one channel post.

    Raises FloodWait to the caller (the ingest worker requeues it); all
    other exceptions are logged and swallowed — one bad file must not
    stall the channel queue.
    """
    try:
        # Telegram video messages carry .video, not .document — pick the
        # most specific media attribute and read mime/name from it.
        media = message.document or message.video or message.photo
        if media is None:
            return
        # RAM bound: a direct channel post bypasses the web-upload caps.
        # Check the declared size BEFORE downloading so an oversized post
        # never enters memory; thumbnail generation buffers the whole
        # file, so guard process memory.
        media_size = int(getattr(media, "file_size", 0) or 0)
        if media_size > Var.PHOTOS_UPLOAD_MAX_FILE:
            log.warning(
                "ingest skip mid=%d: %d bytes exceeds per-file cap", message.id, media_size
            )
            return
        data = await message.download(in_memory=True)
        # download(in_memory=True) returns a BytesIO, not bytes.
        if isinstance(data, io.BytesIO):
            data = data.getvalue()
        # Declared size can lie; enforce the cap on the actual bytes too.
        if len(data) > Var.PHOTOS_UPLOAD_MAX_FILE:
            log.warning(
                "ingest skip mid=%d: actual %d bytes exceeds per-file cap",
                message.id, len(data),
            )
            return
        if message.photo and not (message.document or message.video):
            mime = "image/jpeg"
            file_name = f"photo_{message.id}.jpg"
        else:
            mime = getattr(media, "mime_type", "") or "application/octet-stream"
            file_name = getattr(media, "file_name", "") or f"file_{message.id}"
        # Photos vaults hold images and videos only — skip PDFs and
        # arbitrary documents posted to a bound channel.
        if not (mime.startswith("image/") or mime.startswith("video/")):
            log.info("ingest skip mid=%d: unsupported mime %s", message.id, mime)
            return
        doc = await photo_store.get_photo(owner_user_id, message.id, channel_id=channel_id)
        if doc and doc.get("sha256"):
            return  # already processed
        result = await process(data, mime, file_name)
        # Dedup (race-safe): if another ingest/upload with the same
        # (owner, sha256) already inserted a doc, the unique index makes
        # our insert a no-op ("duplicate") — the extra channel copy the
        # losing request sent to Telegram is simply not double-indexed.
        err = await photo_store.upsert_photo({
            "owner_user_id": owner_user_id,
            "channel_id": channel_id,
            "message_id": message.id,
            "file_id": str(getattr(media, "file_id", "")),
            "kind": result["kind"],
            "file_name": file_name,
            "mime": mime,
            "size": len(data),
            "width": result.get("width"),
            "height": result.get("height"),
            "duration": result.get("duration"),
            "taken_at": result.get("taken_at") or _now_utc(),
            "camera": result.get("camera"),
            "gps": result.get("gps"),
            "sha256": result["sha256"],
            "uploaded_at": _now_utc(),
        })
        if err and err != "duplicate":
            log.warning("ingest mid=%d: %s", message.id, err)
            return
        if result.get("thumb_grid"):
            await photo_store.put_thumb(
                owner_user_id,
                photo_store.thumb_key(channel_id, message.id, "grid"),
                result["thumb_grid"],
            )
        if result.get("thumb_preview"):
            await photo_store.put_thumb(
                owner_user_id,
                photo_store.thumb_key(channel_id, message.id, "preview"),
                result["thumb_preview"],
            )
        await photo_store.set_thumb_flags(
            owner_user_id, channel_id, message.id,
            grid=bool(result.get("thumb_grid")),
            preview=bool(result.get("thumb_preview")),
        )
    except FloodWait:
        raise
    except Exception:
        log.exception("ingest failed cid=%d mid=%d", channel_id, getattr(message, "id", -1))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
