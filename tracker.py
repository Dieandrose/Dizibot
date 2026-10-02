#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot Otomatik Takip Modülü (Watchlist Tracker Daemon)
========================================================
Watchlist'teki dizileri periyodik olarak tarar, yeni yayınlanan bölümleri
tespit edip otomatik indirme ve yükleme kuyruğuna aktarır.
"""

import asyncio
import logging
from typing import Optional, Callable

from config import config
from database import db
from downloader import Downloader

try:
    from Public.API.v1.Libs.local_plugins import (
        search as local_search,
        load_item as local_load_item
    )
except ImportError:
    pass

logger = logging.getLogger("DiziBot.Tracker")


class WatchlistTracker:
    def __init__(self, queue_callback: Optional[Callable] = None):
        self.queue_callback = queue_callback
        self.is_running = False

    async def check_series(self, series_name: str):
        logger.info(f"Takip listesi kontrol ediliyor: {series_name}")
        results = await Downloader.search_all_plugins(series_name)
        if not results:
            return

        norm_name = db._norm_title(series_name)
        
        for item in results:
            item_title = item.get("title", "")
            plugin_name = item.get("plugin_name", "")
            item_url = item.get("url", "")

            if norm_name not in db._norm_title(item_title):
                continue

            try:
                detail = await local_load_item(plugin_name, item_url)
                if not detail:
                    continue

                episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                for ep in episodes:
                    s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                    e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                    ep_url = ep.get("url", "") if isinstance(ep, dict) else getattr(ep, "url", "")
                    ep_title = ep.get("title", "") if isinstance(ep, dict) else getattr(ep, "title", "")

                    # Daha önce yüklendi mi kontrol et
                    if not db.is_title_ep_uploaded(series_name, s_num, e_num):
                        logger.info(f"Yeni Bölüm Tespit Edildi! {series_name} S{s_num:02d}E{e_num:02d} -> Kuyruğa ekleniyor.")
                        db.add_to_queue(
                            title=series_name,
                            season=s_num,
                            episode=e_num,
                            plugin_name=plugin_name,
                            item_url=ep_url,
                            priority=2
                        )
            except Exception as ex:
                logger.debug(f"Tracker eklenti ({plugin_name}) atlandı: {ex}")

    async def run_loop(self):
        self.is_running = True
        logger.info("Watchlist Tracker döngüsü başlatıldı.")
        while self.is_running:
            try:
                watchlist = db.get_watchlist()
                if watchlist:
                    logger.info(f"Takip listesinde {len(watchlist)} dizi taranıyor...")
                    for row in watchlist:
                        s_name = row["series_name"]
                        await self.check_series(s_name)
                        await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Tracker döngü hatası: {e}")

            # Belirlenen aralık kadar bekle
            await asyncio.sleep(config.check_interval_tracker)

    def stop(self):
        self.is_running = False
