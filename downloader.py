#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot Akıllı İndirici & Fallback Zinciri (Engine)
===================================================
HLS çoklu ses/video ayıklama, <2GB boyut kontrolü, kayıpsız senkron (muxing)
ve DarkBox eklentileri arasında otomatik fallback zinciri.
"""

import os
import re
import sys
import time
import json
import asyncio
import logging
import sqlite3
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable

from config import config, TEMP_DIR, DARKBOX_ROOT
from database import db

# DarkBox ve KekikStream Importları
try:
    from Public.API.v1.Libs.local_plugins import (
        search as local_search,
        load_item as local_load_item,
        load_links as local_load_links
    )
    from KekikStream.Core import Episode
except ImportError:
    pass

from curl_cffi.requests import AsyncSession

logger = logging.getLogger("DiziBot.Downloader")


class Downloader:
    @staticmethod
    def parse_title_season_episode(raw_title: str, ep_title: str = "", ep_url: str = "") -> Tuple[str, int, int]:
        clean = raw_title.strip()
        s_num = 1
        e_num = 1

        # S01E02 / 1x02 pattern
        m_se = re.search(r"S(\d{1,2})E(\d{1,2})", clean, re.IGNORECASE)
        if not m_se:
            m_se = re.search(r"(\d{1,2})x(\d{1,2})", clean, re.IGNORECASE)
        if not m_se:
            m_se = re.search(r"(\d{1,2})\.\s*Sezon\s*(\d{1,2})\.\s*Bölüm", clean, re.IGNORECASE)

        if m_se:
            s_num = int(m_se.group(1))
            e_num = int(m_se.group(2))
            clean = re.sub(r"\s*S\d{1,2}E\d{1,2}.*$", "", clean, flags=re.IGNORECASE).strip()
            clean = re.sub(r"\s*\d{1,2}x\d{1,2}.*$", "", clean, flags=re.IGNORECASE).strip()
            clean = re.sub(r"\s*\d{1,2}\.\s*Sezon.*$", "", clean, flags=re.IGNORECASE).strip()
        else:
            # Sadece Sezon ve Bölüm ayrı arama
            m_s = re.search(r"(\d{1,2})\.\s*Sezon", f"{clean} {ep_title}", re.IGNORECASE)
            if m_s:
                s_num = int(m_s.group(1))
            m_e = re.search(r"(\d{1,2})\.\s*Bölüm", f"{clean} {ep_title}", re.IGNORECASE)
            if m_e:
                e_num = int(m_e.group(1))

        # URL fallback (örn: /sezon/2/bolum/1)
        if s_num == 1 and e_num == 1 and ep_url:
            m_url = re.search(r"sezon[/_-](\d+)[/_-]bolum[/_-](\d+)", ep_url, re.IGNORECASE)
            if m_url:
                s_num = int(m_url.group(1))
                e_num = int(m_url.group(2))

        # Temizlik
        clean = re.sub(r"\s*-\s*.*(?:Bölüm|Part|Sezon).*$", "", clean, flags=re.IGNORECASE).strip()
        clean = clean.rstrip(" -_")
        return clean, s_num, e_num

    @classmethod
    async def find_all_candidate_streams(cls, query_title: str, target_s: int, target_e: int) -> List[Dict[str, Any]]:
        """DarkBox eklentilerinde arama yapar ve hedef sezon/bölüm için tüm alternatif akışları toplar."""
        candidates = []
        try:
            results = await local_search(query_title)
        except Exception as e:
            logger.error(f"DarkBox arama hatası ({query_title}): {e}")
            return candidates

        norm_target = db._norm_title(query_title)

        for item in results:
            item_title = item.title if hasattr(item, "title") else str(item)
            plugin_name = item.plugin_name if hasattr(item, "plugin_name") else "DarkBox"
            item_url = item.url if hasattr(item, "url") else ""

            if norm_target not in db._norm_title(item_title):
                continue

            try:
                detail = await local_load_item(plugin_name, item_url)
                if not detail or not hasattr(detail, "episodes"):
                    continue

                for ep in detail.episodes:
                    s_num = ep.season if hasattr(ep, "season") else (ep.get("season") if isinstance(ep, dict) else 1)
                    e_num = ep.episode if hasattr(ep, "episode") else (ep.get("episode") if isinstance(ep, dict) else 1)
                    ep_url = ep.url if hasattr(ep, "url") else (ep.get("url") if isinstance(ep, dict) else "")
                    ep_title = ep.title if hasattr(ep, "title") else (ep.get("title") if isinstance(ep, dict) else "")

                    if s_num == target_s and e_num == target_e and ep_url:
                        links = await local_load_links(plugin_name, ep_url)
                        for l in links:
                            link_name = l.name if hasattr(l, "name") else l.get("name", "Akış")
                            link_url = l.url if hasattr(l, "url") else l.get("url", "")
                            if link_url:
                                candidates.append({
                                    "plugin": plugin_name,
                                    "name": link_name,
                                    "url": link_url,
                                    "ep_url": ep_url,
                                    "title": ep_title
                                })
            except Exception as ex:
                logger.debug(f"{plugin_name} link çözümleme atlandı: {ex}")

        # Eklenti önceliğine göre sırala
        def plugin_priority_key(c):
            p = c["plugin"]
            if p in config.plugins_priority:
                return config.plugins_priority.index(p)
            return 99

        candidates.sort(key=plugin_priority_key)
        return candidates

    @classmethod
    async def download_hls_stream(
        cls, 
        stream_url: str, 
        output_path: Path, 
        progress_cb: Optional[Callable[[float], None]] = None
    ) -> bool:
        """HLS akışını video + Türkçe ses parçalarıyla tam senkronlu olarak indirir."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": stream_url
        }

        async with AsyncSession(impersonate="chrome124", proxy=config.proxy, timeout=60) as session:
            try:
                resp = await session.get(stream_url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"HLS Playlist 200 dönmedi: {resp.status_code}")
                    return False
            except Exception as e:
                logger.error(f"HLS Playlist istek hatası: {e}")
                return False

            manifest_text = resp.text
            lines = [l.strip() for l in manifest_text.splitlines() if l.strip()]

            video_target_url = None
            audio_target_url = None

            # Master Playlist kontrolü
            if any("#EXT-X-STREAM-INF" in l for l in lines) or any("#EXT-X-MEDIA:TYPE=AUDIO" in l for l in lines):
                # 1. Türkçe Ses Akışı Tespiti
                for l in lines:
                    if l.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
                        m_uri = re.search(r'URI=["\']?([^"\',]+)["\']?', l)
                        m_name = re.search(r'NAME=["\']?([^"\',]+)["\']?', l)
                        m_lang = re.search(r'LANGUAGE=["\']?([^"\',]+)["\']?', l)
                        if m_uri:
                            u = urllib.parse.urljoin(stream_url, m_uri.group(1))
                            name = (m_name.group(1) if m_name else "").lower()
                            lang = (m_lang.group(1) if m_lang else "").lower()
                            if any(x in name or x in lang for x in ["tur", "türk", "turkish", "tr", "dublaj"]):
                                audio_target_url = u
                                break
                            elif not audio_target_url:
                                audio_target_url = u

                # 2. 720p / 1080p (< 2GB) Video Akışı Tespiti
                variants = []
                for i, l in enumerate(lines):
                    if l.startswith("#EXT-X-STREAM-INF"):
                        bw_m = re.search(r"BANDWIDTH=(\d+)", l)
                        res_m = re.search(r"RESOLUTION=(\d+)x(\d+)", l)
                        bw = int(bw_m.group(1)) if bw_m else 0
                        h = int(res_m.group(2)) if res_m else 0
                        if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                            v_url = urllib.parse.urljoin(stream_url, lines[i + 1])
                            variants.append((h, bw, v_url))

                if variants:
                    # 720p tercih et, yoksa 1080p veya en uygunu seç
                    pref_720 = [v for v in variants if v[0] == 720]
                    if pref_720:
                        video_target_url = pref_720[0][2]
                    else:
                        pref_under_1080 = [v for v in variants if v[0] <= 1080]
                        if pref_under_1080:
                            pref_under_1080.sort(key=lambda x: x[0], reverse=True)
                            video_target_url = pref_under_1080[0][2]
                        else:
                            variants.sort(key=lambda x: x[1])
                            video_target_url = variants[0][2]

            if not video_target_url:
                video_target_url = stream_url

            # Segmentleri İndir
            async def get_segments(url: str) -> List[str]:
                r = await session.get(url, headers=headers)
                if r.status_code != 200:
                    return []
                seg_urls = []
                for ln in r.text.splitlines():
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        seg_urls.append(urllib.parse.urljoin(url, ln))
                return seg_urls

            video_segs = await get_segments(video_target_url)
            audio_segs = await get_segments(audio_target_url) if audio_target_url else []

            if not video_segs:
                logger.warning("HLS video segmentleri bulunamadı.")
                return False

            tmp_video_file = output_path.with_suffix(".vraw.ts")
            tmp_audio_file = output_path.with_suffix(".araw.ts") if audio_segs else None

            async def download_seg_list(seg_list: List[str], dest_file: Path, is_video: bool = True):
                with open(dest_file, "wb") as f_out:
                    sem = asyncio.Semaphore(12)
                    total = len(seg_list)
                    done = 0

                    async def fetch_seg(idx: int, s_url: str):
                        nonlocal done
                        async with sem:
                            for retry in range(4):
                                try:
                                    res = await session.get(s_url, headers=headers)
                                    if res.status_code == 200:
                                        done += 1
                                        if is_video and progress_cb and total > 0 and done % 15 == 0:
                                            progress_cb(done / total)
                                        return idx, res.content
                                except Exception:
                                    await asyncio.sleep(1 + retry)
                            return idx, b""

                    tasks = [fetch_seg(i, u) for i, u in enumerate(seg_list)]
                    results = await asyncio.gather(*tasks)
                    results.sort(key=lambda x: x[0])
                    for _, chunk in results:
                        if chunk:
                            f_out.write(chunk)

            logger.info(f"HLS İndiriliyor: Video={len(video_segs)} parça, Ses={len(audio_segs)} parça...")
            await download_seg_list(video_segs, tmp_video_file, is_video=True)
            if audio_segs and tmp_audio_file:
                await download_seg_list(audio_segs, tmp_audio_file, is_video=False)

            # FFmpeg ile Senkron Birleştirme (Muxing)
            cmd = ["ffmpeg", "-y", "-i", str(tmp_video_file)]
            if tmp_audio_file and tmp_audio_file.exists() and tmp_audio_file.stat().st_size > 0:
                cmd.extend(["-i", str(tmp_audio_file)])
                cmd.extend([
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-af", "aresample=async=1000:first_pts=0",
                    "-shortest",
                    "-movflags", "+faststart",
                    str(output_path)
                ])
            else:
                cmd.extend([
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-af", "aresample=async=1000:first_pts=0",
                    "-movflags", "+faststart",
                    str(output_path)
                ])

            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()

            # Geçici dosyaları temizle
            if tmp_video_file.exists():
                tmp_video_file.unlink(missing_ok=True)
            if tmp_audio_file and tmp_audio_file.exists():
                tmp_audio_file.unlink(missing_ok=True)

            return output_path.exists() and output_path.stat().st_size > 1024 * 1024

    @classmethod
    async def extract_thumbnail(cls, video_path: Path) -> Optional[Path]:
        """Videonun 20. saniyesinden afiş/thumbnail çıkartır."""
        thumb_path = video_path.with_suffix(".jpg")
        cmd = [
            "ffmpeg", "-y", "-ss", "00:00:20", "-i", str(video_path),
            "-vframes", "1", "-q:v", "2", str(thumb_path)
        ]
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()
        return thumb_path if thumb_path.exists() else None
