#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DiziBot Veritabanı Modülü (SQLite)
"""

import sqlite3
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from config import DB_FILE


class Database:
    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            cur = conn.cursor()
            
            # Yüklemeler Tablosu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plugin TEXT,
                    item_url TEXT,
                    title TEXT,
                    season INTEGER,
                    episode INTEGER,
                    status TEXT,
                    tg_message_id INTEGER,
                    file_size INTEGER,
                    timestamp REAL
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_uploads_item ON uploads(plugin, item_url, season, episode)")

            # Forum Konuları Tablosu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_title TEXT UNIQUE,
                    topic_id INTEGER,
                    created_at REAL
                )
            """)

            # Otomatik Takip Listesi (Watchlist)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    series_name TEXT UNIQUE,
                    last_season INTEGER DEFAULT 1,
                    last_episode INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    added_by INTEGER,
                    created_at REAL
                )
            """)

            # Üye İstekleri Tablosu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    user_name TEXT,
                    query TEXT,
                    season INTEGER DEFAULT 0,
                    episode INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending', -- pending, approved, rejected, completed, failed
                    admin_id INTEGER DEFAULT 0,
                    topic_id INTEGER DEFAULT 0,
                    tg_message_id INTEGER DEFAULT 0,
                    created_at REAL
                )
            """)

            # İndirme ve Yükleme Kuyruğu
            cur.execute("""
                CREATE TABLE IF NOT EXISTS download_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    season INTEGER,
                    episode INTEGER,
                    plugin_name TEXT,
                    item_url TEXT,
                    status TEXT DEFAULT 'queued', -- queued, downloading, uploading, completed, failed, cancelled
                    priority INTEGER DEFAULT 1,
                    progress REAL DEFAULT 0.0,
                    error_msg TEXT,
                    created_at REAL,
                    updated_at REAL
                )
            """)
            conn.commit()

    @staticmethod
    def _norm_title(t: str) -> str:
        s = t.lower().strip()
        # Emojileri ve özel sembolleri temizle
        s = s.replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ş", "s").replace("ö", "o").replace("ç", "c")
        s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    # --- Topic Yönetimi ---
    def get_topic_id(self, series_title: str) -> Optional[int]:
        norm = self._norm_title(series_title)
        if not norm:
            return None
        with self._get_conn() as conn:
            cur = conn.cursor()
            # 1. Birebir normalize eşleşme
            cur.execute("SELECT topic_id FROM topics WHERE series_title = ?", (norm,))
            row = cur.fetchone()
            if row:
                return row["topic_id"]

            # 2. Esnek içerik eşleşmesi
            cur.execute("SELECT topic_id, series_title FROM topics")
            all_topics = cur.fetchall()
            for r in all_topics:
                t_norm = r["series_title"]
                if t_norm == norm or (len(norm) >= 4 and norm in t_norm) or (len(t_norm) >= 4 and t_norm in norm):
                    return r["topic_id"]

            return None

    def save_topic(self, series_title: str, topic_id: int):
        norm = self._norm_title(series_title)
        if not norm or topic_id <= 0:
            return
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO topics (series_title, topic_id, created_at) VALUES (?, ?, ?)",
                (norm, topic_id, time.time())
            )
            conn.commit()

    # --- Yükleme Durumu ---
    def is_uploaded(self, plugin: str, item_url: str, season: int = 0, episode: int = 0) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM uploads WHERE plugin = ? AND item_url = ? AND season = ? AND episode = ? AND status = 'uploaded'",
                (plugin, item_url, season, episode)
            )
            return cur.fetchone() is not None

    def get_uploaded_episodes_for_series(self, title: str) -> set:
        norm = self._norm_title(title)
        uploaded = set()
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT title, season, episode FROM uploads WHERE status = 'uploaded'")
            rows = cur.fetchall()
            for r in rows:
                if norm in self._norm_title(r["title"]):
                    uploaded.add((int(r["season"]), int(r["episode"])))
        return uploaded

    def is_title_ep_uploaded(self, title: str, season: int, episode: int) -> bool:
        return (int(season), int(episode)) in self.get_uploaded_episodes_for_series(title)

    def is_job_in_queue(self, title: str, season: int, episode: int) -> bool:
        norm = self._norm_title(title)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, title FROM download_queue WHERE season = ? AND episode = ? AND status IN ('queued', 'claimed', 'downloading', 'uploading')",
                (season, episode)
            )
            rows = cur.fetchall()
            for r in rows:
                if norm in self._norm_title(r["title"]):
                    return True
        return False

    def log_upload(self, plugin: str, item_url: str, title: str, season: int, episode: int, status: str, tg_msg_id: int = 0, file_size: int = 0):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO uploads (plugin, item_url, title, season, episode, status, tg_message_id, file_size, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (plugin, item_url, title, season, episode, status, tg_msg_id, file_size, time.time())
            )
            conn.commit()

    # --- Watchlist (Otomatik Takip) ---
    def add_to_watchlist(self, series_name: str, added_by: int = 0) -> bool:
        norm = self._norm_title(series_name)
        with self._get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO watchlist (series_name, is_active, added_by, created_at) VALUES (?, 1, ?, ?)",
                    (norm, added_by, time.time())
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                cur.execute("UPDATE watchlist SET is_active = 1 WHERE series_name = ?", (norm,))
                conn.commit()
                return True

    def remove_from_watchlist(self, series_name: str) -> bool:
        norm = self._norm_title(series_name)
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM watchlist WHERE series_name = ? OR ? LIKE '%' || series_name || '%'", (norm, norm))
            conn.commit()
            return cur.rowcount > 0

    def get_watchlist(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM watchlist WHERE is_active = 1 ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]

    # --- İstek Sistemi ---
    def create_request(self, user_id: int, user_name: str, query: str, season: int = 0, episode: int = 0) -> int:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO requests (user_id, user_name, query, season, episode, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (user_id, user_name, query, season, episode, time.time())
            )
            conn.commit()
            return cur.lastrowid or 0

    def get_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM requests WHERE id = ?", (request_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def update_request_status(self, request_id: int, status: str, admin_id: int = 0, topic_id: int = 0, tg_msg_id: int = 0):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE requests 
                SET status = ?, admin_id = CASE WHEN ? > 0 THEN ? ELSE admin_id END,
                    topic_id = CASE WHEN ? > 0 THEN ? ELSE topic_id END,
                    tg_message_id = CASE WHEN ? > 0 THEN ? ELSE tg_message_id END
                WHERE id = ?
                """,
                (status, admin_id, admin_id, topic_id, topic_id, tg_msg_id, tg_msg_id, request_id)
            )
            conn.commit()

    def get_pending_requests(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM requests WHERE status = 'pending' ORDER BY created_at ASC")
            return [dict(r) for r in cur.fetchall()]

    # --- Kuyruk Yönetimi ---
    def add_to_queue(self, title: str, season: int, episode: int, plugin_name: str = "", item_url: str = "", priority: int = 1) -> int:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO download_queue (title, season, episode, plugin_name, item_url, status, priority, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (title, season, episode, plugin_name, item_url, priority, time.time(), time.time())
            )
            conn.commit()
            return cur.lastrowid or 0

    def reset_stale_queue_items(self):
        """Sunucu/bot yeniden başladığında yarım kalan (claimed/downloading) işleri 'queued' durumuna çeker."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE download_queue SET status = 'queued', progress = 0.0 WHERE status IN ('claimed', 'downloading', 'uploading')"
            )
            conn.commit()

    def has_pending_jobs(self) -> bool:
        """Kuyrukta bekleyen 'queued' durumunda iş olup olmadığını kontrol eder (claim etmeden)."""
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM download_queue WHERE status = 'queued' LIMIT 1")
            return cur.fetchone() is not None

    def get_next_queue_item(self) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM download_queue WHERE status = 'queued' ORDER BY priority DESC, season ASC, episode ASC, id ASC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                job_id = row["id"]
                cur.execute("UPDATE download_queue SET status = 'claimed', updated_at = ? WHERE id = ?", (time.time(), job_id))
                conn.commit()
                return dict(row)
            return None

    def update_queue_progress(self, job_id: int, status: str, progress: float = 0.0, error_msg: Optional[str] = None):
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE download_queue SET status = ?, progress = ?, error_msg = ?, updated_at = ? WHERE id = ?",
                (status, progress, error_msg, time.time(), job_id)
            )
            conn.commit()

    def get_active_queue(self) -> List[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM download_queue WHERE status IN ('queued', 'downloading', 'uploading') ORDER BY priority DESC, created_at ASC"
            )
            return [dict(r) for r in cur.fetchall()]

    def cancel_queue_item(self, job_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE download_queue SET status = 'cancelled', updated_at = ? WHERE id = ? AND status IN ('queued', 'downloading', 'uploading')", (time.time(), job_id))
            conn.commit()
            return cur.rowcount > 0

    def get_queue_item(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM download_queue WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def is_job_cancelled(self, job_id: int) -> bool:
        item = self.get_queue_item(job_id)
        if not item:
            return True
        return item.get("status") == "cancelled"

    def cancel_all_queue(self) -> int:
        with self._get_conn() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE download_queue SET status = 'cancelled', updated_at = ? WHERE status IN ('queued', 'downloading', 'uploading')", (time.time(),))
            deleted = cur.rowcount
            conn.commit()
            return deleted

db = Database()
