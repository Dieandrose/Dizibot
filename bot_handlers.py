#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot Telegram Chat-Ops, Komutlar, Butonlar ve İstek Sistemi
"""

import os
import re
import sys
import time
import asyncio
import logging
import sqlite3
import httpx
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from pyrogram import Client, filters
from pyrogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent
)

from config import config, TEMP_DIR
from database import db
from downloader import Downloader

try:
    from Public.API.v1.Libs.local_plugins import (
        search as local_search,
        load_item as local_load_item,
        load_links as local_load_links
    )
except ImportError:
    pass

logger = logging.getLogger("DiziBot.Bot")

# Bellek içi arama önbelleği (callback butonları için)
SEARCH_CACHE: Dict[str, List[Any]] = {}

# Canlı indirme ve Telegram yükleme takip haritası
LIVE_TRANSFERS: Dict[int, Dict[str, Any]] = {}

# Aktif çalışan asyncio görevleri (İptal yönetimi için)
ACTIVE_TASKS: Dict[int, asyncio.Task] = {}


class DiziBotManager:
    def __init__(self):
        self.app = Client(
            name=config.session_name,
            api_id=config.api_id,
            api_hash=config.api_hash,
            bot_token=config.bot_token
        )
        self._register_handlers()
        self.is_processing_queue = False

    async def get_or_create_series_topic(self, series_title: str, is_movie: bool = False) -> int:
        if is_movie:
            target_key = "Filmler"
            topic_name = "🎬 Filmler"
        else:
            target_key, _, _ = Downloader.parse_title_season_episode(series_title)
            topic_name = f"🎬 {target_key}"

        existing_id = db.get_topic_id(target_key)
        if existing_id:
            return existing_id

        api_url = f"https://api.telegram.org/bot{config.bot_token}/createForumTopic"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.post(api_url, json={
                    "chat_id": config.target_chat_id,
                    "name": topic_name[:128]
                })
                res_data = resp.json()
                if res_data.get("ok"):
                    new_topic_id = res_data["result"]["message_thread_id"]
                    db.save_topic(target_key, new_topic_id)
                    logger.info(f"Yeni Forum Konusu Açıldı: '{topic_name}' (ID: {new_topic_id})")
                    return new_topic_id
                else:
                    logger.error(f"Forum konusu açılamadı: {res_data}")
            except Exception as e:
                logger.error(f"createForumTopic hatası: {e}")

        return 0

    def _register_handlers(self):
        # 0. Satır İçi Arama (Inline Query Handler)
        @self.app.on_inline_query()
        async def handle_inline(client: Client, inline_query: InlineQuery):
            query = inline_query.query.strip()
            if not query:
                return

            try:
                results = await Downloader.search_all_plugins(query)
                articles = []
                for idx, item in enumerate(results[:15]):
                    title = item.get("title", "İçerik")
                    plugin = item.get("plugin_name", "Kaynak")
                    url = item.get("url", "")
                    poster = item.get("poster")
                    desc = item.get("description") or f"{plugin} üzerinden izle / indir"

                    thumb_url = poster if poster and poster.startswith("http") else None

                    articles.append(
                        InlineQueryResultArticle(
                            id=f"in_{idx}_{plugin}_{abs(hash(url)) % 1000000}",
                            title=f"🎬 {title} [{plugin}]",
                            description=desc[:100],
                            thumb_url=thumb_url,
                            input_message_content=InputTextMessageContent(
                                f"🎬 **{title}**\n\n"
                                f"🌐 **Daha Fazlası İçin :**  izle.darkbox.com.tr:9443"
                            ),
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("🔍 Sezon & Bölümleri Listele", switch_inline_query_current_chat=title)],
                                [InlineKeyboardButton("🌐 Daha Fazlası İçin", url="https://izle.darkbox.com.tr:9443")]
                            ])
                        )
                    )
                await inline_query.answer(articles, cache_time=30)
            except Exception as e:
                logger.error(f"Inline query hatası: {e}")

        # 1. /start ve /yardim
        @self.app.on_message(filters.command(["start", "yardim", "help"]))
        async def cmd_start(client: Client, message: Message):
            help_text = (
                "🤖 **DiziBot - Otonom Medya & Telegram Yükleyici**\n\n"
                "**Kullanılabilir Komutlar:**\n"
                "🔍 `/ara <dizi/film>` - İçerik ara ve butonlarla seçerek indir\n"
                "📥 `/indir <dizi> <sezon> <bölüm>` - Doğrudan bölüm indir ve yükle\n"
                "📊 `/durum` - Aktif indirme ve sistem durumu\n"
                "📋 `/kuyruk` - İndirme kuyruğunu görüntüle\n"
                "❌ `/iptal <id>` - Kuyruktaki işlemi iptal et\n\n"
                "**Otomatik Takipçi & Denetim:**\n"
                "🔔 `/takip <dizi>` - Diziyi otomatik yeni bölüm takibine al\n"
                "🔕 `/takipbirak <dizi>` - Takip listesinden çıkar\n"
                "📑 `/takiplistesi` - Takip edilen tüm diziler\n"
                "🛡️ `/kontrol <dizi>` - Eksik bölümleri tara ve otomatik tamamla\n\n"
                "**İstek Sistemi:**\n"
                "✍️ `/istek <içerik adı>` - Yöneticilere dizi/film isteği ilet\n"
            )
            if message.from_user and message.from_user.id in config.admin_ids:
                help_text += "\n👑 **Yönetici:** `/istekler` - Bekleyen istekleri listele"

            await message.reply_text(help_text)

        # 2. /ara <içerik>
        @self.app.on_message(filters.command(["ara", "search"]))
        async def cmd_search(client: Client, message: Message):
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("⚠️ Lütfen aramak istediğiniz içerik adını yazın.\nÖrnek: `/ara Mezarlık`")
                return

            query = args[1].strip()
            msg = await message.reply_text(f"🔍 **'{query}'** tüm DarkBox eklentilerinde aranıyor...")
            
            try:
                results = await Downloader.search_all_plugins(query)
            except Exception as e:
                await msg.edit_text(f"❌ Arama sırasında hata oluştu: {e}")
                return

            if not results:
                await msg.edit_text(f"❌ **'{query}'** için hiçbir kaynak bulunamadı.")
                return

            SEARCH_CACHE[str(message.from_user.id)] = results
            buttons = []
            for idx, r in enumerate(results[:25]):
                title = r.get("title", "İçerik")
                plugin = r.get("plugin_name", "Kaynak")
                buttons.append([InlineKeyboardButton(f"🎬 {title} [{plugin}]", callback_data=f"sel_res:{idx}")])

            keyboard = InlineKeyboardMarkup(buttons)
            await msg.edit_text(f"🎯 **'{query}'** için **{len(results)}** kaynak bulundu:\nİndirmek istediğiniz sunucuyu seçin:", reply_markup=keyboard)

        # 3. /indir <dizi> <sezon> <bölüm>
        @self.app.on_message(filters.command(["indir", "download"]))
        async def cmd_download(client: Client, message: Message):
            if message.from_user and message.from_user.id not in config.admin_ids:
                await message.reply_text("⚠️ Bu komut sadece yöneticiler içindir. İçerik istemek için `/istek <isim>` kullanabilirsiniz.")
                return

            parts = message.text.split()
            if len(parts) < 4:
                await message.reply_text("⚠️ Hatalı format!\nKullanım: `/indir <Dizi Adı> <Sezon> <Bölüm>`\nÖrnek: `/indir Mezarlık 2 1`")
                return

            s_num = int(parts[-2])
            e_num = int(parts[-1])
            title = " ".join(parts[1:-2])

            job_id = db.add_to_queue(title=title, season=s_num, episode=e_num, priority=3)
            await message.reply_text(f"✅ **{title} S{s_num:02d}E{e_num:02d}** indirme kuyruğuna eklendi! (İşlem ID: `{job_id}`)")
            asyncio.create_task(self.process_queue())

        # 4. /durum
        @self.app.on_message(filters.command(["durum", "status"]))
        async def cmd_status(client: Client, message: Message):
            text, markup = self.get_system_status_report()
            await message.reply_text(text, reply_markup=markup)

        # 5. /kuyruk
        @self.app.on_message(filters.command(["kuyruk", "queue"]))
        async def cmd_queue(client: Client, message: Message):
            jobs = db.get_active_queue()
            if not jobs:
                await message.reply_text("📋 Kuyrukta bekleyen işlem yok.")
                return

            q_text = f"📋 **İndirme Kuyruğu ({len(jobs)} İşlem):**\n\n"
            for j in jobs[:15]:
                disp = f"{j['title']} (Film)" if (j.get('season') == 0 or j.get('season') is None) else f"{j['title']} S{j['season']}E{j['episode']}"
                q_text += f"• `#{j['id']}` | **{disp}** ➔ `{j['status']}`\n"
            if len(jobs) > 15:
                q_text += f"\n... ve **{len(jobs) - 15}** işlem daha kuyrukta bekliyor."
            await message.reply_text(q_text)

        # 6. /iptal <id> veya /iptal hepsi
        @self.app.on_message(filters.command(["iptal", "cancel", "kuyruktemizle"]))
        async def cmd_cancel(client: Client, message: Message):
            if message.from_user and message.from_user.id not in config.admin_ids:
                await message.reply_text("⚠️ Bu komut sadece yöneticiler içindir.")
                return

            parts = message.text.split()
            if len(parts) == 1 or (len(parts) >= 2 and parts[1].lower() in ["hepsi", "all", "tum", "tümü", "*"]):
                deleted = db.cancel_all_queue()
                # Çalışan tüm aktif görevleri durdur
                for task in list(ACTIVE_TASKS.values()):
                    if task and not task.done():
                        task.cancel()
                ACTIVE_TASKS.clear()
                LIVE_TRANSFERS.clear()
                
                # Temp dizinindeki geçici dosyaları temizle
                for p in TEMP_DIR.glob("*"):
                    try:
                        if p.is_file():
                            p.unlink(missing_ok=True)
                    except Exception:
                        pass
                await message.reply_text(f"🛑 **Tüm aktif ve bekleyen indirmeler iptal edildi.** ({deleted} işlem kuyruktan silindi, geçici dosyalar temizlendi)")
                return

            if not parts[1].isdigit():
                await message.reply_text("⚠️ Kullanım: `/iptal <işlem_id>` veya `/iptal hepsi`")
                return

            job_id = int(parts[1])
            # Aktif çalışan görevi durdur
            task = ACTIVE_TASKS.pop(job_id, None)
            if task and not task.done():
                task.cancel()

            if db.cancel_queue_item(job_id):
                LIVE_TRANSFERS.pop(job_id, None)
                # İlgili temp dosyalarını temizle
                for p in TEMP_DIR.glob(f"job_{job_id}_*"):
                    try:
                        p.unlink(missing_ok=True)
                    except Exception:
                        pass
                await message.reply_text(f"🛑 İşlem `#{job_id}` başarıyla iptal edildi ve indirmesi durduruldu.")
            else:
                await message.reply_text(f"❌ İşlem `#{job_id}` bulunamadı veya zaten tamamlanmış.")

        # 7. Watchlist Komutları (/takip, /takiplistesi, /takipbirak)
        @self.app.on_message(filters.command(["takip"]))
        async def cmd_takip(client: Client, message: Message):
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("⚠️ Kullanım: `/takip <Dizi Adı>`")
                return
            s_name = args[1].strip()
            db.add_to_watchlist(s_name, message.from_user.id if message.from_user else 0)
            await message.reply_text(f"🔔 **'{s_name}'** otomatik takip listesine eklendi! Yeni bölümler yayınlandığında otomatik yüklenecektir.")

        @self.app.on_message(filters.command(["takiplistesi"]))
        async def cmd_takiplistesi(client: Client, message: Message):
            wl = db.get_watchlist()
            if not wl:
                await message.reply_text("📭 Takip listesinde henüz dizi yok.")
                return
            t_text = "🔔 **Otomatik Takip Edilen Diziler:**\n\n"
            for row in wl:
                t_text += f"• 🎬 **{row['series_name'].title()}**\n"
            await message.reply_text(t_text)

        @self.app.on_message(filters.command(["takipbirak"]))
        async def cmd_takipbirak(client: Client, message: Message):
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("⚠️ Kullanım: `/takipbirak <Dizi Adı>`")
                return
            s_name = args[1].strip()
            if db.remove_from_watchlist(s_name):
                await message.reply_text(f"🔕 **'{s_name}'** takip listesinden çıkarıldı.")
            else:
                await message.reply_text(f"❌ **'{s_name}'** takip listesinde bulunamadı.")

        # 8. Forum Konu Yönetimi (/konubagla, /konu, /konular)
        @self.app.on_message(filters.command(["konubagla", "konu", "settopic"]))
        async def cmd_set_topic(client: Client, message: Message):
            if message.from_user and message.from_user.id not in config.admin_ids:
                await message.reply_text("⚠️ Bu komut sadece yöneticiler içindir.")
                return

            parts = message.text.split(maxsplit=2)
            current_thread_id = getattr(message, "message_thread_id", None) or getattr(message, "reply_to_top_message_id", None)

            if len(parts) == 1 and current_thread_id:
                await message.reply_text(f"📌 Bu konunun ID'si: `{current_thread_id}`\nBir diziye bağlamak için konu içinde: `/konubagla <Dizi Adı>` yazabilirsiniz.")
                return

            if len(parts) == 2:
                dizi_name = parts[1]
                if current_thread_id:
                    tid = current_thread_id
                else:
                    await message.reply_text("⚠️ Kullanım: `/konubagla <Dizi Adı> <Konu_ID>` veya konunun içindeyken `/konubagla <Dizi Adı>`")
                    return
            elif len(parts) >= 3:
                dizi_name = parts[1]
                try:
                    tid = int(parts[2])
                except ValueError:
                    await message.reply_text("⚠️ Konu ID sayı olmalıdır. Örnek: `/konubagla Tuzlu Kahve 4823`")
                    return
            else:
                await message.reply_text("⚠️ Kullanım: `/konubagla <Dizi Adı> <Konu_ID>`")
                return

            clean_name, _, _ = Downloader.parse_title_season_episode(dizi_name)
            db.save_topic(clean_name, tid)
            await message.reply_text(f"✅ **'{clean_name}'** içeriği başarıyla Konu ID `#{tid}` ile eşleştirildi!")

        @self.app.on_message(filters.command(["konular", "topics"]))
        async def cmd_list_topics(client: Client, message: Message):
            with db._get_conn() as conn:
                cur = conn.cursor()
                rows = cur.execute("SELECT series_title, topic_id FROM topics ORDER BY id ASC").fetchall()

            if not rows:
                await message.reply_text("📭 Kayıtlı forum konusu bulunamadı.")
                return

            lines = ["📋 **Kayıtlı Forum Konuları:**\n"]
            for r in rows:
                lines.append(f"• 🎬 **{r['series_title'].title()}** ➔ Konu ID: `{r['topic_id']}`")

            await message.reply_text("\n".join(lines))

        @self.app.on_message(filters.command(["konusil", "deltopic"]))
        async def cmd_del_topic(client: Client, message: Message):
            if message.from_user and message.from_user.id not in config.admin_ids:
                await message.reply_text("⚠️ Bu komut sadece yöneticiler içindir.")
                return
            parts = message.text.split(maxsplit=1)
            if len(parts) < 2:
                await message.reply_text("⚠️ Kullanım: `/konusil <Dizi Adı>`")
                return
            dizi_name = parts[1].strip()
            norm = db._norm_title(dizi_name)
            with db._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM topics WHERE series_title = ?", (norm,))
                cnt = cur.rowcount
                conn.commit()
            if cnt > 0:
                await message.reply_text(f"🗑️ **'{dizi_name}'** konusu başarıyla veritabanından silindi.")
            else:
                await message.reply_text(f"⚠️ **'{dizi_name}'** adında kayıtlı bir konu bulunamadı.")

        # 8.1 Forum Konusu Oluşturulduğunda / Düzenlendiğinde Otomatik Yakalama
        @self.app.on_message(filters.chat(config.target_chat_id) & filters.service)
        async def on_topic_action(client: Client, message: Message):
            topic_info = getattr(message, "forum_topic_created", None) or getattr(message, "forum_topic_edited", None)
            if topic_info and getattr(topic_info, "name", None):
                t_name = topic_info.name
                t_id = message.id
                db.save_topic(t_name, t_id)
                logger.info(f"📌 Telegram Konusu Otomatik Yakalandı & Kaydedildi: '{t_name}' -> Topic ID: {t_id}")

        # 9. İstek Sistemi (/istek & /istekler)
        @self.app.on_message(filters.command(["istek"]))
        async def cmd_istek(client: Client, message: Message):
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("⚠️ Lütfen istemek istediğiniz dizi veya film adını belirtin.\nÖrnek: `/istek Mezarlık 2. Sezon`")
                return

            query = args[1].strip()
            u_id = message.from_user.id if message.from_user else 0
            u_name = message.from_user.first_name if message.from_user else "Üye"
            u_username = (message.from_user.username or "").lower() if message.from_user else ""

            # Sadece Dark / Yönetici kontrolü (ID veya Kullanıcı Adı)
            is_dark_or_admin = (
                u_id in config.admin_ids
                or u_username in ["dark", "dieandrose"]
                or "dark" in u_name.lower()
            )

            if is_dark_or_admin:
                # Dark / Yönetici için ONAYSIZ DOĞRUDAN YÜKLEME
                clean_title, s, e = Downloader.parse_title_season_episode(query)
                req_id = db.create_request(user_id=u_id, user_name=u_name, query=query)
                db.update_request_status(req_id, "approved", admin_id=u_id)

                # Sezon tespiti kontrolü (örn: "2. Sezon" denmiş ama bölüm belirtilmemişse)
                season_match = re.search(r"(\d+)\s*\.?\s*sezon", query, re.IGNORECASE)
                has_explicit_ep = bool(re.search(r"(\d+)\s*\.?\s*bölüm|s\d+e\d+|e\d+", query, re.IGNORECASE))

                if season_match and not has_explicit_ep:
                    target_s = int(season_match.group(1))
                    status_msg = await message.reply_text(f"⚡ **Dark İstek Algılandı:** '{clean_title} {target_s}. Sezon' taranıyor ve tüm bölümler onaysız kuyruğa alınıyor...")
                    
                    # Sezon bölümlerini bul
                    results = await Downloader.search_all_plugins(clean_title)
                    queued_count = 0
                    if results:
                        item = results[0]
                        detail = await local_load_item(item.get("plugin_name", ""), item.get("url", ""))
                        episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                        episodes.sort(key=lambda ep: (
                            ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1),
                            ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                        ))
                        for ep in episodes:
                            s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                            e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                            if s_num == target_s:
                                db.add_to_queue(title=clean_title, season=s_num, episode=e_num, priority=3)
                                queued_count += 1

                    if queued_count > 0:
                        await status_msg.edit_text(f"🚀 **Dark İsteği Başlatıldı:** {clean_title} {target_s}. Sezon ({queued_count} bölüm) onaysız olarak doğrudan kuyruğa alındı ve indirme başladı!")
                    else:
                        job_id = db.add_to_queue(title=clean_title, season=target_s, episode=1, priority=3)
                        await status_msg.edit_text(f"🚀 **Dark İsteği Başlatıldı:** '{clean_title} S{target_s:02d}E01' kuyruğa alındı! (İşlem ID: `{job_id}`)")
                else:
                    # İçeriğin Dizi mi Film mi olduğunu kontrol et
                    results = await Downloader.search_all_plugins(clean_title)
                    is_series_found = False
                    item = None
                    episodes = []
                    if results:
                        try:
                            item = results[0]
                            detail = await local_load_item(item.get("plugin_name", ""), item.get("url", ""))
                            episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                            if episodes and (len(episodes) > 1 or (episodes[0].get("episode", 0) if isinstance(episodes[0], dict) else getattr(episodes[0], "episode", 0)) > 1 or "/dizi/" in item.get("url", "")):
                                is_series_found = True
                        except Exception:
                            pass

                    # Eğer spesifik sezon/bölüm girilmemişse
                    if not has_explicit_ep and not season_match:
                        if is_series_found and episodes:
                            episodes.sort(key=lambda ep: (
                                ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1),
                                ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                            ))
                            added = 0
                            seasons_set = set()
                            p_name = item.get("plugin_name", "") if item else ""
                            i_url = item.get("url", "") if item else ""
                            for ep in episodes:
                                s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                                e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                                db.add_to_queue(title=clean_title, season=s_num, episode=e_num, plugin_name=p_name, item_url=i_url, priority=2)
                                added += 1
                                seasons_set.add(s_num)

                            await message.reply_text(f"🚀 **Dark İsteği Başlatıldı:** '{clean_title}' dizisinin **TÜM SEZONLARI** ({len(seasons_set)} sezon, toplam {added} bölüm) onaysız olarak doğrudan kuyruğa alındı ve sırayla indirilip yükleniyor!")
                        else:
                            job_id = db.add_to_queue(title=clean_title, season=0, episode=0, priority=3)
                            await message.reply_text(f"🚀 **Dark İsteği Başlatıldı:** '{clean_title}' (Film) onaysız olarak doğrudan kuyruğa alındı ve '🎬 Filmler' konusuna yükleniyor! (İşlem ID: `{job_id}`)")
                    else:
                        job_id = db.add_to_queue(title=clean_title, season=s, episode=e, priority=3)
                        await message.reply_text(f"🚀 **Dark İsteği Başlatıldı:** '{clean_title} S{s:02d}E{e:02d}' onaysız olarak doğrudan kuyruğa alındı ve indirme başladı! (İşlem ID: `{job_id}`)")

                asyncio.create_task(self.process_queue())
                return

            # Normal Üyeler için Yönetici Onayı Akışı
            req_id = db.create_request(user_id=u_id, user_name=u_name, query=query)
            await message.reply_text(f"📩 İsteğiniz alındı! (İstek No: `#{req_id}`)\nYöneticiler onayladığında otomatik olarak konuya yüklenecektir.")

            # Adminlere Onay Butonlu Bildirim Gönder
            admin_btn = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Onayla & Yükle", callback_data=f"req_app:{req_id}"),
                    InlineKeyboardButton("❌ Reddet", callback_data=f"req_rej:{req_id}")
                ]
            ])
            admin_msg = (
                f"📥 **Yeni İçerik İsteği Geldi!**\n\n"
                f"👤 **İsteyen:** {u_name} (`{u_id}`)\n"
                f"🎬 **İçerik:** `{query}`\n"
                f"📌 **İstek No:** `#{req_id}`"
            )
            for a_id in config.admin_ids:
                try:
                    await self.app.send_message(chat_id=a_id, text=admin_msg, reply_markup=admin_btn)
                except Exception as ex:
                    logger.debug(f"Admin bildirim hatası ({a_id}): {ex}")

        # 9. Eksik Bölüm Kontrolü & Otomatik Tamamlama (/kontrol & /dogrula)
        @self.app.on_message(filters.command(["kontrol", "dogrula", "check"]))
        async def cmd_check(client: Client, message: Message):
            args = message.text.split(maxsplit=1)
            if len(args) < 2:
                await message.reply_text("⚠️ Kullanım: `/kontrol <Dizi Adı>` veya `/kontrol <Dizi Adı> <Sezon No>`\nÖrnek: `/kontrol Mezarlık 2`")
                return

            query = args[1].strip()
            clean_title, s, _ = Downloader.parse_title_season_episode(query)
            target_season = s if f"{s}" in query else None

            status_msg = await message.reply_text(f"🔍 **'{clean_title}'** için eklenti kaynakları taranıyor ve yükleme durumu denetleniyor...")
            
            total_avail, total_up, missing_queued, missing_eps = await self.verify_and_auto_heal(clean_title, target_season)
            
            if total_avail == 0:
                await status_msg.edit_text(f"❌ **'{clean_title}'** için eklentilerde kaynak bulunamadı.")
                return

            rep_text = (
                f"📊 **Bütünlük & Yükleme Raporu:**\n\n"
                f"🎬 **Dizi:** `{clean_title}`\n"
                f"📦 **Kaynakta Bulunan Bölüm:** `{total_avail}`\n"
                f"✅ **Yüklenmiş Bölüm:** `{total_up}`\n"
            )

            if missing_queued > 0:
                m_str = ", ".join([f"S{ms}E{me}" for ms, me in missing_eps[:10]])
                if len(missing_eps) > 10:
                    m_str += f" ve {len(missing_eps)-10} bölüm daha..."
                rep_text += f"\n🚨 **{missing_queued} Eksik Bölüm Tespit Edildi!**\n`{m_str}`\n\n🚀 Tüm eksik bölümler yüksek öncelikle indirme kuyruğuna alındı ve indirme başladı!"
            else:
                rep_text += "\n🎉 **Tüm bölümler eksiksiz ve tam olarak mevcut!** Eksik bölüm yok."

            await status_msg.edit_text(rep_text)

        async def _notify_admins_for_request(req_id: int, req_title: str, u_id: int, u_name: str, p_name: str = ""):
            admin_btn = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Onayla & Yükle", callback_data=f"req_app:{req_id}"),
                    InlineKeyboardButton("❌ Reddet", callback_data=f"req_rej:{req_id}")
                ]
            ])
            src_info = f"\n🌐 **Seçilen Kaynak:** `{p_name}`" if p_name else ""
            admin_msg = (
                f"📥 **Yeni İçerik İsteği Geldi!**\n\n"
                f"👤 **İsteyen:** {u_name} (`{u_id}`)\n"
                f"🎬 **İçerik:** `{req_title}`{src_info}\n"
                f"📌 **İstek No:** `#{req_id}`"
            )
            for adm_id in config.admin_ids:
                try:
                    await self.app.send_message(chat_id=adm_id, text=admin_msg, reply_markup=admin_btn)
                except Exception:
                    pass

        # Callback Handlers (Buton Tıklamaları)
        @self.app.on_callback_query()
        async def handle_callbacks(client: Client, query: CallbackQuery):
            data = query.data
            user_id = query.from_user.id
            user_name = query.from_user.first_name if query.from_user else "Üye"
            user_uname = (query.from_user.username or "").lower() if query.from_user else ""

            is_admin = (
                user_id in config.admin_ids
                or user_uname in ["dark", "dieandrose"]
                or "dark" in user_name.lower()
            )

            # 1. Arama Sonucu Seçimi -> Dizi ise Sezonları, Film ise İndirme Butonunu Getir
            if data.startswith("sel_res:"):
                idx = int(data.split(":")[1])
                user_cache = SEARCH_CACHE.get(str(user_id), [])
                if not user_cache or idx >= len(user_cache):
                    try:
                        await query.answer("⚠️ Arama sonucu süresi doldu, lütfen tekrar arayın.", show_alert=True)
                    except Exception:
                        pass
                    return

                selected_item = user_cache[idx]
                title = selected_item.get("title", "")
                plugin = selected_item.get("plugin_name", "Kaynak")
                url = selected_item.get("url", "")

                try:
                    await query.answer("⏳ Detaylar alınıyor...")
                except Exception:
                    pass

                detail = None
                try:
                    detail = await asyncio.wait_for(local_load_item(plugin, url), timeout=6.0)
                except Exception as load_err:
                    logger.warning(f"Plugin {plugin} load_item zaman aşımı/hata: {load_err}")

                episodes = (detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])) if detail else []
                
                # Dizi Kontrolü: Bölüm listesi var ve birden fazla bölüm veya sezon bilgisi içeriyor mu?
                is_series = bool(episodes and len(episodes) > 0 and (
                    len(episodes) > 1 or 
                    (episodes[0].get("season", 0) if isinstance(episodes[0], dict) else getattr(episodes[0], "season", 0)) > 0 or
                    (episodes[0].get("episode", 0) if isinstance(episodes[0], dict) else getattr(episodes[0], "episode", 0)) > 1 or
                    "/dizi/" in url or "/tv/" in url or "diziler" in url
                ))

                if is_series:
                    seasons = sorted(set(ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1) for ep in episodes))
                    s_buttons = []
                    # En üste TÜM SEZONLARI İNDİR butonu
                    s_buttons.append([InlineKeyboardButton("🔥 TÜM SEZONLARI İNDİR (Tüm Bölümler)", callback_data=f"dl_all_series:{idx}")])
                    row = []
                    for s in seasons:
                        row.append(InlineKeyboardButton(f"{s}. Sezon", callback_data=f"sel_s:{idx}:{s}"))
                        if len(row) == 3:
                            s_buttons.append(row)
                            row = []
                    if row:
                        s_buttons.append(row)
                    
                    # Geri butonu (Arama sonuçlarına dön)
                    s_buttons.append([InlineKeyboardButton("🔙 Arama Sonuçlarına Dön", callback_data="back_search")])

                    try:
                        await query.edit_message_text(
                            f"🎬 **{title}** [{plugin}]\n📌 **Dizi:** {len(seasons)} Sezon, {len(episodes)} Bölüm\n\nLütfen indirmek istediğiniz seçeneği belirleyin:",
                            reply_markup=InlineKeyboardMarkup(s_buttons)
                        )
                    except Exception as e:
                        logger.debug(f"Mesaj düzenleme hatası: {e}")
                else:
                    # Film veya Tek Parça İçerik -> Filmler Konusuna Aktarılacak
                    btn = InlineKeyboardMarkup([
                        [InlineKeyboardButton("📥 FİLMİ İNDİR & YÜKLE", callback_data=f"dl_movie:{idx}")],
                        [InlineKeyboardButton("🔙 Arama Sonuçlarına Dön", callback_data="back_search")]
                    ])
                    try:
                        await query.edit_message_text(
                            f"🎬 **{title}** [{plugin}]\n📌 **Tür:** Film / Tek Parça\n\nBu içerik doğrudan **'🎬 Filmler'** konusuna yüklenecektir.",
                            reply_markup=btn
                        )
                    except Exception as e:
                        logger.debug(f"Mesaj düzenleme hatası: {e}")

            # 1.1 Tüm Sezonları İndirme Tetikleme
            elif data.startswith("dl_all_series:"):
                idx = int(data.split(":")[1])
                user_cache = SEARCH_CACHE.get(str(user_id), [])
                if not user_cache or idx >= len(user_cache):
                    await query.answer("⚠️ Süre aşımı.", show_alert=True)
                    return

                selected_item = user_cache[idx]
                title = selected_item.get("title", "")
                plugin = selected_item.get("plugin_name", "")
                url = selected_item.get("url", "")

                if not is_admin:
                    req_id = db.create_request(user_id=user_id, user_name=user_name, query=f"{title} (Tüm Sezonlar)")
                    await query.answer("📩 İsteğiniz yöneticilere iletildi!", show_alert=True)
                    await query.edit_message_text(f"📩 **{title}** dizisinin tüm sezonlarını indirme talebiniz yöneticilere iletildi! (İstek No: `#{req_id}`)\n👑 Yöneticiler onayladığında otomatik olarak foruma yüklenecektir.")
                    await _notify_admins_for_request(req_id, f"{title} (Tüm Sezonlar)", user_id, user_name, plugin)
                    return

                detail = await local_load_item(plugin, url)
                episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                episodes.sort(key=lambda ep: (
                    ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1),
                    ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                ))

                added = 0
                seasons_set = set()
                for ep in episodes:
                    s = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                    e = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                    db.add_to_queue(title=title, season=s, episode=e, plugin_name=plugin, item_url=url, priority=2)
                    added += 1
                    seasons_set.add(s)

                await query.answer(f"✅ {added} bölüm kuyruğa eklendi!")
                await query.edit_message_text(f"🚀 **{title}** dizisinin **tüm sezonları** ({len(seasons_set)} sezon, {added} bölüm) sırayla indirilip yüklenmek üzere kuyruğa alındı!")
                asyncio.create_task(self.process_queue())

            # 1.2 Film İndirme Tetikleme
            elif data.startswith("dl_movie:"):
                idx = int(data.split(":")[1])
                user_cache = SEARCH_CACHE.get(str(user_id), [])
                if not user_cache or idx >= len(user_cache):
                    await query.answer("⚠️ Süre aşımı.", show_alert=True)
                    return

                selected_item = user_cache[idx]
                title = selected_item.get("title", "")
                plugin = selected_item.get("plugin_name", "")
                url = selected_item.get("url", "")

                if not is_admin:
                    req_id = db.create_request(user_id=user_id, user_name=user_name, query=f"{title} (Film)")
                    await query.answer("📩 İsteğiniz yöneticilere iletildi!", show_alert=True)
                    await query.edit_message_text(f"📩 **{title}** (Film) talebiniz yöneticilere iletildi! (İstek No: `#{req_id}`)\n👑 Yöneticiler onayladığında '🎬 Filmler' konusuna yüklenecektir.")
                    await _notify_admins_for_request(req_id, f"{title} (Film)", user_id, user_name, plugin)
                    return

                job_id = db.add_to_queue(title=title, season=0, episode=0, plugin_name=plugin, item_url=url, priority=3)
                await query.answer("✅ Film kuyruğa eklendi!")
                await query.edit_message_text(f"✅ **{title}** (Film) indirme kuyruğuna alındı! (İşlem ID: `{job_id}`)\n'🎬 Filmler' konusuna yüklenecektir.")
                asyncio.create_task(self.process_queue())

            # 2. Sezon Seçimi -> Bölümleri Getir
            elif data.startswith("sel_s:"):
                _, idx_str, s_str = data.split(":")
                idx, season = int(idx_str), int(s_str)
                user_cache = SEARCH_CACHE.get(str(user_id), [])
                if not user_cache or idx >= len(user_cache):
                    await query.answer("⚠️ Süre aşımı.", show_alert=True)
                    return

                selected_item = user_cache[idx]
                plugin = selected_item.get("plugin_name", "")
                url = selected_item.get("url", "")
                title = selected_item.get("title", "")

                detail = await local_load_item(plugin, url)
                episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                ep_buttons = []
                # En üste SEÇİLİ SEZONU İNDİR butonu
                ep_buttons.append([InlineKeyboardButton(f"📥 {season}. SEZONU İNDİR (Tüm Bölümler)", callback_data=f"dl_all_s:{idx}:{season}")])

                row = []
                for ep in episodes:
                    s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                    e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                    if s_num == season:
                        row.append(InlineKeyboardButton(f"{e_num}. Bölüm", callback_data=f"dl_ep:{idx}:{s_num}:{e_num}"))
                        if len(row) == 4:
                            ep_buttons.append(row)
                            row = []
                if row:
                    ep_buttons.append(row)

                # Geri butonu (Sezonlara dön)
                ep_buttons.append([InlineKeyboardButton("🔙 Sezon Seçimine Dön", callback_data=f"sel_res:{idx}")])

                await query.edit_message_text(
                    f"🎬 **{title}** - **{season}. Sezon**\nSezonun tamamını tek tıkla indirebilir veya tekil bölüm seçebilirsiniz:",
                    reply_markup=InlineKeyboardMarkup(ep_buttons)
                )

            # 2.1 Arama Sonuçlarına Geri Dön
            elif data == "back_search":
                user_cache = SEARCH_CACHE.get(str(user_id), [])
                if not user_cache:
                    await query.answer("⚠️ Arama sonucu süresi doldu, lütfen tekrar arayın.", show_alert=True)
                    return
                buttons = []
                for i_idx, r in enumerate(user_cache[:25]):
                    t_title = r.get("title", "İçerik")
                    p_name = r.get("plugin_name", "Kaynak")
                    buttons.append([InlineKeyboardButton(f"🎬 {t_title} [{p_name}]", callback_data=f"sel_res:{i_idx}")])
                keyboard = InlineKeyboardMarkup(buttons)
                await query.edit_message_text("🎯 **Arama Sonuçları:**\nİndirmek istediğiniz içeriği seçin:", reply_markup=keyboard)

            # 3. Bölüm İndirme Tetikleme
            elif data.startswith("dl_ep:"):
                _, idx_str, s_str, e_str = data.split(":")
                idx, s_num, e_num = int(idx_str), int(s_str), int(e_str)
                user_cache = SEARCH_CACHE.get(str(user_id), [])
                if not user_cache or idx >= len(user_cache):
                    await query.answer("⚠️ Süre aşımı.", show_alert=True)
                    return

                selected_item = user_cache[idx]
                title = selected_item.get("title", "")
                plugin = selected_item.get("plugin_name", "")
                url = selected_item.get("url", "")

                if not is_admin:
                    req_id = db.create_request(user_id=user_id, user_name=user_name, query=f"{title} S{s_num:02d}E{e_num:02d}")
                    await query.answer("📩 İsteğiniz yöneticilere iletildi!", show_alert=True)
                    await query.edit_message_text(f"📩 **{title} S{s_num:02d}E{e_num:02d}** isteğiniz yöneticilere iletildi! (İstek No: `#{req_id}`)\n👑 Yöneticiler onayladığında otomatik yüklenecektir.")
                    await _notify_admins_for_request(req_id, f"{title} S{s_num:02d}E{e_num:02d}", user_id, user_name, plugin)
                    return

                job_id = db.add_to_queue(title=title, season=s_num, episode=e_num, plugin_name=plugin, item_url=url, priority=3)
                await query.answer("✅ Kuyruğa eklendi!")
                await query.edit_message_text(f"✅ **{title} S{s_num:02d}E{e_num:02d}** indirme kuyruğuna alındı! (İşlem ID: `{job_id}`)")
                asyncio.create_task(self.process_queue())

            # 4. Tüm Sezonu İndirme Tetikleme
            elif data.startswith("dl_all_s:"):
                _, idx_str, s_str = data.split(":")
                idx, s_num = int(idx_str), int(s_str)
                user_cache = SEARCH_CACHE.get(str(user_id), [])
                if not user_cache or idx >= len(user_cache):
                    await query.answer("⚠️ Süre aşımı.", show_alert=True)
                    return

                selected_item = user_cache[idx]
                title = selected_item.get("title", "")
                plugin = selected_item.get("plugin_name", "")
                url = selected_item.get("url", "")

                if not is_admin:
                    req_id = db.create_request(user_id=user_id, user_name=user_name, query=f"{title} {s_num}. Sezon")
                    await query.answer("📩 İsteğiniz yöneticilere iletildi!", show_alert=True)
                    await query.edit_message_text(f"📩 **{title} {s_num}. Sezon** indirme talebiniz yöneticilere iletildi! (İstek No: `#{req_id}`)\n👑 Yöneticiler onayladığında otomatik yüklenecektir.")
                    await _notify_admins_for_request(req_id, f"{title} {s_num}. Sezon", user_id, user_name, plugin)
                    return

                detail = await local_load_item(plugin, url)
                episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                episodes.sort(key=lambda ep: (
                    ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1),
                    ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                ))

                added = 0
                for ep in episodes:
                    s = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                    e = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                    if s == s_num:
                        db.add_to_queue(title=title, season=s, episode=e, plugin_name=plugin, item_url=url, priority=2)
                        added += 1

                await query.answer(f"✅ {added} bölüm kuyruğa eklendi!")
                await query.edit_message_text(f"✅ **{title} {s_num}. Sezonun** tüm bölümleri ({added} bölüm) kuyruğa alındı!")
                asyncio.create_task(self.process_queue())

            # 5. Admin İstek Onayı
            elif data.startswith("req_app:"):
                if user_id not in config.admin_ids:
                    await query.answer("⚠️ Sadece yöneticiler onaylayabilir.", show_alert=True)
                    return
                req_id = int(data.split(":")[1])
                req = db.get_request(req_id)
                if not req:
                    await query.answer("İstek bulunamadı.", show_alert=True)
                    return

                clean_title, s, e = Downloader.parse_title_season_episode(req["query"])
                db.update_request_status(req_id, "approved", admin_id=user_id)

                # Sezon tespiti kontrolü
                season_match = re.search(r"(\d+)\s*\.?\s*sezon", req["query"], re.IGNORECASE)
                has_explicit_ep = bool(re.search(r"(\d+)\s*\.?\s*bölüm|s\d+e\d+|e\d+", req["query"], re.IGNORECASE))

                if season_match and not has_explicit_ep:
                    target_s = int(season_match.group(1))
                    results = await Downloader.search_all_plugins(clean_title)
                    queued_count = 0
                    if results:
                        item = results[0]
                        detail = await local_load_item(item.get("plugin_name", ""), item.get("url", ""))
                        episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                        episodes.sort(key=lambda ep: (
                            ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1),
                            ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                        ))
                        for ep in episodes:
                            s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                            e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                            if s_num == target_s:
                                db.add_to_queue(title=clean_title, season=s_num, episode=e_num, priority=3)
                                queued_count += 1
                    if queued_count > 0:
                        await query.edit_message_text(f"✅ **İstek `#{req_id}` Onaylandı!** '{clean_title} {target_s}. Sezon' ({queued_count} bölüm) kuyruğa alındı.")
                    else:
                        job_id = db.add_to_queue(title=clean_title, season=target_s, episode=1, priority=3)
                        await query.edit_message_text(f"✅ **İstek `#{req_id}` Onaylandı!** (İşlem ID: `{job_id}`)")
                elif not has_explicit_ep and not season_match:
                    results = await Downloader.search_all_plugins(clean_title)
                    is_series_found = False
                    item = None
                    episodes = []
                    if results:
                        try:
                            item = results[0]
                            detail = await local_load_item(item.get("plugin_name", ""), item.get("url", ""))
                            episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                            if episodes and (len(episodes) > 1 or "/dizi/" in item.get("url", "")):
                                is_series_found = True
                        except Exception:
                            pass
                    if is_series_found and episodes:
                        episodes.sort(key=lambda ep: (
                            ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1),
                            ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                        ))
                        added = 0
                        seasons_set = set()
                        p_name = item.get("plugin_name", "") if item else ""
                        i_url = item.get("url", "") if item else ""
                        for ep in episodes:
                            s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                            e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                            db.add_to_queue(title=clean_title, season=s_num, episode=e_num, plugin_name=p_name, item_url=i_url, priority=2)
                            added += 1
                            seasons_set.add(s_num)
                        await query.edit_message_text(f"✅ **İstek `#{req_id}` Onaylandı!** '{clean_title}' dizisinin tüm sezonları ({len(seasons_set)} sezon, {added} bölüm) kuyruğa alındı.")
                    else:
                        job_id = db.add_to_queue(title=clean_title, season=0, episode=0, priority=3)
                        await query.edit_message_text(f"✅ **İstek `#{req_id}` Onaylandı!** '{clean_title}' (Film) kuyruğa alındı. (İşlem ID: `{job_id}`)")
                else:
                    job_id = db.add_to_queue(title=clean_title, season=s, episode=e, priority=3)
                    await query.edit_message_text(f"✅ **İstek `#{req_id}` Onaylandı & Kuyruğa Alındı!** (İşlem ID: `{job_id}`)")
                if req["user_id"]:
                    try:
                        await self.app.send_message(
                            chat_id=req["user_id"],
                            text=f"🎉 **Tebrikler!** '{req['query']}' isteğiniz yönetici tarafından onaylandı ve indirilmeye başlandı."
                        )
                    except Exception:
                        pass
                asyncio.create_task(self.process_queue())

            # 6. Admin İstek Reddi
            elif data.startswith("req_rej:"):
                if user_id not in config.admin_ids:
                    await query.answer("⚠️ Sadece yöneticiler reddedebilir.", show_alert=True)
                    return
                req_id = int(data.split(":")[1])
                db.update_request_status(req_id, "rejected", admin_id=user_id)
                await query.edit_message_text(f"❌ **İstek `#{req_id}` Reddedildi.**")

            # 7. Durum Canlı Yenileme Butonu
            elif data == "status_ref":
                text, markup = self.get_system_status_report()
                try:
                    await query.edit_message_text(text, reply_markup=markup)
                    await query.answer("🔄 Durum güncellendi.")
                except Exception:
                    await query.answer("Durum güncel.")

            # 8. Buton ile Tekil İptal
            elif data.startswith("cancel_job:"):
                if user_id not in config.admin_ids:
                    await query.answer("⚠️ İptal yetkisi sadece yöneticidedir.", show_alert=True)
                    return
                c_id = int(data.split(":")[1])
                t = ACTIVE_TASKS.pop(c_id, None)
                if t and not t.done():
                    t.cancel()
                if db.cancel_queue_item(c_id):
                    LIVE_TRANSFERS.pop(c_id, None)
                    for p in TEMP_DIR.glob(f"job_{c_id}_*"):
                        try:
                            p.unlink(missing_ok=True)
                        except Exception:
                            pass
                    await query.answer(f"🛑 İşlem #{c_id} iptal edildi.", show_alert=True)
                else:
                    await query.answer(f"⚠️ İşlem #{c_id} zaten tamamlanmış veya bulunamadı.", show_alert=True)
                
                text, markup = self.get_system_status_report()
                try:
                    await query.edit_message_text(text, reply_markup=markup)
                except Exception:
                    pass

            # 9. Buton ile Tüm Kuyruğu Temizleme
            elif data == "cancel_all":
                if user_id not in config.admin_ids:
                    await query.answer("⚠️ İptal yetkisi sadece yöneticidedir.", show_alert=True)
                    return
                del_count = db.cancel_all_queue()
                for task in list(ACTIVE_TASKS.values()):
                    if task and not task.done():
                        task.cancel()
                ACTIVE_TASKS.clear()
                LIVE_TRANSFERS.clear()
                for p in TEMP_DIR.glob("*"):
                    try:
                        if p.is_file():
                            p.unlink(missing_ok=True)
                    except Exception:
                        pass
                await query.answer(f"🛑 Tüm kuyruk temizlendi ({del_count} işlem durduruldu).", show_alert=True)
                text, markup = self.get_system_status_report()
                try:
                    await query.edit_message_text(text, reply_markup=markup)
                except Exception:
                    pass

    def get_system_status_report(self) -> Tuple[str, InlineKeyboardMarkup]:
        active_jobs = db.get_active_queue()
        if not active_jobs and not LIVE_TRANSFERS:
            text = "🟢 **Sistem Boşta.**\nAktif veya bekleyen indirme/yükleme işlemi yok."
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yenile", callback_data="status_ref")]])
            return text, markup

        text = "📊 **Canlı İndirme & Telegram Yükleme Durumu:**\n\n"
        live_shown = 0
        buttons = []
        
        for j in active_jobs:
            job_id = j["id"]
            t_info = LIVE_TRANSFERS.get(job_id)
            status_label = (j.get("status") or "queued").upper()
            p_val = float(j.get("progress") or 0.0)
            disp_title = f"{j['title']} (Film)" if (j.get('season') == 0 or j.get('season') is None) else f"{j['title']} S{j['season']:02d}E{j['episode']:02d}"

            if t_info and t_info.get("phase") == "uploading":
                cur_mb = (t_info.get("current") or 0) / (1024 * 1024)
                tot_mb = (t_info.get("total") or 0) / (1024 * 1024)
                pct_f = min(1.0, max(0.0, float(t_info.get("progress") or 0.0)))
                pct = int(pct_f * 100)
                p_bar = "▓" * int(pct_f * 10) + "░" * (10 - int(pct_f * 10))
                speed = float(t_info.get("speed_mb") or 0.0)
                rem_sec = int((tot_mb - cur_mb) / max(0.1, speed)) if speed > 0 else 0
                
                text += (
                    f"🎬 **{disp_title}**\n"
                    f"• Aşama: 📤 `TELEGRAM'A YÜKLENİYOR` (MTProto)\n"
                    f"• İlerleme: `[{p_bar}] %{pct}` ({cur_mb:.1f} MB / {tot_mb:.1f} MB)\n"
                    f"• Hız: `⚡ {speed:.1f} MB/s` | Kalan: `⏳ ~{rem_sec} sn`\n"
                    f"• İşlem ID: `#{job_id}`\n\n"
                )
                live_shown += 1
                buttons.append([InlineKeyboardButton(f"❌ #{job_id} İptal Et", callback_data=f"cancel_job:{job_id}")])
            elif t_info and t_info.get("phase") == "muxing":
                text += (
                    f"🎬 **{disp_title}**\n"
                    f"• Aşama: ⚙️ `SES & VİDEO SENKRONİZASYONU` (FFmpeg)\n"
                    f"• Durum: `🔄 İndirme tamamlandı, ses ve video kayıpsız birleştiriliyor...`\n"
                    f"• İşlem ID: `#{job_id}`\n\n"
                )
                live_shown += 1
                buttons.append([InlineKeyboardButton(f"❌ #{job_id} İptal Et", callback_data=f"cancel_job:{job_id}")])
            elif t_info and t_info.get("phase") == "downloading":
                pct_f = min(1.0, max(0.0, float(t_info.get("progress") or p_val)))
                pct = int(pct_f * 100)
                p_bar = "▓" * int(pct_f * 10) + "░" * (10 - int(pct_f * 10))
                cur_seg = t_info.get("current_seg", 0)
                tot_seg = t_info.get("total_seg", 0)
                
                text += (
                    f"🎬 **{disp_title}**\n"
                    f"• Aşama: 📥 `KAYNAKTAN İNDİRİLİYOR` (HLS)\n"
                    f"• İlerleme: `[{p_bar}] %{pct}` ({cur_seg}/{tot_seg} Parça)\n"
                    f"• İşlem ID: `#{job_id}`\n\n"
                )
                live_shown += 1
                buttons.append([InlineKeyboardButton(f"❌ #{job_id} İptal Et", callback_data=f"cancel_job:{job_id}")])
            elif live_shown < 3:
                pct_f = min(1.0, max(0.0, p_val))
                p_bar = "▓" * int(pct_f * 10) + "░" * (10 - int(pct_f * 10))
                text += (
                    f"🎬 **{disp_title}**\n"
                    f"• Durum: ⏳ `{status_label}`\n"
                    f"• İlerleme: `[{p_bar}] %{int(pct_f * 100)}`\n"
                    f"• İşlem ID: `#{job_id}`\n\n"
                )
                live_shown += 1

        queued_remaining = len(active_jobs) - live_shown
        if queued_remaining > 0:
            text += f"📋 **Sırada Bekleyen:** `{queued_remaining}` ek işlem kuyrukta.\n\n"

        # Disk Bilgisi
        try:
            total, used, free = shutil.disk_usage(TEMP_DIR)
            text += f"💾 **Disk Alanı:** `{free / (1024**3):.1f} GB Boş` / `{total / (1024**3):.1f} GB`\n"
        except Exception:
            pass

        # Kontrol Butonları
        ctrl_row = [InlineKeyboardButton("🔄 Canlı Yenile", callback_data="status_ref")]
        if active_jobs:
            ctrl_row.insert(0, InlineKeyboardButton("🛑 Tüm Kuyruğu Temizle", callback_data="cancel_all"))
        buttons.append(ctrl_row)

        markup = InlineKeyboardMarkup(buttons)
        return text, markup

    def _check_disk_space(self):
        try:
            total, used, free = shutil.disk_usage(TEMP_DIR)
            free_gb = free / (1024**3)
            if free_gb < config.min_free_disk_gb:
                logger.warning(f"Düşük disk alanı ({free_gb:.1f} GB), eski temp dosyaları temizleniyor...")
                for f in TEMP_DIR.glob("*"):
                    if f.is_file():
                        try:
                            f.unlink(missing_ok=True)
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"Disk kontrol hatası: {e}")

    async def _process_single_job(self, job: Dict[str, Any]):
        job_id = job["id"]
        title = job["title"]
        season = job["season"]
        episode = job["episode"]

        if db.is_job_cancelled(job_id):
            logger.info(f"İşlem #{job_id} zaten iptal edilmiş, atlanıyor.")
            return

        cur_task = asyncio.current_task()
        if cur_task:
            ACTIVE_TASKS[job_id] = cur_task

        is_movie = (season == 0 and episode == 0) or (season == 0)
        clean_title, _, _ = Downloader.parse_title_season_episode(title)
        
        disp_title = f"{clean_title} (Film)" if is_movie else f"{clean_title} S{season:02d}E{episode:02d}"
        logger.info(f"Kuyruk İşleniyor: #{job_id} | {disp_title}")
        db.update_queue_progress(job_id, "downloading", 0.0)
        self._check_disk_space()

        # 1. Konu ID'sini Bul / Aç (Filmler tekil '🎬 Filmler' konusuna, Diziler kendi dizisi konusuna)
        topic_id = await self.get_or_create_series_topic(clean_title, is_movie=is_movie)

        # 2. Aday Akışları Bul (Önce seçilen plugin ve link, ardından fallback zinciri)
        candidates = []
        p_direct = job.get("plugin_name", "")
        url_direct = job.get("item_url", "")
        
        if p_direct and url_direct:
            try:
                if is_movie:
                    links = await asyncio.wait_for(local_load_links(p_direct, url_direct), timeout=8)
                    for l in links:
                        l_name = l.get("name", "Akış") if isinstance(l, dict) else getattr(l, "name", "Akış")
                        l_url = l.get("url", "") if isinstance(l, dict) else getattr(l, "url", "")
                        l_subs = l.get("subtitles", []) if isinstance(l, dict) else getattr(l, "subtitles", [])
                        if l_url:
                            candidates.append({
                                "plugin": p_direct,
                                "name": l_name,
                                "url": l_url,
                                "ep_url": url_direct,
                                "title": title,
                                "subtitles": l_subs
                            })
                else:
                    detail = await asyncio.wait_for(local_load_item(p_direct, url_direct), timeout=8)
                    episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
                    for ep in episodes:
                        s_num = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                        e_num = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                        ep_url = ep.get("url", "") if isinstance(ep, dict) else getattr(ep, "url", "")
                        if s_num == season and e_num == episode and ep_url:
                            links = await asyncio.wait_for(local_load_links(p_direct, ep_url), timeout=8)
                            for l in links:
                                l_name = l.get("name", "Akış") if isinstance(l, dict) else getattr(l, "name", "Akış")
                                l_url = l.get("url", "") if isinstance(l, dict) else getattr(l, "url", "")
                                l_subs = l.get("subtitles", []) if isinstance(l, dict) else getattr(l, "subtitles", [])
                                if l_url:
                                    candidates.append({
                                        "plugin": p_direct,
                                        "name": l_name,
                                        "url": l_url,
                                        "ep_url": ep_url,
                                        "title": ep.get("title", ""),
                                        "subtitles": l_subs
                                    })
            except Exception as direct_err:
                logger.debug(f"Doğrudan kaynak çözme hatası ({p_direct}): {direct_err}")

        # Eğer doğrudan kaynaktan link bulunamadıysa fallback olarak tüm eklentileri ara
        if not candidates:
            candidates = await Downloader.find_all_candidate_streams(clean_title, season, episode)

        if not candidates:
            logger.warning(f"#{job_id} için akış kaynağı bulunamadı.")
            db.update_queue_progress(job_id, "failed", error_msg="Kaynak akış bulunamadı")
            return

        uploaded_ok = False
        try:
            for cand in candidates:
                if db.is_job_cancelled(job_id):
                    logger.info(f"İşlem #{job_id} iptal edilmiş, akış döngüsü durduruluyor.")
                    break

                p_name = cand["plugin"]
                stream_url = cand["url"]
                logger.info(f"Denenen Kaynak: [{p_name}] -> {stream_url}")

                safe_title = re.sub(r'[^a-zA-Z0-9_\-]', '_', clean_title)
                temp_file = TEMP_DIR / f"job_{job_id}_{safe_title}_{'movie' if is_movie else f'S{season}E{episode}'}.mp4"
                
                def prog_cb(pct: float, done_seg: int = 0, tot_seg: int = 0, phase: str = "downloading"):
                    if db.is_job_cancelled(job_id):
                        raise asyncio.CancelledError(f"İşlem #{job_id} iptal edildi")
                    LIVE_TRANSFERS[job_id] = {
                        "title": disp_title,
                        "phase": phase,
                        "progress": pct,
                        "current_seg": done_seg,
                        "total_seg": tot_seg,
                        "updated_at": time.time()
                    }
                    db.update_queue_progress(job_id, "downloading", 0.73 if phase == "muxing" else pct * 0.7)

                dl_res = await Downloader.download_hls_stream(
                    stream_url, 
                    temp_file, 
                    progress_cb=prog_cb,
                    extra_subtitles=cand.get("subtitles")
                )
                if not dl_res or not dl_res.get("success") or not temp_file.exists():
                    logger.warning(f"[{p_name}] İndirme başarısız oldu, sonraki kaynağa geçiliyor...")
                    continue

                has_multi_audio = dl_res.get("has_multi_audio", False)
                has_subtitles = dl_res.get("has_subtitles", False)

                audio_badge = "🇹🇷 Türkçe Dublaj | 🇬🇧 Orijinal" if has_multi_audio else "🇹🇷 Türkçe Dublaj"
                sub_badge = "\n💬 **Altyazı:** 🇹🇷 Türkçe (Açılıp Kapanabilir / CC)" if has_subtitles else ""

                if db.is_job_cancelled(job_id):
                    break

                # Boyut Kontrolü (< 1950 MB) - Aşarsa Otomatik Optimize Et
                f_size = temp_file.stat().st_size
                if f_size > config.max_file_size_bytes:
                    logger.info(f"[{p_name}] Dosya boyutu Telegram limitini aşıyor ({f_size / (1024*1024):.1f} MB), <1.9GB için optimize ediliyor...")
                    opt_file = temp_file.with_name(f"opt_{temp_file.name}")
                    opt_ok = await Downloader.compress_video_to_limit(temp_file, opt_file, target_mb=1850)
                    if opt_ok and opt_file.exists():
                        temp_file.unlink(missing_ok=True)
                        temp_file = opt_file
                        f_size = temp_file.stat().st_size
                        logger.info(f"[{p_name}] Video optimize edildi: {f_size / (1024*1024):.1f} MB")
                    else:
                        logger.warning(f"[{p_name}] Optimizasyon başarısız, alternatif aranıyor...")
                        temp_file.unlink(missing_ok=True)
                        continue

                # Thumbnail Çıkart
                thumb_path = await Downloader.extract_thumbnail(temp_file)

                if db.is_job_cancelled(job_id):
                    break

                # Telegram'a Yükle (Canlı İlerleme Takibi)
                db.update_queue_progress(job_id, "uploading", 0.75)
                logger.info(f"Telegram Konusuna Yükleniyor: '{disp_title}' (Topic: {topic_id})")

                upload_start = time.time()
                last_db_up = 0.0

                async def upload_prog(current: int, total: int):
                    if db.is_job_cancelled(job_id):
                        raise asyncio.CancelledError(f"İşlem #{job_id} iptal edildi")
                    nonlocal last_db_up
                    now = time.time()
                    pct = current / total if total > 0 else 0.0
                    elapsed = max(0.1, now - upload_start)
                    speed_mb = (current / (1024 * 1024)) / elapsed
                    LIVE_TRANSFERS[job_id] = {
                        "title": disp_title,
                        "phase": "uploading",
                        "current": current,
                        "total": total,
                        "speed_mb": speed_mb,
                        "progress": pct,
                        "updated_at": now
                    }
                    if now - last_db_up >= 3.0 or current == total:
                        last_db_up = now
                        db.update_queue_progress(job_id, "uploading", 0.75 + pct * 0.25)

                if is_movie:
                    caption = (
                        f"🎬 **{clean_title}**\n\n"
                        f"📌 **Tür:** Film\n"
                        f"📦 **Boyut:** {f_size / (1024*1024):.1f} MB\n\n"
                        f"🌐 **Daha Fazlası İçin :**  izle.darkbox.com.tr:9443"
                    )
                else:
                    caption = (
                        f"🎬 **{clean_title}**\n"
                        f"📌 **{season}. Sezon {episode}. Bölüm**\n"
                        f"📦 **Boyut:** {f_size / (1024*1024):.1f} MB\n\n"
                        f"🌐 **Daha Fazlası İçin :**  izle.darkbox.com.tr:9443"
                    )

                try:
                    sent_msg = await self.app.send_video(
                        chat_id=config.target_chat_id,
                        video=str(temp_file),
                        caption=caption,
                        thumb=str(thumb_path) if thumb_path else None,
                        supports_streaming=True,
                        reply_to_message_id=topic_id if topic_id > 0 else None,
                        progress=upload_prog
                    )
                    msg_id = sent_msg.id if sent_msg else 0
                    db.log_upload(p_name, cand.get("ep_url", ""), disp_title, season, episode, "uploaded", msg_id, f_size)
                    db.update_queue_progress(job_id, "completed", 1.0)
                    uploaded_ok = True
                    logger.info(f"✅ Başarıyla Yüklendi! Mesaj ID: {msg_id}")
                    
                    # İşlem Sonrası Otomatik Eksik Bölüm Kontrolü (Sadece Diziler için)
                    if not is_movie:
                        asyncio.create_task(self._auto_heal_hook(clean_title, season))
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as upload_err:
                    logger.error(f"Telegram yükleme hatası: {upload_err}")
                finally:
                    if temp_file.exists():
                        temp_file.unlink(missing_ok=True)
                    if thumb_path and thumb_path.exists():
                        thumb_path.unlink(missing_ok=True)

            if not uploaded_ok and not db.is_job_cancelled(job_id):
                db.update_queue_progress(job_id, "failed", error_msg="Tüm alternatif akışlar başarısız oldu")
        except asyncio.CancelledError:
            logger.info(f"🛑 İşlem #{job_id} ({disp_title}) iptal edildi ve anında durduruldu.")
            db.cancel_queue_item(job_id)
            LIVE_TRANSFERS.pop(job_id, None)
            for p in TEMP_DIR.glob(f"job_{job_id}_*"):
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass
        finally:
            ACTIVE_TASKS.pop(job_id, None)
            LIVE_TRANSFERS.pop(job_id, None)

    async def _auto_heal_hook(self, series_title: str, season: int):
        """Yükleme tamamlandıktan sonra arka planda eksik bölüm var mı kontrol eder ve kuyruğa alır."""
        try:
            await asyncio.sleep(5)
            _, _, missing_count, _ = await self.verify_and_auto_heal(series_title, target_season=season)
            if missing_count > 0:
                logger.info(f"Auto-Heal: {series_title} Sezon {season} için {missing_count} eksik bölüm kuyruğa eklendi.")
        except Exception as e:
            logger.debug(f"Auto-heal hook hatası: {e}")

    async def verify_and_auto_heal(self, series_title: str, target_season: Optional[int] = None) -> Tuple[int, int, int, List[Tuple[int, int]]]:
        """
        Dizinin eklentideki tüm bölümlerini veritabanındaki yüklenenlerle karşılaştırır.
        Eksik bölümleri otomatik olarak tespit edip yüksek öncelikle kuyruğa ekler.
        Döner: (toplam_bölüm, yüklenmiş_bölüm, kuyruğa_eklenen_eksik_sayısı, eksik_bölümler_listesi)
        """
        clean_title, _, _ = Downloader.parse_title_season_episode(series_title)
        results = await Downloader.search_all_plugins(clean_title)
        if not results:
            return 0, 0, 0, []

        best_item = results[0]
        plugin_name = best_item.get("plugin_name", "")
        item_url = best_item.get("url", "")

        try:
            detail = await local_load_item(plugin_name, item_url)
        except Exception as e:
            logger.error(f"verify_and_auto_heal detail hatası: {e}")
            return 0, 0, 0, []

        episodes = detail.get("episodes", []) if isinstance(detail, dict) else getattr(detail, "episodes", [])
        if not episodes:
            return 0, 0, 0, []

        uploaded_set = db.get_uploaded_episodes_for_series(clean_title)
        
        all_candidate_eps = []
        for ep in episodes:
            s = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
            e = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
            if target_season is not None and s != target_season:
                continue
            all_candidate_eps.append((s, e))

        all_candidate_eps.sort(key=lambda x: (x[0], x[1]))

        missing_eps = []
        queued_count = 0

        for s, e in all_candidate_eps:
            if (s, e) not in uploaded_set:
                missing_eps.append((s, e))
                if not db.is_job_in_queue(clean_title, s, e):
                    db.add_to_queue(title=clean_title, season=s, episode=e, priority=3)
                    queued_count += 1

        if queued_count > 0:
            asyncio.create_task(self.process_queue())

        total_avail = len(all_candidate_eps)
        total_up = total_avail - len(missing_eps)
        return total_avail, total_up, queued_count, missing_eps

    async def process_queue(self):
        """Kuyruktaki işleri çoklu eşzamanlı işçi havuzuyla (multi-worker) işler."""
        if self.is_processing_queue:
            return

        self.is_processing_queue = True
        sem = asyncio.Semaphore(config.max_concurrent_workers)

        async def _worker():
            while True:
                job = None
                async with sem:
                    job = db.get_next_queue_item()
                    if not job:
                        break
                    try:
                        await self._process_single_job(job)
                    except Exception as e:
                        logger.error(f"İşleme hatası (Job {job.get('id')}): {e}")
                await asyncio.sleep(1)

        try:
            workers = [_worker() for _ in range(config.max_concurrent_workers)]
            await asyncio.gather(*workers)
        finally:
            self.is_processing_queue = False
