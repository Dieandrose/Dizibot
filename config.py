#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot Yapılandırma Modülü
"""

import os
import sys
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent
DARKBOX_ROOT = Path(os.environ.get("DARKBOX_ROOT", "/root/Darkbox"))

# DarkBox kök dizinini Python path'e ekle
if str(DARKBOX_ROOT) not in sys.path:
    sys.path.insert(0, str(DARKBOX_ROOT))

# Ortam Dosyaları
ENV_FILE = BASE_DIR / ".env"
if not ENV_FILE.exists():
    ENV_FILE = Path("/etc/darkbox/tg_uploader.env")
if not ENV_FILE.exists():
    ENV_FILE = DARKBOX_ROOT / "tools" / "tg_uploader.env"

# Veri & Geçici Dizinler
DATA_DIR = BASE_DIR / "data"
TEMP_DIR = DATA_DIR / "temp"
DB_FILE = DATA_DIR / "dizibot.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)


class Config:
    def __init__(self):
        self.bot_token: str = ""
        self.api_id: int = 2040
        self.api_hash: str = "b18441a1ff607e10a989891a5462e627"
        self.session_name: str = str(DATA_DIR / "dizibot_session")
        self.target_chat_id: int = -1001909587016
        self.admin_ids: List[int] = [1080169172]  # Sahip / Admin Telegram ID
        self.proxy: str = "socks5://127.0.0.1:4000"  # WARP / MASQUE Proxy
        self.invite_link: str = "https://t.me/+_gKDymkpjtZmMzg8"  # Grup Davet Linki
        self.website_url: str = "https://izle.darkbox.com.tr:9443"  # Web Sitesi
        self.check_interval_tracker: int = 900  # 15 dakika
        self.max_file_size_bytes: int = int(1950 * 1024 * 1024)  # 1.95 GB
        self.max_concurrent_workers: int = 1  # Sıralı ve hatasız yükleme için tekil işlem
        self.min_free_disk_gb: float = 3.0  # Minimum boş disk alanı
        self.plugins_priority: List[str] = [
            "Dizi65", "DizipalX", "Dizirella", "Diziyou", "Dizilab", "RecTV", "SineWix", 
            "FullHDFilmizlesene", "JetFilmIzle", "Hdizipal", "FilmMakinesi"
        ]
        self.load()

    def load(self):
        env_vars = {}
        if ENV_FILE.exists():
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip().strip("'\"")

        self.bot_token = os.environ.get("TG_BOT_TOKEN") or env_vars.get("TG_BOT_TOKEN") or ""
        self.api_id = int(os.environ.get("TG_API_ID") or env_vars.get("TG_API_ID") or 2040)
        self.api_hash = os.environ.get("TG_API_HASH") or env_vars.get("TG_API_HASH") or "b18441a1ff607e10a989891a5462e627"
        
        chat_raw = os.environ.get("TG_TARGET_CHAT_ID") or env_vars.get("TG_TARGET_CHAT_ID") or "-1001909587016"
        try:
            cid = int(chat_raw)
            if cid > 0 and str(cid).startswith("100"):
                self.target_chat_id = -cid
            elif cid > 0:
                self.target_chat_id = -int(f"100{cid}")
            else:
                self.target_chat_id = cid
        except ValueError:
            self.target_chat_id = -1001909587016

        admin_raw = os.environ.get("ADMIN_IDS") or env_vars.get("ADMIN_IDS") or "1080169172"
        self.admin_ids = [int(a.strip()) for a in admin_raw.split(",") if a.strip().isdigit()]

        self.proxy = os.environ.get("TG_UPLOADER_PROXY") or env_vars.get("TG_UPLOADER_PROXY") or ""
        if self.proxy and (self.proxy.startswith("http://127.0.0.1:4000") or self.proxy.startswith("http://localhost:4000")):
            self.proxy = self.proxy.replace("http://", "socks5://")
        self.invite_link = os.environ.get("TG_INVITE_LINK") or env_vars.get("TG_INVITE_LINK") or "https://t.me/+_gKDymkpjtZmMzg8"
        self.website_url = os.environ.get("WEB_SITE_URL") or env_vars.get("WEB_SITE_URL") or "https://izle.darkbox.com.tr:9443"
        self.check_interval_tracker = int(os.environ.get("TRACKER_INTERVAL") or env_vars.get("TRACKER_INTERVAL") or 900)

config = Config()
