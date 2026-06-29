import time
import asyncio
import logging
from main import Var
from typing import Dict, Tuple, Union
from main.bot import work_loads
from pyrogram import Client, utils, raw
from pyrogram.crypto import aes
from pyrogram.errors import CDNFileHashMismatch, Timeout as TelegramTimeout, VolumeLocNotFound
from .file_properties import get_file_ids
from .stream_range import chunk_size, offset_fix
from pyrogram.session import Session
from main.exceptions import FIleNotFound
from pyrogram.file_id import FileId, FileType, ThumbnailSource


CACHE_TTL = 30 * 60
CACHE_SWEEP_INTERVAL = 5 * 60


class ByteStreamer:
    def __init__(self, client: Client):
        """A custom class that holds the cache of a specific client and class functions.

        This is a modified version of <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        self.client: Client = client
        self.cached_file_ids: Dict[int, Tuple[float, FileId]] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, message_id: int) -> FileId:
        """
        Returns the properties of a media of a specific message in a FileId class.
        Cached entries older than CACHE_TTL are refreshed.
        """
        entry = self.cached_file_ids.get(message_id)
        if entry is None or (time.monotonic() - entry[0]) > CACHE_TTL:
            await self.generate_file_properties(message_id)
            logging.debug(f"Cached file properties for message with ID {message_id}")
        return self.cached_file_ids[message_id][1]

    async def generate_file_properties(self, message_id: int) -> FileId:
        """
        Generates the properties of a media file on a specific message.
        returns ths properties in a FIleId class.
        """
        file_id = await get_file_ids(self.client, Var.BIN_CHANNEL, message_id)
        logging.debug(f"Generated file ID and Unique ID for message with ID {message_id}")
        if not file_id:
            logging.debug(f"Message with ID {message_id} not found")
            raise FIleNotFound
        self.cached_file_ids[message_id] = (time.monotonic(), file_id)
        logging.debug(f"Cached media message with ID {message_id}")
        return file_id

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Returns a media session for the DC that holds the file. Delegates the
        full cross-DC auth handshake + media-session cache to kurigram's
        Client.get_session helper.
        """
        return await client.get_session(file_id.dc_id, is_media=True)


    @staticmethod
    async def get_location(file_id: FileId) -> Union[raw.types.InputPhotoFileLocation,
                                                     raw.types.InputDocumentFileLocation,
                                                     raw.types.InputPeerPhotoFileLocation,]:
        """
        Returns the file location for the media file.
        """
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        return location

    async def yield_file(
        self,
        file_id: FileId,
        index: int,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ) -> Union[str, None]:
        """
        Custom generator that yields the bytes of the media file.
        Modded from <https://github.com/eyaadh/megadlbot_oss/blob/master/mega/telegram/utils/custom_download.py#L20>
        Thanks to Eyaadh <https://github.com/eyaadh>
        """
        client = self.client
        work_loads[index] += 1
        logging.debug(f"Starting to yielding file with client {index}.")
        media_session = await self.generate_media_session(client, file_id)

        current_part = 1

        location = await self.get_location(file_id)

        cdn_session: Union[Session, None] = None
        try:
            # Retry the *initial* GetFile a few times. Under thumbnail batch
            # load, the first call for a given (file, offset) sometimes
            # times out — the silent ``except TimeoutError: pass`` below
            # then yields zero bytes, and the skeleton cache truncation
            # guard surfaces it as a 503. A short retry loop here gives
            # the media session a second chance before we give up.
            r = None
            last_err: Union[BaseException, None] = None
            for attempt in range(3):
                try:
                    r = await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location, offset=offset, limit=chunk_size
                        ),
                    )
                    break
                except (TimeoutError, asyncio.TimeoutError, TelegramTimeout) as e:
                    last_err = e
                    logging.warning(
                        "yield_file: initial GetFile timeout (attempt %d/3) "
                        "media_id=%s offset=%d limit=%d",
                        attempt + 1,
                        getattr(file_id, "media_id", "?"),
                        offset, chunk_size,
                    )
                    await asyncio.sleep(0.5 * (attempt + 1))
            if r is None:
                logging.warning(
                    "yield_file: giving up after initial GetFile timeouts "
                    "media_id=%s offset=%d (last error: %r)",
                    getattr(file_id, "media_id", "?"), offset, last_err,
                )
                return
            if isinstance(r, raw.types.upload.File):
                while current_part <= part_count:
                    chunk = r.bytes
                    if not chunk:
                        break
                    offset += chunk_size
                    if part_count == 1:
                        yield chunk[first_part_cut:last_part_cut]
                        break
                    if current_part == 1:
                        yield chunk[first_part_cut:]
                    elif current_part == part_count:
                        # Trim the trailing bytes so the range that ends
                        # mid-chunk doesn't overshoot what the Content-Range
                        # header promised.
                        yield chunk[:last_part_cut]
                    else:
                        yield chunk

                    r = await media_session.send(
                        raw.functions.upload.GetFile(
                            location=location, offset=offset, limit=chunk_size
                        ),
                    )

                    current_part += 1
            elif isinstance(r, raw.types.upload.FileCdnRedirect):
                # Telegram serves popular/large files from edge CDNs. The
                # initial DC responds with a redirect; the bytes live on
                # ``r.dc_id`` and arrive encrypted (CTR-256). Without this
                # branch yield_file would silently return zero bytes, which
                # is the root cause of the "End of file" thumbnail failures
                # for these specific message ids.
                cdn_session = await client.get_session(
                    r.dc_id, is_cdn=True, temporary=True
                )
                while current_part <= part_count:
                    r2 = await cdn_session.send(
                        raw.functions.upload.GetCdnFile(
                            file_token=r.file_token,
                            offset=offset,
                            limit=chunk_size,
                        )
                    )
                    if isinstance(r2, raw.types.upload.CdnFileReuploadNeeded):
                        # CDN node hasn't been primed yet — ask the home DC
                        # to push the file, then retry the same offset.
                        try:
                            await media_session.send(
                                raw.functions.upload.ReuploadCdnFile(
                                    file_token=r.file_token,
                                    request_token=r2.request_token,
                                )
                            )
                        except VolumeLocNotFound:
                            break
                        continue

                    chunk = r2.bytes
                    if not chunk:
                        break

                    # https://core.telegram.org/cdn#decrypting-files
                    iv = bytearray(
                        r.encryption_iv[:-4]
                        + (offset // 16).to_bytes(4, "big")
                    )
                    decrypted = aes.ctr256_decrypt(chunk, r.encryption_key, iv)

                    offset += chunk_size
                    if part_count == 1:
                        yield decrypted[first_part_cut:last_part_cut]
                        break
                    if current_part == 1:
                        yield decrypted[first_part_cut:]
                    elif current_part == part_count:
                        yield decrypted[:last_part_cut]
                    else:
                        yield decrypted
                    current_part += 1
            else:
                # Some other unexpected response type — log instead of
                # silently returning so it's obvious next time.
                logging.warning(
                    "yield_file: unexpected upload.GetFile response %r "
                    "(media_id=%s offset=%d)",
                    type(r).__name__, getattr(file_id, "media_id", "?"), offset,
                )
        except (TimeoutError, asyncio.TimeoutError, TelegramTimeout, AttributeError) as e:
            # Mid-stream timeout or detached session — log so we can tell this
            # apart from "everything finished" in the diagnostics.
            logging.warning(
                "yield_file: aborted mid-stream after part %d/%d "
                "media_id=%s offset=%d (%s)",
                current_part, part_count,
                getattr(file_id, "media_id", "?"),
                offset, type(e).__name__,
            )
        finally:
            logging.debug(f"Finished yielding file with {current_part} parts.")
            work_loads[index] -= 1
            if cdn_session is not None:
                try:
                    await cdn_session.stop()
                except Exception:
                    pass

    
    async def clean_cache(self) -> None:
        """
        Periodically evict only stale FileId entries instead of clearing everything,
        which would force every in-flight stream to re-fetch from Telegram at once.
        """
        while True:
            await asyncio.sleep(CACHE_SWEEP_INTERVAL)
            now = time.monotonic()
            stale = [mid for mid, (ts, _) in self.cached_file_ids.items() if now - ts > CACHE_TTL]
            for mid in stale:
                del self.cached_file_ids[mid]
            if stale:
                logging.debug(f"Evicted {len(stale)} stale cache entries")
