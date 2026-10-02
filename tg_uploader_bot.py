#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DarkBox Telegram MTProto Video Uploader Bot
===========================================
DarkBox eklentilerindeki içerikleri periyodik olarak kontrol eder veya
kullanıcının istediği film/diziyi DarkBox üzerindeki tüm eklentilerde arayıp
en uygun kalitedeki (2GB altı, orijinal ses ve mükemmel senkronlu) akışı indirerek
Telegram gizli grubuna (MTProto ile 2GB'a kadar) afiş, temiz başlık, sezon/bölüm detaylarıyla yükler.
"""

import os
import sys
import re
import time
import json
import asyncio
import logging
import sqlite3
import shutil
import urllib.parse
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

# DarkBox kök dizinini ekle
DARKBOX_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DARKBOX_ROOT))

from KekikStream.Core import PluginBase, MainPageResult, MovieInfo, SeriesInfo, Episode, ExtractResult
from Public.API.v1.Libs.local_plugins import LocalProviderClient, list_plugin_names, get_plugin, search as local_search, load_item as local_load_item, load_links as local_load_links

try:
    from pyrogram import Client
    from pyrogram.types import Message
except ImportError:
    print("HATA: Pyrogram kütüphanesi bulunamadı. Lütfen 'pip install pyrogram tgcrypto' çalıştırın.")
    sys.exit(1)

# Loglama ayarları
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("TGUploader")

# Yapılandırma Yolları
CONFIG_FILE = Path("/etc/darkbox/tg_uploader.env")
if not CONFIG_FILE.exists():
    CONFIG_FILE = DARKBOX_ROOT / "tools" / "tg_uploader.env"

DB_FILE = DARKBOX_ROOT / "data" / "tg_uploader.db"
TEMP_DIR = DARKBOX_ROOT / "data" / "tg_uploader_temp"
SESSION_DIR = DARKBOX_ROOT / "data"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)

MAX_ALLOWED_FILE_SIZE_BYTES = int(1950 * 1024 * 1024)  # 1950 MB (2GB Telegram sınırının hemen altı)

CORE_SEARCH_PLUGINS = [
    "DizipalX", "SineWix", "Vizyona", "Hdizipal", "Dizimom", "RecTV", 
    "FilmIzleCH", "FullHDFilmizlesene", "FilmMakinesi", "HDFilmCehennemi", 
    "Selcukflix", "JetFilmIzle", "Medya", "DarkTV", "SuperTV", "NetTV"
]


class Config:
    def __init__(self):
        self.bot_token: str = ""
        self.api_id: int = 2040
        self.api_hash: str = "b18441a1ff607e10a989891a5462e627"
        self.session_string: str = ""
        self.target_chat_id: int = -1001909587016
        self.check_interval: int = 900  # 15 dakika
        self.max_file_size_gb: float = 2.0
        self.plugins_to_watch: List[str] = ["DizipalX", "Vizyona"]
        self.load()

    def load(self):
        env_vars = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip("'\"")

        # Ortam değişkenleri veya config dosyası
        self.bot_token = os.environ.get("TG_BOT_TOKEN", env_vars.get("TG_BOT_TOKEN", ""))
        self.api_id = int(os.environ.get("TG_API_ID", env_vars.get("TG_API_ID", 2040)))
        self.api_hash = os.environ.get("TG_API_HASH", env_vars.get("TG_API_HASH", "b18441a1ff607e10a989891a5462e627"))
        self.session_string = os.environ.get("TG_SESSION_STRING", env_vars.get("TG_SESSION_STRING", ""))
        
        chat_raw = os.environ.get("TG_TARGET_CHAT_ID", env_vars.get("TG_TARGET_CHAT_ID", "1001909587016"))
        try:
            chat_id = int(chat_raw)
            if chat_id > 0 and str(chat_id).startswith("100"):
                self.target_chat_id = -chat_id
            elif chat_id > 0:
                self.target_chat_id = -int(f"100{chat_id}")
            else:
                self.target_chat_id = chat_id
        except ValueError:
            self.target_chat_id = -1001909587016

        self.check_interval = int(os.environ.get("CHECK_INTERVAL", env_vars.get("CHECK_INTERVAL", 900)))
        self.max_file_size_gb = float(os.environ.get("MAX_FILE_SIZE_GB", env_vars.get("MAX_FILE_SIZE_GB", 2.0)))
        
        plugins_str = os.environ.get("PLUGINS", env_vars.get("PLUGINS", "DizipalX,Vizyona"))
        self.plugins_to_watch = [p.strip() for p in plugins_str.split(",") if p.strip()]


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin TEXT NOT NULL,
                    item_url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    season INTEGER DEFAULT 0,
                    episode INTEGER DEFAULT 0,
                    file_size INTEGER DEFAULT 0,
                    tg_message_id INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    uploaded_at TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_title TEXT UNIQUE NOT NULL,
                    topic_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_uploads_url ON uploads(item_url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_uploads_status ON uploads(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_topics_title ON topics(series_title)")

    def _norm_title(self, title: str) -> str:
        t = re.sub(r"[^\w\s]", "", title.strip().lower(), flags=re.UNICODE)
        return re.sub(r"\s+", " ", t).strip()

    def get_topic_id(self, series_title: str) -> Optional[int]:
        norm = self._norm_title(series_title)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT topic_id FROM topics WHERE series_title = ? OR ? LIKE '%' || series_title || '%' OR series_title LIKE '%' || ? || '%'", (norm, norm, norm))
            row = cur.fetchone()
            return row[0] if row else None

    def save_topic_id(self, series_title: str, topic_id: int):
        norm = self._norm_title(series_title)
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO topics (series_title, topic_id)
                VALUES (?, ?)
                ON CONFLICT(series_title) DO UPDATE SET topic_id = excluded.topic_id
            """, (norm, topic_id))

    def is_processed(self, item_url: str) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM uploads WHERE item_url = ? AND status = 'uploaded'", (item_url,))
            return cur.fetchone() is not None

    def add_or_update_record(self, plugin: str, item_url: str, title: str, season: int = 0, episode: int = 0, status: str = "pending") -> int:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO uploads (plugin, item_url, title, season, episode, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_url) DO UPDATE SET
                    status = excluded.status,
                    error_message = NULL
            """, (plugin, item_url, title, season, episode, status))
            return cur.lastrowid

    def mark_uploaded(self, item_url: str, file_size: int, tg_message_id: int):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE uploads 
                SET status = 'uploaded', 
                    file_size = ?, 
                    tg_message_id = ?, 
                    uploaded_at = CURRENT_TIMESTAMP 
                WHERE item_url = ?
            """, (file_size, tg_message_id, item_url))

    def mark_failed(self, item_url: str, error_message: str):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE uploads 
                SET status = 'failed', 
                    error_message = ? 
                WHERE item_url = ?
            """, (error_message, item_url))


class MediaDownloader:
    """FFmpeg ve curl tabanlı yüksek performanslı video & thumbnail indirici."""

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        name = re.sub(r'[\\/*?:"<>|]', "", name)
        return re.sub(r"\s+", " ", name).strip(" ._")

    @classmethod
    def parse_title_season_episode(cls, raw_title: str, url: str) -> Tuple[str, int, int]:
        """Başlık ve URL'den içerik adını, sezon ve bölüm numaralarını ayıklar."""
        clean_title = raw_title.strip()
        
        # SEO / Fazlalık temizliği
        clean_title = re.sub(r"\s+bölümleri\s+hd\s+izle.*$", "", clean_title, flags=re.IGNORECASE).strip()
        clean_title = re.sub(r"\s+full\s+hd(?:\s+izle)?.*$", "", clean_title, flags=re.IGNORECASE).strip()
        clean_title = re.sub(r"\s+-\s+Dizipal\s+Güncel.*$", "", clean_title, flags=re.IGNORECASE).strip()
        clean_title = re.sub(r"\s+hd\s+izle.*$", "", clean_title, flags=re.IGNORECASE).strip()

        season = 0
        episode = 0

        # Başlıktan Sezon/Bölüm (örn: "Dizi Adı 1. Sezon 5. Bölüm")
        m_se = re.search(r"(\d+)\.\s*Sezon\s*(\d+)\.\s*Bölüm", clean_title, re.IGNORECASE)
        if m_se:
            season = int(m_se.group(1))
            episode = int(m_se.group(2))
            clean_title = re.sub(r"\s*\d+\.\s*Sezon\s*\d+\.\s*Bölüm.*$", "", clean_title, flags=re.IGNORECASE).strip()

        if season == 0 or episode == 0:
            m_sxe = re.search(r"\bS(\d+)\s*E(\d+)\b", clean_title, re.IGNORECASE) or re.search(r"\b(\d+)x(\d+)\b", clean_title)
            if m_sxe:
                season = int(m_sxe.group(1))
                episode = int(m_sxe.group(2))
                clean_title = re.sub(r"\s*(?:S\d+\s*E\d+|\d+x\d+).*$", "", clean_title, flags=re.IGNORECASE).strip()

        # URL'den Sezon/Bölüm (örn: /sezon-1/bolum-4 veya -1-sezon-4-bolum veya -1x04)
        if season == 0 or episode == 0:
            m_url_se = re.search(r"[/-](\d+)[-_]sezon[-_](\d+)[-_]bolum", url, re.IGNORECASE) or \
                       re.search(r"/sezon-(\d+)/bolum-(\d+)", url, re.IGNORECASE) or \
                       re.search(r"-(\d+)x(\d+)(?:/|$)", url, re.IGNORECASE)
            if m_url_se:
                season = int(m_url_se.group(1))
                episode = int(m_url_se.group(2))

        clean_title = cls._sanitize_filename(clean_title)
        return clean_title, season, episode

    @staticmethod
    async def get_video_info(file_path: Path) -> Dict[str, Any]:
        """ffprobe ile video ve ses akış bilgilerini alır."""
        cmd = [
            "ffprobe", "-v", "error",
            "-show_streams",
            "-show_entries", "stream=codec_type,codec_name,width,height,duration:format=duration",
            "-of", "json",
            str(file_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        try:
            data = json.loads(stdout.decode())
            streams = data.get("streams", [])
            format_data = data.get("format", {})
            
            v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
            has_audio = any(s.get("codec_type") == "audio" for s in streams)
            
            width = int(v_stream.get("width", 1280)) if v_stream else 1280
            height = int(v_stream.get("height", 720)) if v_stream else 720
            
            duration_s = v_stream.get("duration") or format_data.get("duration") or 0
            duration = int(float(duration_s))
            return {"width": width, "height": height, "duration": duration, "has_audio": has_audio}
        except Exception as e:
            logger.warning(f"Video metadata alınamadı ({file_path}): {e}")
            return {"width": 1280, "height": 720, "duration": 0, "has_audio": True}

    @staticmethod
    async def extract_thumbnail(video_path: Path, thumb_path: Path, at_second: int = 15) -> bool:
        """Video içerisinden yüksek kaliteli kapak resmi üretir."""
        cmd = [
            "ffmpeg", "-y", "-ss", str(at_second),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(thumb_path)
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return thumb_path.exists() and thumb_path.stat().st_size > 0

    @classmethod
    async def resolve_sub_highest_stream(cls, stream_url: str, referer: str = "", max_bitrate_bps: int = 4000000) -> str:
        """Eğer verilen URL bir master m3u8 ise, 2GB limitini aşmayacak en yüksek kalite varyantını seçer."""
        from curl_cffi.requests import AsyncSession
        if not (".m3u8" in stream_url or "/stream/" in stream_url or "master" in stream_url or "playlist" in stream_url):
            return stream_url

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        if referer:
            headers["Referer"] = referer

        try:
            async with AsyncSession(headers=headers, impersonate="chrome124", timeout=12) as client:
                resp = await client.get(stream_url)
                if resp.status_code == 200 and "#EXT-X-STREAM-INF" in resp.text:
                    lines = resp.text.splitlines()
                    variants = []
                    cur_bw = 0
                    cur_res = ""
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("#EXT-X-STREAM-INF:"):
                            bw_m = re.search(r"BANDWIDTH=(\d+)", line)
                            res_m = re.search(r"RESOLUTION=(\d+x\d+)", line)
                            cur_bw = int(bw_m.group(1)) if bw_m else 0
                            cur_res = res_m.group(1) if res_m else ""
                        elif not line.startswith("#"):
                            sub_url = urllib.parse.urljoin(stream_url, line)
                            variants.append({"url": sub_url, "bandwidth": cur_bw, "resolution": cur_res})
                            cur_bw = 0
                            cur_res = ""

                    if variants:
                        # Bitrate'e göre azalan sırada sırala
                        variants.sort(key=lambda x: x["bandwidth"], reverse=True)
                        # 2GB'a sığacak (örn. 720p / 1080p dengeli) varyantı seç
                        chosen = variants[0]
                        for v in variants:
                            # 3500 kbps ve altı 720p/1080p standart 1-2 saatlik içerikler için < 1.8 GB garantilidir
                            if v["bandwidth"] <= max_bitrate_bps or len(variants) == 1:
                                chosen = v
                                break
                        logger.info(f"HLS Master varyantı seçildi: Res: {chosen['resolution']} | Bitrate: {chosen['bandwidth']} bps")
                        return chosen["url"]
        except Exception as e:
            logger.debug(f"Master m3u8 varyant çözme atlandı: {e}")

        return stream_url

    @classmethod
    async def download_hls_async(cls, m3u8_url: str, referer: str, output_path: Path, meta_title: str = "", season: int = 0, episode: int = 0, max_concurrency: int = 25) -> bool:
        """
        HLS segmentlerini (video ve varsa ayrı Türkçe ses akışını) tam eksiksiz indirir,
        FFmpeg ve aresample filtresi ile ses senkronunu milisaniyesine kadar kilitler.
        2GB üstü ise işlemi hemen durdurup False döner.
        """
        from curl_cffi.requests import AsyncSession
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": referer or "https://vidmixi.com/"
        }
        
        target_m3u8 = m3u8_url
        try:
            async with AsyncSession(proxy="socks5://127.0.0.1:4000", headers=headers, impersonate="chrome124", timeout=20) as client:
                r = await client.get(target_m3u8)
                if r.status_code != 200:
                    async with AsyncSession(headers=headers, impersonate="chrome124", timeout=20) as direct_client:
                        r = await direct_client.get(target_m3u8)
                        if r.status_code != 200:
                            return False
                    
                master_lines = r.text.splitlines()
                audio_target_url = None
                
                # Ayrı Ses Akışı (Türkçe ses) tespiti
                for l in master_lines:
                    if l.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
                        m_uri = re.search(r'URI="([^"]+)"', l)
                        m_name = re.search(r'NAME="([^"]+)"', l)
                        m_lang = re.search(r'LANGUAGE="([^"]+)"', l)
                        if m_uri:
                            u = urllib.parse.urljoin(target_m3u8, m_uri.group(1))
                            name = (m_name.group(1) if m_name else "").lower()
                            lang = (m_lang.group(1) if m_lang else "").lower()
                            is_tr = any(x in name or x in lang for x in ["tur", "türk", "turkish", "tr", "dublaj"])
                            if is_tr or not audio_target_url or "DEFAULT=YES" in l:
                                audio_target_url = u
                                if is_tr:
                                    break

                # Master manifest varyant seçimi (2GB sınırına uygun 720p/1080p dengeli)
                lines = master_lines
                if "#EXT-X-STREAM-INF" in r.text:
                    variants = []
                    cur_bw = 0
                    for l in lines:
                        l = l.strip()
                        if l.startswith("#EXT-X-STREAM-INF:"):
                            bw_m = re.search(r"BANDWIDTH=(\d+)", l)
                            cur_bw = int(bw_m.group(1)) if bw_m else 0
                        elif not l.startswith("#") and l:
                            variants.append({"url": urllib.parse.urljoin(target_m3u8, l), "bw": cur_bw})
                            cur_bw = 0
                    if variants:
                        variants.sort(key=lambda x: x["bw"], reverse=True)
                        # Bitrate ~3.5 Mbps altı olanı (veya en düşüğün bir üstünü) seç
                        chosen_variant = variants[-1]
                        for v in variants:
                            if v["bw"] <= 3500000:
                                chosen_variant = v
                                break
                        target_m3u8 = chosen_variant["url"]
                        r_sub = await client.get(target_m3u8)
                        lines = r_sub.text.splitlines()

                segment_urls = []
                for l in lines:
                    l = l.strip()
                    if l and not l.startswith("#"):
                        segment_urls.append(urllib.parse.urljoin(target_m3u8, l))

                if not segment_urls:
                    return False

                # Ses segmentleri (Eğer master'da ayrı ses akışı tanımlıysa)
                audio_segment_urls = []
                if audio_target_url:
                    try:
                        r_aud = await client.get(audio_target_url)
                        if r_aud.status_code == 200:
                            for l in r_aud.text.splitlines():
                                l = l.strip()
                                if l and not l.startswith("#"):
                                    audio_segment_urls.append(urllib.parse.urljoin(audio_target_url, l))
                    except Exception as e:
                        logger.warning(f"Ayrı ses akışı segmentleri alınamadı: {e}")

                logger.info(f"HLS segmentleri indiriliyor: Video={len(segment_urls)} parça, Ses={len(audio_segment_urls)} parça...")
                temp_ts_file = output_path.with_suffix(".temp_v.ts")
                temp_aud_file = output_path.with_suffix(".temp_a.ts") if audio_segment_urls else None
                sem = asyncio.Semaphore(max_concurrency)

                # Segmentleri eksiksiz indirme garantisi (Parça atlamadan)
                async def download_segments_strict(urls: List[str], dest_file: Path) -> bool:
                    results = [None] * len(urls)
                    async def fetch_seg(idx, seg_url):
                        async with sem:
                            for attempt in range(6):
                                try:
                                    resp = await client.get(seg_url, timeout=20)
                                    if resp.status_code == 200 and len(resp.content) > 10:
                                        results[idx] = resp.content
                                        return
                                except Exception:
                                    pass
                                await asyncio.sleep(0.4)
                            logger.warning(f"Segment indirilemedi (idx {idx}): {seg_url}")

                    chunk_size = 40
                    with open(dest_file, "wb") as f_out:
                        for i in range(0, len(urls), chunk_size):
                            chunk = urls[i:i + chunk_size]
                            tasks = [fetch_seg(i + j, url) for j, url in enumerate(chunk)]
                            await asyncio.gather(*tasks)
                            
                            for j in range(len(chunk)):
                                data = results[i + j]
                                if data:
                                    f_out.write(data)
                                    results[i + j] = None
                                else:
                                    logger.error(f"Eksik parça tespit edildi ({i+j}). Akış tutarsız!")
                                    return False
                            
                            # İndirme anında 2GB sınır kontrolü
                            if dest_file.stat().st_size > MAX_ALLOWED_FILE_SIZE_BYTES:
                                logger.warning(f"İndirme esnasında 2GB limiti aşıldı ({dest_file.stat().st_size / (1024*1024):.1f} MB). İndirme durduruluyor...")
                                return False
                    return True

                # Video TS indir
                v_ok = await download_segments_strict(segment_urls, temp_ts_file)
                if not v_ok:
                    if temp_ts_file.exists(): temp_ts_file.unlink()
                    return False

                # Ses TS indir (Varsa)
                if temp_aud_file and audio_segment_urls:
                    a_ok = await download_segments_strict(audio_segment_urls, temp_aud_file)
                    if not a_ok:
                        if temp_ts_file.exists(): temp_ts_file.unlink()
                        if temp_aud_file.exists(): temp_aud_file.unlink()
                        return False

            # FFmpeg ile MP4'e mux et (Ses senkronu garantili: aresample async=1000)
            meta_args = []
            if meta_title: meta_args += ["-metadata", f"title={meta_title}"]
            if season > 0: meta_args += ["-metadata", f"season_number={season}"]
            if episode > 0: meta_args += ["-metadata", f"episode_sort={episode}"]

            if temp_aud_file and temp_aud_file.exists() and temp_aud_file.stat().st_size > 1024:
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(temp_ts_file),
                    "-i", str(temp_aud_file),
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-af", "aresample=async=1000:first_pts=0",
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-shortest",
                    "-movflags", "+faststart"
                ] + meta_args + [str(output_path)]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(temp_ts_file),
                    "-c", "copy",
                    "-bsf:a", "aac_adtstoasc",
                    "-movflags", "+faststart"
                ] + meta_args + [str(output_path)]

            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()

            if temp_ts_file.exists():
                try: temp_ts_file.unlink()
                except Exception: pass
            if temp_aud_file and temp_aud_file.exists():
                try: temp_aud_file.unlink()
                except Exception: pass

            if output_path.exists() and output_path.stat().st_size > 1024 * 100:
                final_size = output_path.stat().st_size
                if final_size > MAX_ALLOWED_FILE_SIZE_BYTES:
                    logger.warning(f"Üretilen MP4 2GB sınırını aşıyor ({final_size / (1024*1024):.1f} MB). Dosya siliniyor...")
                    output_path.unlink()
                    return False
                
                size_mb = final_size / (1024 * 1024)
                logger.info(f"HLS indirme ve senkronizasyon başarılı: {output_path.name} ({size_mb:.2f} MB)")
                return True
        except Exception as e:
            logger.warning(f"Async HLS indirme hatası: {e}")

        return False

    @classmethod
    async def download_stream(cls, stream_url: str, referer: str, output_path: Path, meta_title: str = "", season: int = 0, episode: int = 0) -> bool:
        """HLS veya direct MP4 akışını diske kaydeder ve metadata işler."""
        logger.info(f"Video akışı deneniyor -> {output_path.name}...")
        
        actual_url = stream_url
        actual_referer = referer or ""
        
        if "/proxy/video" in stream_url:
            parsed = urllib.parse.urlparse(stream_url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "url" in qs and qs["url"]:
                actual_url = qs["url"][0]
            if "referer" in qs and qs["referer"]:
                actual_referer = qs["referer"][0]

        # Vidmixi veya HLS içerikleri için hızlı async WARP downloader
        if "vidmixi.com" in actual_url or ".m3u8" in actual_url or "/stream/" in actual_url:
            success = await cls.download_hls_async(actual_url, actual_referer, output_path, meta_title, season, episode)
            if success:
                return True

        # Fallback: Standart FFmpeg ile HLS/MP4 indirme
        actual_url = await cls.resolve_sub_highest_stream(actual_url, actual_referer)

        headers_str = f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36\r\n"
        if actual_referer:
            headers_str += f"Referer: {actual_referer}\r\n"

        meta_args = []
        if meta_title: meta_args += ["-metadata", f"title={meta_title}"]
        if season > 0: meta_args += ["-metadata", f"season_number={season}"]
        if episode > 0: meta_args += ["-metadata", f"episode_sort={episode}"]

        cmd = [
            "ffmpeg", "-y",
            "-headers", headers_str,
            "-i", actual_url,
            "-c", "copy",
            "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart"
        ] + meta_args + [str(output_path)]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        if output_path.exists() and output_path.stat().st_size > 1024 * 100:
            final_size = output_path.stat().st_size
            if final_size > MAX_ALLOWED_FILE_SIZE_BYTES:
                logger.warning(f"Dosya 2GB limitini aşıyor ({final_size / (1024*1024):.1f} MB). Siliniyor...")
                output_path.unlink()
                return False

            size_mb = final_size / (1024 * 1024)
            logger.info(f"İndirme tamamlandı: {output_path.name} ({size_mb:.2f} MB)")
            return True
        else:
            return False


class DarkBoxUploaderBot:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(DB_FILE)
        self.client: Optional[Client] = None
        self.provider_client = LocalProviderClient()

    def _get_client(self) -> Client:
        if self.client is None:
            session_name = "darkbox_uploader"
            if self.config.bot_token:
                self.client = Client(
                    name=session_name,
                    api_id=self.config.api_id,
                    api_hash=self.config.api_hash,
                    bot_token=self.config.bot_token,
                    workdir=str(SESSION_DIR)
                )
            elif self.config.session_string:
                self.client = Client(
                    name=session_name,
                    api_id=self.config.api_id,
                    api_hash=self.config.api_hash,
                    session_string=self.config.session_string,
                    workdir=str(SESSION_DIR)
                )
            else:
                self.client = Client(
                    name=session_name,
                    api_id=self.config.api_id,
                    api_hash=self.config.api_hash,
                    workdir=str(SESSION_DIR)
                )
        return self.client

    async def get_or_create_series_topic(self, series_name: str) -> Optional[int]:
        """Dizi veya Film için grupta forum konusu/kategorisi açar veya mevcut olanı döndürür."""
        clean_name = series_name.strip()
        if not clean_name:
            return None

        # 1. DB'den kontrol et
        topic_id = self.db.get_topic_id(clean_name)
        if topic_id:
            return topic_id

        # 2. Telegram Bot API ile konu aç
        if not self.config.bot_token:
            return None

        import httpx
        url = f"https://api.telegram.org/bot{self.config.bot_token}/createForumTopic"
        payload = {
            "chat_id": self.config.target_chat_id,
            "name": f"🎬 {clean_name}" if clean_name != "Filmler" else "🍿 Filmler"
        }
        try:
            async with httpx.AsyncClient(timeout=15) as http_client:
                resp = await http_client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        tid = data["result"]["message_thread_id"]
                        self.db.save_topic_id(clean_name, tid)
                        logger.info(f"Dizi için yeni Telegram Konusu oluşturuldu: '{clean_name}' (ID: {tid})")
                        return tid
                else:
                    logger.warning(f"Telegram Forum Konusu oluşturulamadı: {resp.text}")
        except Exception as e:
            logger.warning(f"Forum konusu oluşturma hatası: {e}")

        return None

    async def search_content_across_darkbox(self, query: str) -> List[Dict[str, Any]]:
        """DarkBox üzerindeki tüm eklentilerde içerik araması yapar."""
        clean_q = query.strip()
        if not clean_q:
            return []

        logger.info(f"DarkBox genelinde '{clean_q}' aranıyor...")
        
        async def search_one(plugin_name: str):
            try:
                res = await asyncio.wait_for(local_search(plugin_name, clean_q), timeout=6.0)
                if isinstance(res, list) and res:
                    return [{"plugin": plugin_name, **item} for item in res]
            except Exception:
                pass
            return []

        tasks = [search_one(p) for p in CORE_SEARCH_PLUGINS]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_results = []
        for res in results_nested:
            if isinstance(res, list):
                all_results.extend(res)

        logger.info(f"DarkBox arama sonucu: {len(all_results)} eşleşme bulundu.")
        return all_results

    async def find_sources_for_episode(self, series_title: str, season: int, episode: int, known_plugin: str = "", known_url: str = "") -> List[Tuple[str, ExtractResult]]:
        """Belirtilen dizi ve bölüm için DarkBox'taki tüm eklentilerden oynatılabilir kaynakları toplar."""
        sources: List[Tuple[str, ExtractResult]] = []

        # 1. Önceden bilinen URL varsa önce onu dene
        if known_plugin and known_url:
            try:
                links = await local_load_links(known_plugin, known_url)
                for l in links:
                    sources.append((known_plugin, l))
            except Exception as e:
                logger.debug(f"[{known_plugin}] load_links hatası: {e}")

        # 2. DarkBox genelinde ara ve diğer eklentilerin bölümlerini keşfet
        matched_items = await self.search_content_across_darkbox(series_title)
        
        for item in matched_items:
            p_name = item.get("plugin")
            i_url = item.get("url")
            if not p_name or not i_url or (p_name == known_plugin and i_url == known_url):
                continue

            try:
                detail = await asyncio.wait_for(local_load_item(p_name, i_url), timeout=7.0)
                if isinstance(detail, dict) and "episodes" in detail:
                    ep_list = detail.get("episodes", [])
                    for ep in ep_list:
                        ep_s = ep.get("season", 0)
                        ep_e = ep.get("episode", 0)
                        if ep_s == season and ep_e == episode:
                            ep_url = ep.get("url")
                            if ep_url:
                                links = await local_load_links(p_name, ep_url)
                                for l in links:
                                    sources.append((p_name, l))
            except Exception:
                continue

        logger.info(f"S{season:02d}E{episode:02d} için DarkBox genelinde toplam {len(sources)} kaynak bulundu.")
        return sources

    async def upload_video_file(self, file_path: Path, display_filename: str, caption: str, thumb_path: Optional[Path] = None, topic_id: Optional[int] = None) -> Optional[Message]:
        """Videoyu MTProto üzerinden Telegram grubuna (veya ilgili dizi konusuna) yükler."""
        client = self._get_client()
        meta = await MediaDownloader.get_video_info(file_path)
        
        last_log_time = 0
        def progress_callback(current, total):
            nonlocal last_log_time
            now = time.time()
            if now - last_log_time >= 5 or current == total:
                percent = (current / total) * 100 if total > 0 else 0
                mb_curr = current / (1024 * 1024)
                mb_tot = total / (1024 * 1024)
                logger.info(f"Yükleme İlerlemesi: %{percent:.1f} ({mb_curr:.1f}/{mb_tot:.1f} MB)")
                last_log_time = now

        logger.info(f"Telegram'a yükleniyor -> Chat: {self.config.target_chat_id} (Konu ID: {topic_id or 'Genel'}) [{display_filename}]")
        
        send_kwargs = {
            "chat_id": self.config.target_chat_id,
            "video": str(file_path),
            "file_name": display_filename,
            "caption": caption,
            "duration": meta.get("duration", 0),
            "width": meta.get("width", 1280),
            "height": meta.get("height", 720),
            "thumb": str(thumb_path) if thumb_path and thumb_path.exists() else None,
            "supports_streaming": True,
            "progress": progress_callback
        }
        if topic_id:
            send_kwargs["reply_to_message_id"] = topic_id

        msg = await client.send_video(**send_kwargs)
        logger.info(f"Yükleme başarılı! Mesaj ID: {msg.id}")
        return msg

    async def process_item(self, plugin_name: str, item_url: str, raw_title: str, season: int = 0, episode: int = 0) -> bool:
        """Tek bir içeriği DarkBox genelinde en uygun kalitede (<2GB, senkronlu) bulup Telegram'a yükler."""
        if self.db.is_processed(item_url):
            logger.info(f"Zaten yüklendi, atlanıyor: [{plugin_name}] {raw_title}")
            return True

        clean_title, s_num, e_num = MediaDownloader.parse_title_season_episode(raw_title, item_url)
        if season > 0: s_num = season
        if episode > 0: e_num = episode

        display_name = f"{clean_title} S{s_num:02d}E{e_num:02d}" if (s_num > 0 and e_num > 0) else clean_title
        logger.info(f"\nİşleniyor: {display_name} (Başlangıç: [{plugin_name}] {item_url})")
        self.db.add_or_update_record(plugin_name, item_url, display_name, s_num, e_num, status="downloading")

        # 1. DarkBox genelinde kaynakları topla
        if s_num > 0 and e_num > 0:
            sources = await self.find_sources_for_episode(clean_title, s_num, e_num, known_plugin=plugin_name, known_url=item_url)
        else:
            # Tekil film için doğrudan linkleri çöz
            sources = []
            try:
                links = await local_load_links(plugin_name, item_url)
                for l in links: sources.append((plugin_name, l))
            except Exception: pass
            
            # Bulunamazsa DarkBox genelinde ara
            if not sources:
                matched = await self.search_content_across_darkbox(clean_title)
                for m in matched:
                    p = m.get("plugin")
                    u = m.get("url")
                    if p and u:
                        try:
                            lks = await local_load_links(p, u)
                            for l in lks: sources.append((p, l))
                        except Exception: pass

        if not sources:
            logger.warning(f"Oynatılabilir kaynak bulunamadı: {display_name}")
            self.db.mark_failed(item_url, "Kaynak bulunamadı")
            return False

        # Dosya adı ve başlık formatı
        safe_base = MediaDownloader._sanitize_filename(clean_title)
        if s_num > 0 and e_num > 0:
            display_filename = f"{safe_base} - S{s_num:02d}E{e_num:02d}.mp4"
            caption = (
                f"🎬 **{clean_title}**\n"
                f"📌 **{s_num}. Sezon {e_num}. Bölüm** (`S{s_num:02d}E{e_num:02d}`)\n\n"
                f"Daha Fazlası İçin: https://izle.darkbox.com.tr:9443/"
            )
        else:
            display_filename = f"{safe_base}.mp4"
            caption = (
                f"🎬 **{clean_title}**\n"
                f"📌 **Film**\n\n"
                f"Daha Fazlası İçin: https://izle.darkbox.com.tr:9443/"
            )

        temp_disk_filename = f"dl_{int(time.time())}_{safe_base}.mp4"
        video_path = TEMP_DIR / temp_disk_filename
        thumb_path = TEMP_DIR / f"{temp_disk_filename}.jpg"

        try:
            # 2. Kaynakları sırayla dene (2GB altı ve ses senkronlu olanı bulana kadar)
            download_success = False
            successful_plugin = plugin_name

            for p_name, extract_res in sources:
                link_url = extract_res.get("url") if isinstance(extract_res, dict) else getattr(extract_res, "url", "")
                link_referer = extract_res.get("referer") if isinstance(extract_res, dict) else getattr(extract_res, "referer", "")
                link_name = extract_res.get("name") if isinstance(extract_res, dict) else getattr(extract_res, "name", "")

                if not link_url:
                    continue

                logger.info(f"Denenen Kaynak: [{p_name}] {link_name} -> {link_url[:80]}...")
                success = await MediaDownloader.download_stream(
                    stream_url=link_url,
                    referer=link_referer,
                    output_path=video_path,
                    meta_title=display_name,
                    season=s_num,
                    episode=e_num
                )
                if success and video_path.exists():
                    fsize = video_path.stat().st_size
                    if fsize <= MAX_ALLOWED_FILE_SIZE_BYTES:
                        download_success = True
                        successful_plugin = p_name
                        break
                    else:
                        logger.info(f"Dosya 2GB limitini aştı ({fsize / (1024*1024):.1f} MB). Sıkıştırma yapılmıyor, alternatif kaynak deneniyor...")
                        try: video_path.unlink()
                        except Exception: pass

            if not download_success or not video_path.exists():
                logger.error(f"Tüm alternatif kaynaklar denendi ancak 2GB altı uygun akış bulunamadı: {display_name}")
                self.db.mark_failed(item_url, "2GB altı uygun kaynak bulunamadı")
                return False

            # 3. Thumbnail üret
            await MediaDownloader.extract_thumbnail(video_path, thumb_path)

            # 4. Telegram'a yükle
            topic_id = None
            if s_num > 0 and e_num > 0:
                topic_id = await self.get_or_create_series_topic(clean_title)
            else:
                topic_id = await self.get_or_create_series_topic("Filmler")

            self.db.add_or_update_record(successful_plugin, item_url, display_name, s_num, e_num, status="uploading")
            msg = await self.upload_video_file(
                file_path=video_path,
                display_filename=display_filename,
                caption=caption,
                thumb_path=thumb_path,
                topic_id=topic_id
            )
            
            if msg:
                self.db.mark_uploaded(item_url, video_path.stat().st_size, msg.id)
                logger.info(f"Tamamlandı: [{successful_plugin}] {display_name} (Mesaj ID: {msg.id})")
                return True
            else:
                self.db.mark_failed(item_url, "Telegram yükleme hatası")
                return False

        except Exception as e:
            logger.error(f"İşlem sırasında hata ({item_url}): {e}", exc_info=True)
            self.db.mark_failed(item_url, str(e))
            return False
        finally:
            if video_path.exists():
                try: video_path.unlink()
                except Exception: pass
            if thumb_path.exists():
                try: thumb_path.unlink()
                except Exception: pass

    async def crawl_and_process_plugin(self, plugin_name: str):
        """Belirtilen eklentinin son eklenen içeriklerini tarar."""
        logger.info(f"[{plugin_name}] taranıyor...")
        try:
            plugin = await get_plugin(plugin_name)
            if not plugin:
                return

            items_to_process = []
            if plugin_name == "DizipalX":
                ep_cards = await plugin.get_main_page(1, url=f"{plugin.main_url}/son-bolumler", category="Son Bölümler")
                for card in ep_cards[:15]:
                    clean_name, s_num, e_num = MediaDownloader.parse_title_season_episode(card.title, card.url)
                    items_to_process.append({"url": card.url, "title": clean_name, "season": s_num, "episode": e_num})
            elif plugin_name == "Vizyona":
                main_cards = await plugin.get_main_page(1)
                for card in main_cards[:15]:
                    clean_name, s_num, e_num = MediaDownloader.parse_title_season_episode(card.title, card.url)
                    items_to_process.append({"url": card.url, "title": clean_name, "season": s_num, "episode": e_num})

            for item in items_to_process:
                if self.db.is_processed(item["url"]):
                    continue
                await self.process_item(plugin_name, item["url"], item["title"], item["season"], item["episode"])
        except Exception as e:
            logger.error(f"[{plugin_name}] Tarama hatası: {e}")

    async def run_loop(self):
        """Periyodik arka plan tarama döngüsü."""
        logger.info(f"DarkBox Telegram Uploader Botu başlatıldı. Hedef Grup: {self.config.target_chat_id}")
        client = self._get_client()
        async with client:
            me = await client.get_me()
            logger.info(f"Telegram oturumu aktif: {me.first_name} (@{me.username or me.id})")
            
            while True:
                for plugin_name in self.config.plugins_to_watch:
                    try:
                        await self.crawl_and_process_plugin(plugin_name)
                    except Exception as e:
                        logger.error(f"Hata [{plugin_name}]: {e}", exc_info=True)
                
                logger.info(f"Tarama bitti. {self.config.check_interval} saniye bekleniyor...")
                await asyncio.sleep(self.config.check_interval)


async def main():
    config = Config()
    bot = DarkBoxUploaderBot(config)

    # DarkBox Genel Arama ve Yükleme Parametresi
    if len(sys.argv) > 2 and sys.argv[1] == "--search-and-upload":
        query = sys.argv[2]
        season_target = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        from_ep = int(sys.argv[4]) if len(sys.argv) > 4 else 1

        print(f"DarkBox genelinde '{query}' aranıyor ve yükleniyor...")
        client = bot._get_client()
        async with client:
            matched = await bot.search_content_across_darkbox(query)
            if not matched:
                print(f"'{query}' için hiçbir kaynak bulunamadı.")
                return

            # Dizi detayını çöz
            series_detail = None
            found_plugin = ""
            for m in matched:
                p = m.get("plugin")
                u = m.get("url")
                if p and u:
                    try:
                        det = await local_load_item(p, u)
                        if isinstance(det, dict) and det.get("episodes"):
                            series_detail = det
                            found_plugin = p
                            break
                    except Exception: pass

            if series_detail and series_detail.get("episodes"):
                episodes = series_detail.get("episodes", [])
                episodes.sort(key=lambda x: (x.get("season", 1), x.get("episode", 1)))
                print(f"Toplam {len(episodes)} bölüm bulundu ({series_detail.get('title')}).")
                
                for ep in episodes:
                    s_num = ep.get("season", 1)
                    e_num = ep.get("episode", 1)
                    if season_target > 0 and s_num != season_target:
                        continue
                    if e_num < from_ep and (season_target == 0 or s_num == season_target):
                        continue

                    print(f"\n{'='*50}\n-> {s_num}. Sezon {e_num}. Bölüm işleniyor: {ep.get('title')}\n{'='*50}")
                    await bot.process_item(
                        plugin_name=found_plugin,
                        item_url=ep.get("url"),
                        raw_title=series_detail.get("title", query),
                        season=s_num,
                        episode=e_num
                    )
                    await asyncio.sleep(2)
            else:
                # Tekil içerik/Film
                first_item = matched[0]
                print(f"Tekil içerik işleniyor: {first_item.get('title')} ({first_item.get('plugin')})")
                await bot.process_item(
                    plugin_name=first_item.get("plugin"),
                    item_url=first_item.get("url"),
                    raw_title=first_item.get("title", query)
                )
        return

    # Tekil URL / Dizi URL parametreleri
    if len(sys.argv) > 2 and sys.argv[1] == "--series":
        series_url = sys.argv[2]
        plugin_name = sys.argv[3] if len(sys.argv) > 3 else "DizipalX"
        client = bot._get_client()
        async with client:
            det = await local_load_item(plugin_name, series_url)
            if isinstance(det, dict) and det.get("episodes"):
                eps = sorted(det.get("episodes", []), key=lambda x: (x.get("season", 1), x.get("episode", 1)))
                for ep in eps:
                    await bot.process_item(
                        plugin_name=plugin_name,
                        item_url=ep.get("url"),
                        raw_title=det.get("title", "Dizi"),
                        season=ep.get("season", 1),
                        episode=ep.get("episode", 1)
                    )
                    await asyncio.sleep(2)
        return

    if len(sys.argv) > 2 and sys.argv[1] == "--item":
        item_url = sys.argv[2]
        plugin_name = sys.argv[3] if len(sys.argv) > 3 else "Vizyona"
        raw_title = sys.argv[4] if len(sys.argv) > 4 else "İçerik"
        season = int(sys.argv[5]) if len(sys.argv) > 5 else 0
        episode = int(sys.argv[6]) if len(sys.argv) > 6 else 0
        client = bot._get_client()
        async with client:
            await bot.process_item(plugin_name, item_url, raw_title, season, episode)
        return

    await bot.run_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot durduruldu.")
