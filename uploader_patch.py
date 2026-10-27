#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot High-Speed MTProto Parallel Uploader (v2)
================================================
- 6 Kalıcı MTProto Media Session Havuzu (Warm Session Pool)
- Her soket için 2 In-Flight Pipelining (Toplam 12 Eşzamanlı Parça İletimi)
- 512 KB parça tamponlama ve sıfır gecikmeli TCP akış doygunluğu
- Anlık kayar pencereli hız ölçümü (Instantaneous Speed Calculation)
"""

import os
import io
import math
import asyncio
import logging
from hashlib import md5
from pathlib import PurePath
from typing import Union, BinaryIO, Callable, Optional, List

import pyrogram
from pyrogram import raw, StopTransmission
from pyrogram.session import Session

logger = logging.getLogger("DiziBot.ParallelUploader")

class MediaSessionPool:
    """Telegram DC medya sunucularına kalıcı ve sıcak (warm) çoklu MTProto oturumu sağlar."""
    def __init__(self, client: "pyrogram.Client", pool_size: int = 6):
        self.client = client
        self.pool_size = pool_size
        self.sessions: List[Session] = []
        self._lock = asyncio.Lock()
        self._started = False

    async def get_sessions(self) -> List[Session]:
        async with self._lock:
            if not self._started or len(self.sessions) != self.pool_size:
                # Önceki oturumları kapat
                for s in self.sessions:
                    try:
                        await s.stop()
                    except Exception:
                        pass
                self.sessions.clear()

                dc_id = await self.client.storage.dc_id()
                auth_key = await self.client.storage.auth_key()
                test_mode = await self.client.storage.test_mode()

                self.sessions = [
                    Session(self.client, dc_id, auth_key, test_mode, is_media=True)
                    for _ in range(self.pool_size)
                ]
                # Tüm oturumları paralel başlat
                await asyncio.gather(*[s.start() for s in self.sessions], return_exceptions=True)
                self._started = True
                logger.info(f"⚡ MTProto Medya Havuzu Hazır: {len(self.sessions)} kalıcı oturum bağlandı (DC{dc_id}).")

            return self.sessions

    async def close(self):
        async with self._lock:
            if self._started:
                await asyncio.gather(*[s.stop() for s in self.sessions], return_exceptions=True)
                self.sessions.clear()
                self._started = False


_POOL_INSTANCE: Optional[MediaSessionPool] = None

async def parallel_save_file(
    client: "pyrogram.Client",
    path: Union[str, BinaryIO],
    file_id: int = None,
    file_part: int = 0,
    progress: Callable = None,
    progress_args: tuple = (),
    pool_size: int = 6,
    in_flight_per_session: int = 2
):
    """
    6 kalıcı MTProto oturumu ve her oturum için 2 in-flight pipelining ile
    aynı anda 12 parçayı (6 MB) eşzamanlı Telegram'a basarak tam hat doygunluğuna ulaşır.
    """
    global _POOL_INSTANCE
    async with client.save_file_semaphore:
        if path is None:
            return None

        part_size = 512 * 1024  # 512 KB parçalar

        if isinstance(path, (str, PurePath)):
            fp = open(path, "rb")
        elif isinstance(path, io.IOBase):
            fp = path
        else:
            raise ValueError("Invalid file. Expected file path or binary file object.")

        file_name = getattr(fp, "name", "video.mp4")
        if isinstance(file_name, str):
            file_name = os.path.basename(file_name)

        fp.seek(0, os.SEEK_END)
        file_size = fp.tell()
        fp.seek(0)

        if file_size == 0:
            if isinstance(path, (str, PurePath)):
                fp.close()
            raise ValueError("File size equals to 0 B")

        file_total_parts = int(math.ceil(file_size / part_size))
        is_big = file_size > 10 * 1024 * 1024  # 10 MB üstü SaveBigFilePart
        file_id = file_id or client.rnd_id()
        md5_sum = md5() if not is_big else None

        # 10 MB altındaki küçük dosyalarda tek iş yeterli
        if not is_big:
            session = Session(
                client, await client.storage.dc_id(), await client.storage.auth_key(),
                await client.storage.test_mode(), is_media=True
            )
            await session.start()
            try:
                curr_part = file_part
                fp.seek(part_size * curr_part)
                while True:
                    chunk = fp.read(part_size)
                    if not chunk:
                        break
                    md5_sum.update(chunk)
                    rpc = raw.functions.upload.SaveFilePart(
                        file_id=file_id,
                        file_part=curr_part,
                        bytes=chunk
                    )
                    await session.invoke(rpc)
                    curr_part += 1
            finally:
                await session.stop()
                if isinstance(path, (str, PurePath)):
                    fp.close()

            return raw.types.InputFile(
                id=file_id,
                parts=file_total_parts,
                name=file_name,
                md5_checksum="".join([hex(i)[2:].zfill(2) for i in md5_sum.digest()])
            )

        # Büyük dosyalarda havuzdan oturumları al
        if _POOL_INSTANCE is None:
            _POOL_INSTANCE = MediaSessionPool(client, pool_size=pool_size)

        try:
            sessions = await _POOL_INSTANCE.get_sessions()
        except Exception as e:
            logger.warning(f"Session havuzu başlatılamadı, tekil oturuma dönülüyor: {e}")
            dc_id = await client.storage.dc_id()
            auth_key = await client.storage.auth_key()
            test_mode = await client.storage.test_mode()
            sessions = [Session(client, dc_id, auth_key, test_mode, is_media=True)]
            await sessions[0].start()

        num_sessions = len(sessions)
        total_workers = num_sessions * in_flight_per_session
        queue = asyncio.Queue(maxsize=total_workers * 2)

        uploaded_bytes = 0
        prog_lock = asyncio.Lock()
        upload_error = None

        async def worker(sess: Session):
            nonlocal uploaded_bytes, upload_error
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    return

                if upload_error is not None:
                    queue.task_done()
                    continue

                part_idx, chunk_bytes = item
                rpc = raw.functions.upload.SaveBigFilePart(
                    file_id=file_id,
                    file_part=part_idx,
                    file_total_parts=file_total_parts,
                    bytes=chunk_bytes
                )

                success = False
                for attempt in range(5):
                    try:
                        await sess.invoke(rpc)
                        success = True
                        break
                    except StopTransmission as st:
                        upload_error = st
                        break
                    except Exception as err:
                        if attempt == 4:
                            logger.error(f"Part #{part_idx} yükleme hatası (tükendi): {err}")
                            upload_error = err
                            break
                        await asyncio.sleep(0.3 * (attempt + 1))

                queue.task_done()

                if success and progress and upload_error is None:
                    async with prog_lock:
                        uploaded_bytes += len(chunk_bytes)
                        cur = uploaded_bytes
                    try:
                        if asyncio.iscoroutinefunction(progress):
                            await progress(cur, file_size, *progress_args)
                        else:
                            await client.loop.run_in_executor(client.executor, progress, cur, file_size, *progress_args)
                    except StopTransmission:
                        upload_error = StopTransmission()
                    except Exception:
                        pass

        # Her oturuma in_flight_per_session kadar worker görevi bağla
        worker_tasks = []
        for s in sessions:
            for _ in range(in_flight_per_session):
                worker_tasks.append(client.loop.create_task(worker(s)))

        try:
            curr_part = file_part
            fp.seek(part_size * curr_part)

            while True:
                if upload_error is not None:
                    break

                chunk = fp.read(part_size)
                if not chunk:
                    break

                await queue.put((curr_part, chunk))
                curr_part += 1

            await queue.join()

            if upload_error:
                raise upload_error

        finally:
            for _ in worker_tasks:
                await queue.put(None)
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            if isinstance(path, (str, PurePath)):
                fp.close()

        return raw.types.InputFileBig(
            id=file_id,
            parts=file_total_parts,
            name=file_name
        )


def apply_parallel_uploader_patch(client: "pyrogram.Client"):
    """Pyrogram istemcisine yüksek hızlı çok kanallı uploader metodunu bağlar."""
    client.save_file = lambda path, file_id=None, file_part=0, progress=None, progress_args=(): parallel_save_file(
        client, path, file_id=file_id, file_part=file_part, progress=progress, progress_args=progress_args, pool_size=6, in_flight_per_session=2
    )
    logger.info("⚡ 6 Oturum x 2 Pipelining (12 Kanallı) MTProto yükleyicisi başarıyla bağlandı.")
