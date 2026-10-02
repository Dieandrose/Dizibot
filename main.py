#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot Ana Giriş Noktası (Main Entrypoint)
===========================================
Pyrogram Bot, Otomatik Takipçi (Watchlist Tracker) ve
İş Kuyruğu İşleyicisini eşzamanlı olarak başlatır.
"""

import sys
import asyncio
import logging
import signal
from pathlib import Path

# DiziBot dizinini path'e ekle
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config import config
from database import db
from bot_handlers import DiziBotManager
from tracker import WatchlistTracker

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("DiziBot.Main")


async def main():
    logger.info("=" * 60)
    logger.info("🤖 DiziBot (Otonom Telegram Video Dağıtım Botu) Başlatılıyor...")
    logger.info("=" * 60)

    if not config.bot_token:
        logger.error("HATA: TG_BOT_TOKEN yapılandırılmamış! Lütfen .env dosyasını kontrol edin.")
        sys.exit(1)

    bot_manager = DiziBotManager()
    tracker = WatchlistTracker()

    # Pyrogram İstemcisini Başlat
    logger.info("Pyrogram Bot İstemcisi bağlanıyor...")
    await bot_manager.app.start()
    bot_me = await bot_manager.app.get_me()
    logger.info(f"Bot Başarıyla Giriş Yaptı: @{bot_me.username} ({bot_me.first_name})")

    # Watchlist Tracker'ı Arka Planda Başlat
    tracker_task = asyncio.create_task(tracker.run_loop())
    
    # Kuyruk Dinleyicisini Başlat
    async def queue_worker():
        while True:
            try:
                if not bot_manager.is_processing_queue:
                    next_job = db.get_next_queue_item()
                    if next_job:
                        asyncio.create_task(bot_manager.process_queue())
            except Exception as e:
                logger.error(f"Kuyruk worker hatası: {e}")
            await asyncio.sleep(5)

    queue_task = asyncio.create_task(queue_worker())

    # Kapanma Sinyallerini Yakala
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Durdurma sinyali alındı, temizlik yapılıyor...")
        tracker.stop()
        tracker_task.cancel()
        queue_task.cancel()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    from pyrogram import idle
    try:
        await idle()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Bot istemcisi durduruluyor...")
        await bot_manager.app.stop()
        logger.info("DiziBot güvenle kapatıldı.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
