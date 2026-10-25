#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot Otomatik Takip ve Akış Tarama Modülü (Watchlist & Feed Tracker Daemon)
=============================================================================
1. DizipalX ve Dizipal3 güncel/yeni eklenen bölümler akışını periyodik tarar.
2. Grupta kategorisi (topic) mevcut olan ve henüz grupta yüklenmemiş bölümleri
   otomatik tespit edip indirme/yükleme kuyruğuna aktarır.
3. Watchlist'teki özel takipli dizileri denetler.
"""

import asyncio
import logging
import re
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

    @staticmethod
    def _parse_season_episode(title: str, url: str):
        """URL ve başlıktan sezon/bölüm numarasını ayrıştırır."""
        m_x = re.search(r"[-/](\d+)x(\d+)", url)
        if m_x:
            return int(m_x.group(1)), int(m_x.group(2))
        m_3 = re.search(r"(\d+)-sezon/(\d+)-bolum", url)
        if m_3:
            return int(m_3.group(1)), int(m_3.group(2))
        m_t = re.search(r"(\d+)\.\s*Sezon\s*(\d+)\.\s*Bölüm", title, re.IGNORECASE)
        if m_t:
            return int(m_t.group(1)), int(m_t.group(2))
        return None, None

    @staticmethod
    def _clean_series_title(title: str) -> str:
        """Bölüm eklerini temizleyerek yalın dizi adını çıkarır."""
        t = (title or "").strip()
        t = re.sub(r"\s+\d+x\d+.*$", "", t)
        t = re.sub(r"\s+\d+\.\s*Sezon.*$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*-\s*\d+\.\s*Bölüm.*$", "", t, flags=re.IGNORECASE)
        return t.strip()

    async def check_recent_feeds(self, max_pages: int = 3):
        """DizipalX ve Dizipal3 güncel akışını tarar; grupta kategorisi olan eksik bölümleri kuyruğa ekler."""
        logger.info("📡 Güncel bölümler akışı (DizipalX & Dizipal3) kontrol ediliyor...")
        try:
            from Plugins.DizipalX import DizipalX
            from Plugins.Dizipal3 import Dizipal3

            dx = DizipalX()
            d3 = Dizipal3()

            candidates = []

            # 1. DizipalX Güncel Bölümler
            for page in range(1, max_pages + 1):
                try:
                    items_x = await dx.get_main_page(1, url=f"https://dizipal1592.com/yeni-eklenen-bolumler?sayfa={page}")
                    for it in items_x:
                        candidates.append(("DizipalX", it))
                except Exception as ex:
                    logger.debug(f"DizipalX sayfa {page} hatası: {ex}")

            # 2. Dizipal3 Yeni Eklenen Bölümler
            for page in range(1, max_pages + 1):
                try:
                    items_3 = await d3.get_main_page(1, url=f"https://dizipal1822.com/yeni-eklenen-bolumler?&page={page}")
                    for it in items_3:
                        candidates.append(("Dizipal3", it))
                except Exception as ex:
                    logger.debug(f"Dizipal3 sayfa {page} hatası: {ex}")

            added_count = 0
            for plugin_name, it in candidates:
                raw_title = it.title.strip()
                s_num, e_num = self._parse_season_episode(raw_title, it.url)
                if s_num is None or e_num is None:
                    continue

                clean_title = self._clean_series_title(raw_title)

                # Grupta forum konusu (kategorisi) mevcut mu?
                topic_id = db.get_topic_id(clean_title)
                if not topic_id:
                    continue

                # Daha önce yüklendi mi veya şu an kuyrukta mı?
                if db.is_title_ep_uploaded(clean_title, s_num, e_num) or db.is_job_in_queue(clean_title, s_num, e_num):
                    continue

                job_title = f"{clean_title} S{s_num:02d}E{e_num:02d}"
                logger.info(f"⚡ Yeni Çıkan Bölüm Tespit Edildi: {job_title} (Konu ID: {topic_id}) -> Kuyruğa aktarılıyor [{plugin_name}]")
                db.add_to_queue(
                    title=job_title,
                    season=s_num,
                    episode=e_num,
                    plugin_name=plugin_name,
                    item_url=it.url,
                    priority=5,
                    requested_by=1080169172
                )
                added_count += 1

            if added_count > 0:
                logger.info(f"✅ Akış kontrolünden {added_count} yeni bölüm otomatik indirme kuyruğuna eklendi.")
            else:
                logger.info("ℹ️ Akış kontrolü tamamlandı: Grupta kategorisi olan tüm bölümler zaten güncel.")
        except Exception as e:
            logger.error(f"check_recent_feeds hatası: {e}")

    async def check_series(self, series_name: str):
        """Özel takip listesindeki belirli bir diziyi tüm eklentilerde tarar."""
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

                    if not db.is_title_ep_uploaded(series_name, s_num, e_num) and not db.is_job_in_queue(series_name, s_num, e_num):
                        logger.info(f"Yeni Bölüm Tespit Edildi! {series_name} S{s_num:02d}E{e_num:02d} -> Kuyruğa ekleniyor.")
                        db.add_to_queue(
                            title=series_name,
                            season=s_num,
                            episode=e_num,
                            plugin_name=plugin_name,
                            item_url=ep_url,
                            priority=2,
                            requested_by=1080169172
                        )
            except Exception as ex:
                logger.debug(f"Tracker eklenti ({plugin_name}) atlandı: {ex}")

    async def run_loop(self):
        """Arka plan kesintisiz takip döngüsü."""
        self.is_running = True
        logger.info("🚀 DiziBot Otomatik Akış & Takip Servisi başlatıldı.")
        while self.is_running:
            try:
                # 1. DizipalX & Dizipal3 Güncel Bölümler Akışını Tara
                await self.check_recent_feeds(max_pages=3)

                # 2. Watchlist dizilerini tara
                watchlist = db.get_watchlist()
                if watchlist:
                    logger.info(f"Takip listesinde {len(watchlist)} dizi taranıyor...")
                    for row in watchlist:
                        s_name = row["series_name"]
                        await self.check_series(s_name)
                        await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Tracker döngü hatası: {e}")

            # Belirlenen aralık kadar bekle (varsayılan: 15 dakika)
            await asyncio.sleep(config.check_interval_tracker)

    def stop(self):
        self.is_running = False
