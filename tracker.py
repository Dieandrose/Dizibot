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
        try:
            results = await local_search(series_name)
        except Exception as e:
            logger.error(f"Tracker arama hatası ({series_name}): {e}")
            return

        norm_name = db._norm_title(series_name)
        
        for item in results:
            item_title = item.title if hasattr(item, "title") else str(item)
            plugin_name = item.plugin_name if hasattr(item, "plugin_name") else "DarkBox"
            item_url = item.url if hasattr(item, "url") else ""

            if norm_name not in db._norm_title(item_title):
                continue

            try:
                detail = await local_load_item(plugin_name, item_url)
                if not detail or not hasattr(detail, "episodes"):
                    continue

                for ep in detail.episodes:
                    s_num = ep.season if hasattr(ep, "season") else (ep.get("season", 1) if isinstance(ep, dict) else 1)
                    e_num = ep.episode if hasattr(ep, "episode") else (ep.get("episode", 1) if isinstance(ep, dict) else 1)
                    ep_url = ep.url if hasattr(ep, "url") else (ep.get("url", "") if isinstance(ep, dict) else "")
                    ep_title = ep.title if hasattr(ep, "title") else (ep.get("title", "") if isinstance(ep, dict) else "")

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
