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
import difflib
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
    def calculate_relevance(cls, query: str, title: str) -> float:
        """Arama sorgusu ile içerik başlığı arasındaki alaka skorunu (0-100) hesaplar."""
        tr_map = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")

        def _norm(s: str) -> str:
            if not s:
                return ""
            s = s.lower().strip()
            # Parantez/Köşeli parantez içlerini temizle: (2024), [DizipalX], (Türkçe Dublaj) vb.
            s = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', s)
            s = s.translate(tr_map)
            
            # Gürültü kelimeleri ve ekleri temizle
            noise = [
                'turkce dublaj', 'türkçe dublaj', 'turkce altyazili', 'türkçe altyazılı',
                'altyazili', 'altyazılı', 'dublaj', 'full hd izle', 'hd izle', 'full hd',
                'hd', 'izle', 'dizipal', 'dizibox', 'dizi', 'film', 'filmi', '1080p',
                '720p', '4k', 'uhd', 'tek parca', 'tek parça', 'son bolum', 'son bölüm',
                'tum bolumler', 'tüm bölümler', 'sezon', 'bolum', 'bölüm'
            ]
            for n in sorted(noise, key=len, reverse=True):
                s = re.sub(rf'\b{n}\b', ' ', s)

            s = re.sub(r"[^\w\s]", " ", s)
            return " ".join(s.split())

        q_clean = _norm(query)
        t_clean = _norm(title)

        if not q_clean or not t_clean:
            return 0.0

        if q_clean == t_clean:
            return 100.0

        # Başlık tam olarak sorgu ile başlıyor ve devamında sadece yıl veya boşluk varsa
        if t_clean.startswith(q_clean):
            rest = t_clean[len(q_clean):].strip()
            if not rest or rest.isdigit():
                return 95.0

        q_words = q_clean.split()
        t_words = t_clean.split()

        # Kısa sorgularda (1-2 kelime, örn: "FROM", "LOST", "YOU", "DARK")
        # Başlıkta yabancı/alakasız kelimeler olamaz (örn: "Tales from the Crypt", "Stranger Things" elenir)
        if len(q_words) <= 2:
            if q_clean == t_clean:
                return 100.0
            return 0.0

        # Çok kelimeli sorgularda (>= 3 kelime)
        if q_words == t_words:
            return 100.0

        seq_ratio = difflib.SequenceMatcher(None, q_clean, t_clean).ratio()
        if seq_ratio >= 0.85:
            return seq_ratio * 100.0

        return 0.0

    @classmethod
    def get_all_plugin_names(cls) -> List[str]:
        """DarkBox bünyesindeki tüm 50+ eklentiyi öncelik sırasına göre döndürür."""
        try:
            from Public.API.v1.Libs.local_plugins import _plugin_names
            all_names = list(_plugin_names())
        except Exception:
            all_names = []

        # En popüler / öncelikli dizi ve film eklentileri
        top_priority = [
            "Dizi65", "DizipalX", "RecTV", "Vizyona", "SineWix", 
            "FilmMakinesi", "FullHDFilmizlesene", "HDFilmCehennemi", "FilmModu", 
            "Hdizipal", "JetFilmIzle", "Selcukflix", "SetFilmizle", "WebteIzle",
            "Dizimom", "Dizimia", "Dizibal", "Dizibol", "Ddizi", "DiziKorea",
            "DiziIzleClick", "DizifilmLife", "FilmCenneti", "FilmIzleCH", "Filmhane",
            "FullHDFilmIzle", "HDFilmDelisi", "HDFilmUS", "RoketDizi", "SineMerkez",
            "Sinezy", "TvFilmIzle", "WFilmIzle", "WebDramaTurkey", "ZxcPrime", "Aether", "MeowTV"
        ]

        ignored_plugins = {"CanliTV", "Medya", "DarkTV", "M3uListem"}
        ordered = [p for p in top_priority if p in all_names and p not in ignored_plugins]
        for p in all_names:
            if p not in ordered and p not in ignored_plugins:
                ordered.append(p)

        return ordered if ordered else config.plugins_priority

    @classmethod
    async def search_all_plugins(cls, query: str) -> List[Dict[str, Any]]:
        """DarkBox eklentilerinde kontrollü, hızlı ve bellek korumalı paralel arama yapar."""
        plugins = cls.get_all_plugin_names()
        sem = asyncio.Semaphore(12)
        
        async def _search_plugin(p: str):
            async with sem:
                try:
                    res = await asyncio.wait_for(local_search(p, query), timeout=2.5)
                    out = []
                    for item in res:
                        title = item.get("title") if isinstance(item, dict) else (item.title if hasattr(item, "title") else str(item))
                        url = item.get("url") if isinstance(item, dict) else (item.url if hasattr(item, "url") else "")
                        poster = item.get("poster") if isinstance(item, dict) else (item.poster if hasattr(item, "poster") else "")
                        desc = item.get("description") if isinstance(item, dict) else (item.description if hasattr(item, "description") else "")
                        if title and url:
                            out.append({
                                "title": title,
                                "url": url,
                                "poster": poster,
                                "description": desc,
                                "plugin_name": p
                            })
                    return out
                except Exception as e:
                    logger.debug(f"Plugin {p} search error: {e}")
                    return []

        tasks = [asyncio.create_task(_search_plugin(p)) for p in plugins]
        done, pending = await asyncio.wait(tasks, timeout=4.5)
        for t in pending:
            t.cancel()
            
        results = [t.result() for t in done if not t.cancelled() and not t.exception()]
        
        flat = []
        seen = set()
        for r in results:
            if isinstance(r, list):
                for item in r:
                    key = (item["plugin_name"], item["url"])
                    if key not in seen:
                        seen.add(key)
                        score = cls.calculate_relevance(query, item["title"])
                        if score >= 30.0:
                            item["relevance_score"] = score
                            flat.append(item)

        # En yüksek alaka puanına ve eklenti önceliğine göre sırala
        def sort_key(item):
            score = item.get("relevance_score", 0.0)
            p_name = item.get("plugin_name", "")
            p_idx = plugins.index(p_name) if p_name in plugins else 999
            return (score, -p_idx)

        flat.sort(key=sort_key, reverse=True)
        return flat

    @classmethod
    async def find_all_candidate_streams(cls, query_title: str, target_s: int, target_e: int) -> List[Dict[str, Any]]:
        """DarkBox eklentilerinde arama yapar ve hedef sezon/bölüm (veya film) için tüm alternatif akışları toplar."""
        candidates = []
        results = await cls.search_all_plugins(query_title)
        if not results:
            return candidates

        matching_items = [
            item for item in results 
            if cls.calculate_relevance(query_title, item.get("title", "")) >= 65.0
        ]
        matching_items.sort(
            key=lambda x: cls.calculate_relevance(query_title, x.get("title", "")), 
            reverse=True
        )

        async def _process_item(item):
            p_name = item.get("plugin_name", "")
            i_url = item.get("url", "")
            cand_list = []
            try:
                detail = await asyncio.wait_for(local_load_item(p_name, i_url), timeout=8)
                if not detail:
                    return []
                episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                
                # Film Durumu (target_s == 0)
                if target_s == 0:
                    try:
                        links = await asyncio.wait_for(local_load_links(p_name, i_url), timeout=8)
                        for l in links:
                            link_name = l.get("name", "Akış") if isinstance(l, dict) else getattr(l, "name", "Akış")
                            link_url = l.get("url", "") if isinstance(l, dict) else getattr(l, "url", "")
                            if link_url:
                                cand_list.append({
                                    "plugin": p_name,
                                    "name": link_name,
                                    "url": link_url,
                                    "ep_url": i_url,
                                    "title": detail.get("title", "") or item.get("title", "")
                                })
                    except Exception:
                        pass

                    # Episodes içinde tekil film varsa onu da dene
                    if not cand_list and episodes:
                        for ep in episodes:
                            ep_url = ep.get("url", "") if isinstance(ep, dict) else getattr(ep, "url", "")
                            if ep_url:
                                try:
                                    links = await asyncio.wait_for(local_load_links(p_name, ep_url), timeout=8)
                                    for l in links:
                                        link_name = l.get("name", "Akış") if isinstance(l, dict) else getattr(l, "name", "Akış")
                                        link_url = l.get("url", "") if isinstance(l, dict) else getattr(l, "url", "")
                                        if link_url:
                                            cand_list.append({
                                                "plugin": p_name,
                                                "name": link_name,
                                                "url": link_url,
                                                "ep_url": ep_url,
                                                "title": detail.get("title", "") or item.get("title", "")
                                            })
                                except Exception:
                                    pass
                else:
                    # Dizi Durumu (Kesin Sezon / Bölüm Eşleştirme)
                    for ep in episodes:
                        s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                        e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                        ep_url = ep.get("url", "") if isinstance(ep, dict) else getattr(ep, "url", "")
                        ep_title = ep.get("title", "") if isinstance(ep, dict) else getattr(ep, "title", "")

                        if s_num == target_s and e_num == target_e and ep_url:
                            try:
                                links = await asyncio.wait_for(local_load_links(p_name, ep_url), timeout=8)
                                for l in links:
                                    link_name = l.get("name", "Akış") if isinstance(l, dict) else getattr(l, "name", "Akış")
                                    link_url = l.get("url", "") if isinstance(l, dict) else getattr(l, "url", "")
                                    if link_url:
                                        cand_list.append({
                                            "plugin": p_name,
                                            "name": link_name,
                                            "url": link_url,
                                            "ep_url": ep_url,
                                            "title": ep_title
                                        })
                            except Exception:
                                pass
            except Exception as ex:
                logger.debug(f"{p_name} link çözümleme atlandı: {ex}")
            return cand_list

        item_tasks = [_process_item(item) for item in matching_items]
        gathered = await asyncio.gather(*item_tasks, return_exceptions=True)
        for r in gathered:
            if isinstance(r, list):
                candidates.extend(r)

        all_plugins = cls.get_all_plugin_names()
        # Dublaj önceliği ve eklenti sırasına göre sırala
        def dublaj_priority_key(c):
            p = c.get("plugin", "")
            name = (c.get("name") or "").lower()
            title = (c.get("title") or "").lower()
            
            # Dublaj puanı (100 = Dublaj, 50 = Normal, 10 = Altyazı)
            score = 50
            if any(k in name or k in title for k in ["dublaj", "dub", "tr dub", "türkçe dublaj"]):
                score = 100
            elif any(k in name or k in title for k in ["altyazı", "sub", "eng", "orijinal"]):
                score = 10
                
            p_idx = all_plugins.index(p) if p in all_plugins else 999
            return (-score, p_idx)

        candidates.sort(key=dublaj_priority_key)
        return candidates

    @classmethod
    async def download_hls_stream(
        cls, 
        stream_url: str, 
        output_path: Path, 
        progress_cb: Optional[Callable[..., None]] = None
    ) -> bool:
        """HLS akışını video + Türkçe ses parçalarıyla tam senkronlu olarak indirir."""
        custom_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        referer_url = stream_url
        if stream_url.startswith("/proxy/video") or "proxy/video?url=" in stream_url:
            parsed_proxy = urllib.parse.urlparse(stream_url)
            qs = urllib.parse.parse_qs(parsed_proxy.query)
            if "url" in qs and qs["url"]:
                real_target = qs["url"][0]
                referer_url = qs.get("referer", [real_target])[0]
                if "user_agent" in qs and qs["user_agent"]:
                    custom_ua = qs["user_agent"][0]
                stream_url = real_target
            elif stream_url.startswith("/"):
                stream_url = f"http://127.0.0.1:3311{stream_url}"

        parsed_ref = urllib.parse.urlparse(referer_url)
        origin_header = f"{parsed_ref.scheme}://{parsed_ref.netloc}" if parsed_ref.netloc else ""
        headers = {
            "User-Agent": custom_ua,
            "Referer": referer_url
        }
        if origin_header:
            headers["Origin"] = origin_header

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
                # 1. Türkçe Dublaj Ses Akışı Tespiti
                has_audio_tags = any(l.startswith("#EXT-X-MEDIA:TYPE=AUDIO") for l in lines)
                turkish_audio_found = False
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
                                turkish_audio_found = True
                                break

                # Eğer çoklu ses etiketleri var ancak Türkçe dublaj yoksa (yalnızca orijinal/yabancı ses varsa)
                # Türkçe dublajlı alternatif kaynaklara geçebilmek için bu kaynağı atla
                if has_audio_tags and not turkish_audio_found:
                    logger.warning("Bu HLS kaynağında Türkçe Dublaj ses kanalı bulunamadı, sonraki dublajlı kaynağa geçiliyor...")
                    return False

                # 2. 720p / HD (< 2GB) Video Akışı Tespiti (Öncelikli 720p / Optimum Bitrate)
                variants = []
                for i, l in enumerate(lines):
                    if l.startswith("#EXT-X-STREAM-INF"):
                        bw_m = re.search(r"BANDWIDTH=(\d+)", l)
                        res_m = re.search(r"RESOLUTION=(\d+)x(\d+)", l)
                        name_m = re.search(r'NAME=["\']?(\d+)[pP]?["\']?', l)
                        bw = int(bw_m.group(1)) if bw_m else 0
                        h = int(res_m.group(2)) if res_m else (int(name_m.group(1)) if name_m else 0)
                        
                        # Bitrate'e göre yükseklik tahmini
                        if h == 0:
                            if 1000000 <= bw <= 2400000:
                                h = 720
                            elif bw > 2400000:
                                h = 1080
                            elif bw > 0:
                                h = 480

                        for next_idx in range(i + 1, min(i + 5, len(lines))):
                            if not lines[next_idx].startswith("#"):
                                v_url = urllib.parse.urljoin(stream_url, lines[next_idx])
                                variants.append((h, bw, v_url))
                                break

                if variants:
                    # 1. Tam 720p seç
                    pref_720 = [v for v in variants if v[0] == 720]
                    if pref_720:
                        pref_720.sort(key=lambda x: x[1])
                        video_target_url = pref_720[0][2]
                    else:
                        # 2. 480p - 720p arası HD seç
                        under_720 = [v for v in variants if 480 <= v[0] < 720]
                        if under_720:
                            under_720.sort(key=lambda x: x[0], reverse=True)
                            video_target_url = under_720[0][2]
                        else:
                            # 3. Yalnızca 1080p varsa en düşük bitrate'lisini seç (2GB'ı aşmaması için)
                            variants.sort(key=lambda x: x[1])
                            video_target_url = variants[0][2]

            if not video_target_url:
                video_target_url = stream_url

            # Segmentleri İndir
            async def get_segments(url: str) -> List[str]:
                try:
                    r = await session.get(url, headers=headers, timeout=10.0)
                    if r.status_code != 200:
                        return []
                    seg_urls = []
                    for ln in r.text.splitlines():
                        ln = ln.strip()
                        if ln and not ln.startswith("#"):
                            seg_urls.append(urllib.parse.urljoin(url, ln))
                    return seg_urls
                except Exception:
                    return []

            video_segs = await get_segments(video_target_url)
            audio_segs = await get_segments(audio_target_url) if audio_target_url else []

            if not video_segs:
                logger.warning("HLS video segmentleri bulunamadı.")
                return False

            tmp_video_file = output_path.with_suffix(".vraw.ts")
            tmp_audio_file = output_path.with_suffix(".araw.ts") if audio_segs else None

            async def download_seg_list(seg_list: List[str], dest_file: Path, is_video: bool = True):
                total = len(seg_list)
                if total == 0:
                    return

                batch_size = 15
                sem = asyncio.Semaphore(10)
                done = 0

                async def fetch_seg(idx: int, s_url: str):
                    nonlocal done
                    async with sem:
                        for retry in range(3):
                            try:
                                res = await session.get(s_url, headers=headers, timeout=8.0)
                                if res.status_code == 200 and len(res.content) > 0:
                                    done += 1
                                    if is_video and progress_cb and total > 0 and done % 10 == 0:
                                        try:
                                            progress_cb(done / total, done, total)
                                        except TypeError:
                                            progress_cb(done / total)
                                    return idx, res.content
                            except Exception:
                                await asyncio.sleep(0.5 + retry * 0.5)
                        return idx, b""

                with open(dest_file, "wb") as f_out:
                    for b_start in range(0, total, batch_size):
                        b_end = min(total, b_start + batch_size)
                        tasks = [fetch_seg(i, seg_list[i]) for i in range(b_start, b_end)]
                        batch_results = await asyncio.gather(*tasks)
                        batch_results.sort(key=lambda x: x[0])
                        for _, chunk in batch_results:
                            if chunk:
                                f_out.write(chunk)

            logger.info(f"HLS İndiriliyor: Video={len(video_segs)} parça, Ses={len(audio_segs)} parça...")
            await download_seg_list(video_segs, tmp_video_file, is_video=True)
            if audio_segs and tmp_audio_file:
                await download_seg_list(audio_segs, tmp_audio_file, is_video=False)

            if not tmp_video_file.exists() or tmp_video_file.stat().st_size < 1024 * 100:
                logger.warning("HLS video dosyası indirilemedi veya geçersiz boyutta.")
                if tmp_video_file.exists():
                    tmp_video_file.unlink(missing_ok=True)
                if tmp_audio_file and tmp_audio_file.exists():
                    tmp_audio_file.unlink(missing_ok=True)
                return False

            # FFmpeg ile Senkron Birleştirme (Muxing)
            is_audio_aac = False
            if tmp_audio_file and tmp_audio_file.exists() and tmp_audio_file.stat().st_size > 0:
                # Audio codec kontrolü
                try:
                    p_probe = await asyncio.create_subprocess_exec(
                        "ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1",
                        str(tmp_audio_file),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    out, _ = await p_probe.communicate()
                    if b"aac" in out.lower():
                        is_audio_aac = True
                except Exception:
                    pass

            if progress_cb:
                try:
                    progress_cb(1.0, len(video_segs), len(video_segs), phase="muxing")
                except TypeError:
                    try:
                        progress_cb(1.0)
                    except Exception:
                        pass

            # FFmpeg ile Ultra Hızlı & Kayıpsız Akış Kopyalama (Instant Lossless Muxing - 1-2 sn)
            cmd = [
                "ffmpeg", "-y",
                "-threads", "0",
                "-fflags", "+genpts+discardcorrupt",
                "-i", str(tmp_video_file)
            ]
            if tmp_audio_file and tmp_audio_file.exists() and tmp_audio_file.stat().st_size > 0:
                cmd.extend([
                    "-i", str(tmp_audio_file),
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-bsf:a", "aac_adtstoasc",
                    "-avoid_negative_ts", "make_zero",
                    "-shortest",
                    "-movflags", "+faststart",
                    str(output_path)
                ])
            else:
                cmd.extend([
                    "-map", "0:v:0",
                    "-map", "0:a:0?",
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    "-movflags", "+faststart",
                    str(output_path)
                ])

            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()

            # Geçici dosyaları temizle
            if tmp_video_file.exists():
                tmp_video_file.unlink(missing_ok=True)
            if tmp_audio_file and tmp_audio_file.exists():
                tmp_audio_file.unlink(missing_ok=True)

            if not (output_path.exists() and output_path.stat().st_size > 1024 * 1024):
                if err:
                    logger.error(f"FFmpeg birleştirme hatası: {err.decode('utf-8', errors='ignore')[-300:]}")
                return False
            return True

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

    @classmethod
    async def compress_video_to_limit(cls, input_path: Path, output_path: Path, target_mb: int = 1850) -> bool:
        """2GB sınırını aşan videoları Telegram limitine (<1.9GB) optimize eder."""
        try:
            probe_cmd = [
                "ffprobe", "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                str(input_path)
            ]
            proc = await asyncio.create_subprocess_exec(*probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await proc.communicate()
            try:
                duration = float(stdout.decode().strip())
            except Exception:
                duration = 7200.0

            if duration <= 0:
                duration = 7200.0

            target_total_bits = target_mb * 8 * 1024 * 1024
            target_v_bitrate = int((target_total_bits / duration) - 128000)
            target_v_bitrate = max(500000, target_v_bitrate)

            cmd = [
                "ffmpeg", "-y", "-i", str(input_path),
                "-c:v", "libx264", "-preset", "veryfast",
                "-b:v", str(target_v_bitrate),
                "-maxrate", str(int(target_v_bitrate * 1.3)),
                "-bufsize", str(int(target_v_bitrate * 2)),
                "-c:a", "copy",
                "-movflags", "+faststart",
                str(output_path)
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
            return output_path.exists() and output_path.stat().st_size > 1024 * 1024
        except Exception as e:
            logger.error(f"Video optimize hatası: {e}")
            return False
