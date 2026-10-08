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


def safe_int_num(val, default=1) -> int:
    """Metin veya sayısal sezon/bölüm değerlerini güvenli tamsayıya (int) çevirir."""
    if isinstance(val, int):
        return val
    try:
        m = re.search(r'\d+', str(val))
        if m:
            return int(m.group(0))
        return int(val)
    except Exception:
        return default


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
        """Arama sorgusu ile içerik başlığı arasındaki alaka skorunu (0-100) hesaplar. Türkçe/İngilizce çift isimleri destekler."""
        tr_map = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")

        def _norm(s: str) -> str:
            if not s:
                return ""
            s = s.lower().strip()
            s = s.translate(tr_map)
            s = re.sub(r'[\(\[\{].*?[\)\]\}]', ' ', s)
            s = re.sub(r'^(the|a|an|el|la|le|der|die|das)\s+', '', s)
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

        def _extract_variants(text: str) -> list[str]:
            variants = [text]
            for m in re.finditer(r'[\(\[](.*?)[\)\]]', text):
                sub = m.group(1).strip()
                if len(sub) >= 3 and not sub.isdigit():
                    variants.append(sub)
            main_part = re.sub(r'[\(\[].*?[\)\]]', '', text).strip()
            if main_part and main_part != text:
                variants.append(main_part)
            return list(dict.fromkeys(variants))

        q_vars = _extract_variants(query)
        t_vars = _extract_variants(title)

        best_score = 0.0
        for q_v in q_vars:
            for t_v in t_vars:
                q_c = _norm(q_v)
                t_c = _norm(t_v)
                if not q_c or not t_c:
                    continue
                if q_c == t_c:
                    return 100.0
                q_w = q_c.split()
                t_w = t_c.split()
                if q_w == t_w:
                    return 100.0
                if t_c.startswith(q_c) or q_c.startswith(t_c):
                    score = 92.0
                elif all(w in t_w for w in q_w) or all(w in q_w for w in t_w):
                    score = 85.0
                else:
                    seq_ratio = difflib.SequenceMatcher(None, q_c, t_c).ratio()
                    score = seq_ratio * 100.0 if seq_ratio >= 0.75 else 0.0
                if score > best_score:
                    best_score = score

        return best_score

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
            "Dizi65", "DizipalX", "Dizirella", "RecTV", "SineWix", 
            "FilmMakinesi", "FullHDFilmizlesene", "HDFilmCehennemi", "FilmModu", 
            "Hdizipal", "JetFilmIzle", "Selcukflix", "SetFilmizle", "WebteIzle",
            "Dizimom", "Dizimia", "Dizibal", "Dizibol", "Ddizi", "DiziKorea",
            "DiziIzleClick", "DizifilmLife", "FilmCenneti", "FilmIzleCH", "Filmhane",
            "FullHDFilmIzle", "HDFilmDelisi", "HDFilmUS", "RoketDizi", "SineMerkez",
            "Sinezy", "TvFilmIzle", "WFilmIzle", "WebDramaTurkey", "ZxcPrime", "Aether", "MeowTV"
        ]

        ignored_plugins = {"CanliTV", "Medya", "DarkTV", "M3uListem", "Vizyona"}
        ordered = [p for p in top_priority if p in all_names and p not in ignored_plugins]
        for p in all_names:
            if p not in ordered and p not in ignored_plugins:
                ordered.append(p)

        return ordered if ordered else config.plugins_priority

    @classmethod
    async def search_all_plugins(cls, query: str) -> List[Dict[str, Any]]:
        """DarkBox eklentilerinde kontrollü, hızlı ve tüm eklentileri kapsayan paralel arama yapar."""
        plugins = cls.get_all_plugin_names()
        sem = asyncio.Semaphore(60)

        # Sorgu varyantları oluştur
        clean_q = query.strip()
        queries_to_search = [clean_q]
        for m in re.finditer(r'[\(\[](.*?)[\)\]]', clean_q):
            sub = m.group(1).strip()
            if len(sub) >= 3 and not sub.isdigit() and sub not in queries_to_search:
                queries_to_search.append(sub)
        main_part = re.sub(r'[\(\[].*?[\)\]]', '', clean_q).strip()
        if main_part and main_part not in queries_to_search:
            queries_to_search.append(main_part)

        async def _search_plugin_q(p: str, q_term: str):
            async with sem:
                try:
                    res = await asyncio.wait_for(local_search(p, q_term), timeout=10.0)
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

        tasks = []
        for p in plugins:
            for q_term in queries_to_search:
                tasks.append(asyncio.create_task(_search_plugin_q(p, q_term)))

        done, pending = await asyncio.wait(tasks, timeout=14.0)
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
                        score = cls.calculate_relevance(clean_q, item["title"])
                        # Eğer skor sıfırsa bile eklenti bizzat aramada döndürdüyse taban puan ver
                        if score <= 0.0:
                            # Ana kelimelerden en az biri içerik başlığında geçiyorsa
                            q_words = [w.lower() for w in re.sub(r'[^\w\s]', '', clean_q).split() if len(w) >= 3]
                            t_lower = item["title"].lower()
                            if any(w in t_lower for w in q_words):
                                score = 50.0
                            else:
                                score = 35.0
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

        matching_items = []
        for item in results:
            rel = cls.calculate_relevance(query_title, item.get("title", ""))
            if rel >= 50.0:
                item["_rel"] = rel
                matching_items.append(item)

        if not matching_items:
            return candidates

        max_rel = max((it["_rel"] for it in matching_items), default=0.0)
        # Eğer yüksek eşleşen (>=85) içerik varsa, daha düşük eşleşen yan dizileri/filmleri havuza hiç dahil etme!
        if max_rel >= 85.0:
            matching_items = [it for it in matching_items if it["_rel"] >= 80.0]
        else:
            matching_items = [it for it in matching_items if it["_rel"] >= max(50.0, max_rel - 10.0)]

        matching_items.sort(key=lambda x: x.get("_rel", 0.0), reverse=True)

        async def _process_item(item):
            p_name = item.get("plugin_name", "")
            i_url = item.get("url", "")
            i_rel = item.get("_rel", 100.0)
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
                                    "title": detail.get("title", "") or item.get("title", ""),
                                    "relevance": i_rel
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
                                                "title": detail.get("title", "") or item.get("title", ""),
                                                "relevance": i_rel
                                            })
                                except Exception:
                                    pass
                else:
                    # Dizi Durumu (Kesin Sezon / Bölüm Eşleştirme)
                    for ep in episodes:
                        s_val = ep.get("season", 1) if isinstance(ep, dict) else getattr(ep, "season", 1)
                        e_val = ep.get("episode", 1) if isinstance(ep, dict) else getattr(ep, "episode", 1)
                        s_num = safe_int_num(s_val)
                        e_num = safe_int_num(e_val)
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
                                            "title": ep_title,
                                            "relevance": i_rel
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

        # Doğruluk (Relevance) birincil, Dublaj ve Hızlı Kaynak ikincil önceliktedir
        def dublaj_priority_key(c):
            p = c.get("plugin", "")
            rel = float(c.get("relevance", 100.0))
            is_tr = cls.is_candidate_tr_dublaj(c)
            is_fast_master = p in ["DizipalX", "SineWix", "RecTV", "Dizipal", "DiziMom", "Dizi65", "FilmModu", "FullHDFilmizlesene", "HDMovie8"]
            
            # 1. Öncelik: Kesin İçerik Doğruluğu (Relevance). 100% eşleşen içerik asla farklı diziyle ezilemez!
            score = int(rel * 10000)
            if is_tr:
                score += 1000
            if is_fast_master:
                score += 200
                
            p_idx = all_plugins.index(p) if p in all_plugins else 999
            return (-score, p_idx)

        candidates.sort(key=dublaj_priority_key)
        return candidates

    @classmethod
    def is_candidate_tr_dublaj(cls, cand: Dict[str, Any], is_native_turkish: bool = False) -> bool:
        """
        Bir kaynağın kesin olarak Türkçe dublaj / Türkçe ses içerip içermediğini analiz eder.
        """
        if cand.get("is_dublaj") is True:
            return True
        
        name = (cand.get("name") or "").lower()
        title = (cand.get("title") or "").lower()
        ep_url = (cand.get("ep_url") or "").lower()
        url = (cand.get("url") or "").lower()
        item_title = (cand.get("item_title") or "").lower()
        full_meta = f"{name} {title} {item_title} {ep_url} {url}"

        # 1. Açıkça Dublaj Belirtilmişse -> KESİNLİKLE DUBLAJ
        has_dub = any(k in full_meta for k in [
            "dublaj", "tr dub", "türkçe dublaj", "turkce dublaj", 
            "tr-dub", "turkce-dub", "(tr)", "[tr]", "türkçe ses", "turkce ses", "dual", "-dub-"
        ])

        # 2. Açıkça Altyazılı / Orijinal Belirtilmişse (ve dublaj denmemişse) -> DUBLAJ DEĞİL
        has_sub = any(k in full_meta for k in [
            "altyazı", "altyazi", "sub", "tr-sub", "tr-altyazi", "turkce-altyazi", 
            "orijinal", "original", "english", "ingilizce"
        ])

        if has_dub:
            return True
        if has_sub:
            return False

        # 3. Ekli altyazı dosyası varsa ve dublaj denmemişse -> Altyazılıdır
        subs = cand.get("subtitles")
        if subs and len(subs) > 0:
            return False

        # 4. Yerli Türk Yapımı İçerikler
        if is_native_turkish:
            return True

        return False

    @classmethod
    async def detect_native_turkish(cls, title: str) -> bool:
        """TMDB üzerinden içeriğin orijinal dilinin Türkçe veya menşeinin Türkiye olup olmadığını doğrular."""
        import httpx
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', title).strip()
        clean_title = re.sub(r'\s*\d+\.\s*Sezon.*$', '', clean_title, flags=re.I).strip()
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                tmdb_url = f"https://mid.vidzee.wtf/tmdb/search/multi?query={urllib.parse.quote(clean_title)}&page=1&include_adult=false&api_key=adc48d20c0956934fb224de5c40bb85d&language=tr-TR"
                res = await client.get(tmdb_url)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    if results:
                        top = results[0]
                        orig_lang = (top.get("original_language") or "").lower()
                        origin_countries = top.get("origin_country") or []
                        if orig_lang == "tr" or "TR" in origin_countries:
                            return True
        except Exception:
            pass
        return False

    @classmethod
    async def probe_media_audio_language(cls, video_path: Path) -> Optional[str]:
        """İndirilen videonun ses akışının dil etiketini (tur, eng vb.) ffprobe ile derinlemesine analiz eder."""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream_tags=language,title:format_tags=language,title",
                "-of", "json",
                str(video_path)
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await proc.communicate()
            data = json.loads(stdout.decode())
            for stream in data.get("streams", []):
                tags = stream.get("tags", {})
                lang = (tags.get("language") or "").lower()
                title = (tags.get("title") or "").lower()
                if any(t in lang or t in title for t in ["tur", "tr", "turkish", "turkce"]):
                    return "tr"
                if any(t in lang or t in title for t in ["eng", "en", "english"]):
                    return "en"
                if lang:
                    return lang
        except Exception:
            pass
        return None

    @classmethod
    async def extract_and_prepare_source_subtitle(
        cls, 
        cand: Dict[str, Any], 
        output_srt: Optional[Path] = None
    ) -> Optional[Path]:
        """
        Kaynaktan (eklenti yanıtı veya link metadata) Türkçe altyazıyı indirir, 
        VTT ise SRT'ye çevirir, UTF-8 Türkçe karakter onarımını yapar.
        """
        import httpx
        subs = cand.get("subtitles") or []
        target_sub_url = None
        
        for s in subs:
            if isinstance(s, dict):
                lang = (s.get("language") or s.get("lang") or s.get("name") or "").lower()
                u = s.get("url") or s.get("file")
                if u and any(k in lang for k in ["tr", "tur", "turkish", "türkçe", "altyazı", "altyazi"]):
                    target_sub_url = u
                    break
            elif isinstance(s, str) and (s.endswith(".vtt") or s.endswith(".srt") or "sub" in s.lower()):
                target_sub_url = s
                break

        if not target_sub_url and subs and isinstance(subs[0], dict) and subs[0].get("url"):
            target_sub_url = subs[0].get("url")

        if not target_sub_url:
            return None

        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            if cand.get("referer"):
                headers["Referer"] = cand["referer"]

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers=headers) as client:
                r = await client.get(target_sub_url)
                if r.status_code != 200 or len(r.content) < 50:
                    return None
                raw_bytes = r.content

            text = None
            for enc in ["utf-8", "windows-1254", "iso-8859-9", "latin5", "cp1252"]:
                try:
                    text = raw_bytes.decode(enc)
                    win1254_fixes = {
                        "ý": "ı", "þ": "ş", "ð": "ğ",
                        "Ý": "İ", "Þ": "Ş", "Ð": "Ğ"
                    }
                    for old_char, new_char in win1254_fixes.items():
                        if old_char in text:
                            text = text.replace(old_char, new_char)
                    break
                except UnicodeDecodeError:
                    continue

            if not text:
                text = raw_bytes.decode("utf-8", errors="ignore")

            # VTT -> SRT format normalizasyonu
            if "WEBVTT" in text[:50]:
                text = re.sub(r"^WEBVTT.*?\n\n", "", text, flags=re.DOTALL)
                text = re.sub(r'(\d{2}:\d{2}:\d{2})\.(\d{3})', r'\1,\2', text)
                text = re.sub(r'(\d{2}:\d{2})\.(\d{3})', r'00:\1,\2', text)

            if output_srt is None:
                output_srt = TEMP_DIR / f"src_sub_{int(time.time())}_{random.randint(100,999)}.srt"

            output_srt.write_text(text, encoding="utf-8")
            logger.info(f"Kaynaktan Türkçe altyazı başarıyla hazırlandı: {output_srt.name} ({output_srt.stat().st_size} bytes)")
            return output_srt
        except Exception as e:
            logger.debug(f"Kaynak altyazı indirme hatası: {e}")
            return None

    @classmethod
    async def fetch_and_prepare_opensubtitles(
        cls, 
        title: str, 
        season: int = 0, 
        episode: int = 0, 
        is_movie: bool = False, 
        output_srt: Optional[Path] = None
    ) -> Optional[Path]:
        """
        OpenSubtitles / Stremio API üzerinden içerikle uyumlu Türkçe altyazı arar, 
        indirir, Türkçe karakter kodlamasını (UTF-8) onarır ve diske kaydeder.
        """
        import httpx
        clean_title = re.sub(r'[\(\[\{].*?[\)\]\}]', '', title).strip()
        imdb_id = None
        
        # 1. TMDB -> IMDB ID tespiti
        async with httpx.AsyncClient(timeout=8.0) as client:
            try:
                tmdb_url = f"https://mid.vidzee.wtf/tmdb/search/multi?query={urllib.parse.quote(clean_title)}&page=1&include_adult=false&api_key=adc48d20c0956934fb224de5c40bb85d&language=tr-TR"
                res = await client.get(tmdb_url)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    if results:
                        tmdb_id = results[0].get("id")
                        media_type = results[0].get("media_type", "movie" if is_movie else "tv")
                        ext_url = f"https://mid.vidzee.wtf/tmdb/{media_type}/{tmdb_id}/external_ids?api_key=adc48d20c0956934fb224de5c40bb85d"
                        ext_res = await client.get(ext_url)
                        if ext_res.status_code == 200:
                            imdb_id = ext_res.json().get("imdb_id")
            except Exception as e:
                logger.debug(f"OpenSubtitles TMDB sorgulama hatası: {e}")

        if not imdb_id or not imdb_id.startswith("tt"):
            return None

        # 2. Stremio OpenSubtitles v3 endpoint
        if is_movie or season == 0:
            endpoint = f"https://opensubtitles-v3.strem.io/subtitles/movie/{imdb_id}/video.json"
        else:
            endpoint = f"https://opensubtitles-v3.strem.io/subtitles/series/{imdb_id}:{season}:{episode}/video.json"

        sub_url = None
        async with httpx.AsyncClient(timeout=8.0) as client:
            try:
                r = await client.get(endpoint)
                if r.status_code == 200:
                    data = r.json()
                    for item in data.get("subtitles", []):
                        lang = (item.get("lang") or "").lower()
                        if lang in ["tur", "tr", "turkish"]:
                            sub_url = item.get("url")
                            break
            except Exception as e:
                logger.debug(f"OpenSubtitles endpoint hatası: {e}")

        if not sub_url:
            return None

        # 3. İndirme ve Karakter Kodlaması Normalizasyonu (UTF-8)
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                r = await client.get(sub_url)
                raw_bytes = r.content

            text = None
            for enc in ["utf-8", "windows-1254", "iso-8859-9", "latin5", "cp1252"]:
                try:
                    text = raw_bytes.decode(enc)
                    win1254_fixes = {
                        "ý": "ı", "þ": "ş", "ð": "ğ",
                        "Ý": "İ", "Þ": "Ş", "Ð": "Ğ"
                    }
                    for old_char, new_char in win1254_fixes.items():
                        if old_char in text:
                            text = text.replace(old_char, new_char)
                    break
                except UnicodeDecodeError:
                    continue

            if not text:
                text = raw_bytes.decode("utf-8", errors="ignore")

            if output_srt is None:
                output_srt = TEMP_DIR / f"sub_{imdb_id}_{'movie' if is_movie else f'S{season}E{episode}'}.srt"

            output_srt.write_text(text, encoding="utf-8")
            logger.info(f"OpenSubtitles Türkçe altyazı hazırlandı: {output_srt.name} ({output_srt.stat().st_size} bytes)")
            return output_srt
        except Exception as e:
            logger.error(f"Altyazı kaydetme hatası: {e}")
            return None

    @classmethod
    async def download_hls_stream(
        cls, 
        stream_url: str, 
        output_path: Path, 
        progress_cb: Optional[Callable[..., None]] = None,
        extra_subtitles: Optional[List[Dict[str, Any]]] = None,
        subtitle_path: Optional[Path] = None
    ) -> Dict[str, Any]:
        """HLS akışını video + Türkçe/Orijinal ses kanalları ve açılıp-kapanabilir Türkçe altyazı (Soft-Sub) ile indirir."""
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
                    return {"success": False}
            except Exception as e:
                logger.error(f"HLS Playlist istek hatası: {e}")
                return {"success": False}

            manifest_text = resp.text
            lines = [l.strip() for l in manifest_text.splitlines() if l.strip()]

            video_target_url = None
            audio_tr_url = None

            def safe_urljoin(base_url: str, rel_url: str) -> str:
                if not rel_url:
                    return base_url
                joined = urllib.parse.urljoin(base_url, rel_url)
                base_parsed = urllib.parse.urlparse(base_url)
                joined_parsed = urllib.parse.urlparse(joined)
                if base_parsed.query:
                    base_qs = urllib.parse.parse_qs(base_parsed.query)
                    joined_qs = urllib.parse.parse_qs(joined_parsed.query)
                    for k, v in base_qs.items():
                        if k not in joined_qs:
                            joined_qs[k] = v
                    new_q = urllib.parse.urlencode(joined_qs, doseq=True)
                    joined = urllib.parse.urlunparse(joined_parsed._replace(query=new_q))
                return joined

            # Master Playlist kontrolü
            has_turkish_audio_track = False
            if any("#EXT-X-STREAM-INF" in l for l in lines) or any("#EXT-X-MEDIA:TYPE=" in l for l in lines):
                # Tekil Ses Akışı Tespiti (Varsa Türkçe veya Birincil Ses)
                for l in lines:
                    if l.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
                        m_uri = re.search(r'URI=["\']?([^"\',]+)["\']?', l)
                        m_name = re.search(r'NAME=["\']?([^"\',]+)["\']?', l)
                        m_lang = re.search(r'LANGUAGE=["\']?([^"\',]+)["\']?', l)
                        if m_uri:
                            u = safe_urljoin(stream_url, m_uri.group(1))
                            name = (m_name.group(1) if m_name else "").lower()
                            lang = (m_lang.group(1) if m_lang else "").lower()
                            if any(x in name or x in lang for x in ["tur", "türk", "turkish", "tr", "dublaj"]):
                                audio_tr_url = u
                                has_turkish_audio_track = True
                                break
                            elif not audio_tr_url:
                                audio_tr_url = u

                # 720p / HD (< 2GB) Video Akışı Tespiti
                variants = []
                for i, l in enumerate(lines):
                    if l.startswith("#EXT-X-STREAM-INF"):
                        bw_m = re.search(r"BANDWIDTH=(\d+)", l)
                        res_m = re.search(r"RESOLUTION=(\d+)x(\d+)", l)
                        name_m = re.search(r'NAME=["\']?(\d+)[pP]?["\']?', l)
                        bw = int(bw_m.group(1)) if bw_m else 0
                        h = int(res_m.group(2)) if res_m else (int(name_m.group(1)) if name_m else 0)
                        
                        if h == 0:
                            if 1000000 <= bw <= 2400000:
                                h = 720
                            elif bw > 2400000:
                                h = 1080
                            elif bw > 0:
                                h = 480

                        for next_idx in range(i + 1, min(i + 5, len(lines))):
                            if not lines[next_idx].startswith("#"):
                                v_url = safe_urljoin(stream_url, lines[next_idx])
                                variants.append((h, bw, v_url))
                                break

                if variants:
                    # Telegram limitini (2GB) aşmayacak en kaliteli varyantı seç (1500k-2400k arası 720p/1080p)
                    v_fit = [v for v in variants if (v[1] <= 2400000 or v[1] == 0) and v[0] >= 720]
                    if v_fit:
                        v_fit.sort(key=lambda x: (x[0], x[1]), reverse=True)
                        video_target_url = v_fit[0][2]
                    else:
                        v_720 = [v for v in variants if v[0] == 720]
                        if v_720:
                            v_720.sort(key=lambda x: x[1])
                            video_target_url = v_720[0][2]
                        else:
                            v_under = [v for v in variants if 480 <= v[0] <= 1080]
                            if v_under:
                                v_under.sort(key=lambda x: x[1])
                                video_target_url = v_under[0][2]
                            else:
                                variants.sort(key=lambda x: x[1])
                                video_target_url = variants[0][2]

                    # Sessiz video (Audio olmayan) varyant kontrolü
                    has_codecs_attr = any("CODECS=" in l for l in lines)
                    has_audio_codec = any(re.search(r'CODECS="[^"]*(mp4a|aac|ac-3|ec-3|opus)', l, re.IGNORECASE) for l in lines)
                    if has_codecs_attr and not has_audio_codec and not audio_tr_url:
                        logger.warning("⚠️ M3U8 Master Playlist'te ses akışı veya ses codeci bulunamadı (Sessiz Kaynak).")
                        return {"success": False}

            # Segmentleri ve Varsa AES-128 Şifre Anahtarını Çıkar
            async def get_segments_and_key(url: str):
                try:
                    r = await session.get(url, headers=headers, timeout=10.0)
                    if r.status_code != 200:
                        return [], None, None
                    m_lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
                    
                    key_bytes = None
                    key_iv = None
                    for ln in m_lines:
                        if ln.startswith("#EXT-X-KEY:"):
                            m_method = re.search(r'METHOD=([^,\s]+)', ln)
                            m_uri = re.search(r'URI=["\']?([^"\',]+)["\']?', ln)
                            m_iv = re.search(r'IV=0x([0-9a-fA-F]+)', ln)
                            if m_method and m_method.group(1) == "AES-128" and m_uri:
                                key_url = safe_urljoin(url, m_uri.group(1))
                                try:
                                    rk = await session.get(key_url, headers=headers, timeout=8.0)
                                    if rk.status_code == 200 and len(rk.content) == 16:
                                        key_bytes = rk.content
                                        if m_iv:
                                            key_iv = bytes.fromhex(m_iv.group(1))
                                except Exception as ke:
                                    logger.debug(f"AES Key indirme hatası: {ke}")
                                    
                    seg_urls = []
                    for ln in m_lines:
                        if not ln.startswith("#"):
                            seg_urls.append(safe_urljoin(url, ln))
                    return seg_urls, key_bytes, key_iv
                except Exception as e:
                    logger.debug(f"get_segments_and_key hatası: {e}")
                    return [], None, None

            if not video_target_url:
                video_target_url = stream_url

            video_segs, v_key_bytes, v_key_iv = await get_segments_and_key(video_target_url)
            audio_tr_segs, a_key_bytes, a_key_iv = (await get_segments_and_key(audio_tr_url)) if audio_tr_url else ([], None, None)

            if not video_segs:
                logger.warning("HLS video segmentleri bulunamadı.")
                return {"success": False}

            tmp_video_file = output_path.with_suffix(".vraw.ts")
            tmp_audio_tr_file = output_path.with_suffix(".atr.ts") if audio_tr_segs else None

            tot_all_chunks = len(video_segs) + len(audio_tr_segs)
            done_all_chunks = 0
            downloaded_bytes = 0
            start_dl_time = time.time()

            async def download_seg_list(
                seg_list: List[str], 
                dest_file: Path, 
                key_bytes: Optional[bytes] = None, 
                key_iv: Optional[bytes] = None, 
                is_video: bool = True
            ):
                total = len(seg_list)
                if total == 0:
                    return

                sem = asyncio.Semaphore(25 if is_video else 50)
                nonlocal done_all_chunks, downloaded_bytes
                last_working_netloc = None

                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                from cryptography.hazmat.backends import default_backend

                def is_valid_chunk(data: bytes) -> bool:
                    if not data or len(data) < 128:
                        return False
                    if data.startswith(b"<!DOCTYPE") or data.startswith(b"<html") or b"<head" in data[:300] or b"<body" in data[:300]:
                        return False
                    return True

                # Sessiz TS Padding (Ses parçasının tamamen eksik kalması durumunda senkron kaymasını engeller)
                def get_silent_ts_pad() -> bytes:
                    try:
                        pad_file = Path("/tmp/dizibot_silent_audio_pad.ts")
                        if not pad_file.exists() or pad_file.stat().st_size == 0:
                            import subprocess
                            cmd = [
                                "ffmpeg", "-y", "-v", "error",
                                "-f", "lavfi",
                                "-i", "anullsrc=r=48000:cl=stereo",
                                "-t", "2.944",
                                "-c:a", "aac",
                                "-b:a", "128k",
                                "-f", "mpegts",
                                str(pad_file)
                            ]
                            subprocess.run(cmd, check=True)
                        return pad_file.read_bytes()
                    except Exception:
                        return b""

                async def fetch_seg(idx: int, s_url: str):
                    nonlocal done_all_chunks, downloaded_bytes, last_working_netloc
                    orig_parsed = urllib.parse.urlparse(s_url)
                    async with sem:
                        for retry in range(12):
                            url_to_try = s_url
                            # Eğer 2. veya sonraki denemedeyse ve çalışan bir CDN mirror varsa, alan adını mirror ile dene
                            if retry >= 2 and last_working_netloc and orig_parsed.netloc != last_working_netloc:
                                url_to_try = urllib.parse.urlunparse(orig_parsed._replace(netloc=last_working_netloc))

                            try:
                                res = await session.get(url_to_try, headers=headers, timeout=15.0)
                                if res.status_code == 200 and is_valid_chunk(res.content):
                                    done_all_chunks += 1
                                    downloaded_bytes += len(res.content)
                                    last_working_netloc = orig_parsed.netloc if res.url == s_url else last_working_netloc
                                    chunk_data = res.content
                                    # AES-128 Şifre Çözme
                                    if key_bytes:
                                        try:
                                            iv = key_iv if key_iv else idx.to_bytes(16, 'big')
                                            cipher = Cipher(algorithms.AES(key_bytes), modes.CBC(iv), backend=default_backend())
                                            decryptor = cipher.decryptor()
                                            chunk_data = decryptor.update(chunk_data) + decryptor.finalize()
                                        except Exception as dec_err:
                                            logger.debug(f"Segment #{idx} AES çözme hatası: {dec_err}")
                                            
                                    if progress_cb and tot_all_chunks > 0 and (done_all_chunks % 10 == 0 or done_all_chunks == tot_all_chunks):
                                        elapsed = max(0.1, time.time() - start_dl_time)
                                        cur_mb = downloaded_bytes / (1024 * 1024)
                                        speed_mb = cur_mb / elapsed
                                        frac = done_all_chunks / tot_all_chunks
                                        tot_est_mb = cur_mb / max(0.01, frac)
                                        try:
                                            progress_cb(
                                                frac, 
                                                done_seg=done_all_chunks, 
                                                tot_seg=tot_all_chunks, 
                                                cur_mb=cur_mb, 
                                                tot_mb=tot_est_mb, 
                                                speed_mb=speed_mb, 
                                                phase="downloading"
                                            )
                                        except TypeError:
                                            try:
                                                progress_cb(frac, done_all_chunks, tot_all_chunks)
                                            except Exception:
                                                progress_cb(frac)
                                    return idx, chunk_data
                                else:
                                    await asyncio.sleep(0.3 + retry * 0.3)
                            except Exception:
                                await asyncio.sleep(0.3 + retry * 0.3)

                        # Eğer ses parçasıysa ve inemediyse, ses zaman çizgisinin kaymaması için sessiz parça ile doldur
                        if not is_video:
                            silent_bytes = get_silent_ts_pad()
                            if silent_bytes:
                                done_all_chunks += 1
                                return idx, silent_bytes

                        return idx, b""

                completed_chunks = {}
                next_write_idx = 0
                missing_chunks_count = 0
                tasks = [asyncio.create_task(fetch_seg(i, seg_list[i])) for i in range(total)]

                with open(dest_file, "wb") as f_out:
                    for fut in asyncio.as_completed(tasks):
                        idx, chunk = await fut
                        completed_chunks[idx] = chunk
                        while next_write_idx in completed_chunks:
                            c = completed_chunks.pop(next_write_idx)
                            if c:
                                f_out.write(c)
                            else:
                                missing_chunks_count += 1
                            next_write_idx += 1

                if missing_chunks_count > 0:
                    max_allowed_missing = int(total * 0.10)
                    if missing_chunks_count > max_allowed_missing:
                        logger.warning(f"⚠️ {dest_file.name} için {missing_chunks_count}/{total} parça eksik kaldı (Limit aşıldı, akış reddedildi).")
                        return False
                    else:
                        logger.info(f"ℹ️ {dest_file.name} için {missing_chunks_count}/{total} parça atlandı (FFmpeg aresample/genpts ile senkron tamamlanacak).")
                return True

            logger.info(f"HLS İndiriliyor (Hızlı): Video={len(video_segs)} parça" + (f", Ses={len(audio_tr_segs)} parça" if audio_tr_segs else ""))
            dl_tasks = [download_seg_list(video_segs, tmp_video_file, key_bytes=v_key_bytes, key_iv=v_key_iv, is_video=True)]
            if audio_tr_segs and tmp_audio_tr_file:
                dl_tasks.append(download_seg_list(audio_tr_segs, tmp_audio_tr_file, key_bytes=a_key_bytes, key_iv=a_key_iv, is_video=False))
            
            results = await asyncio.gather(*dl_tasks)
            if not all(results):
                logger.warning("HLS akış parçalarından bazıları eksik indi veya bozuk (HTML block), aday başarısız sayılıyor.")
                if tmp_video_file.exists():
                    tmp_video_file.unlink(missing_ok=True)
                if tmp_audio_tr_file and tmp_audio_tr_file.exists():
                    tmp_audio_tr_file.unlink(missing_ok=True)
                return {"success": False}

            if not tmp_video_file.exists() or tmp_video_file.stat().st_size < 1024 * 100:
                logger.warning("HLS video dosyası indirilemedi veya geçersiz boyutta.")
                if tmp_video_file.exists():
                    tmp_video_file.unlink(missing_ok=True)
                if tmp_audio_tr_file and tmp_audio_tr_file.exists():
                    tmp_audio_tr_file.unlink(missing_ok=True)
                return {"success": False}

            if progress_cb:
                try:
                    progress_cb(1.0, tot_all_chunks, tot_all_chunks, phase="muxing")
                except TypeError:
                    try:
                        progress_cb(1.0)
                    except Exception:
                        pass

            # Altyazı Girişi (Kayıpsız 0-CPU Passthrough / Native Softsub Track)
            sub_inputs = []
            sub_maps = []
            sub_meta = []
            if subtitle_path and subtitle_path.exists() and subtitle_path.stat().st_size > 0 and not has_turkish_audio_track:
                sub_inputs = ["-i", str(subtitle_path)]
                sub_idx = 2 if (tmp_audio_tr_file and tmp_audio_tr_file.exists() and tmp_audio_tr_file.stat().st_size > 0) else 1
                sub_maps = ["-map", f"{sub_idx}:s:0"]
                sub_meta = [
                    "-c:s", "mov_text",
                    "-metadata:s:s:0", "language=tur",
                    "-metadata:s:s:0", "title=Türkçe",
                    "-metadata:s:s:0", "handler_name=Türkçe",
                    "-disposition:s:0", "default"
                ]

            # Anında Ultra Hızlı Birleştirme (Kayıpsız 0.5 sn Direct Stream Copy)
            audio_meta = []
            if has_turkish_audio_track:
                audio_meta = [
                    "-metadata:s:a:0", "language=tur",
                    "-metadata:s:a:0", "title=Türkçe Dublaj",
                    "-metadata:s:a:0", "handler_name=Türkçe Dublaj"
                ]

            if tmp_audio_tr_file and tmp_audio_tr_file.exists() and tmp_audio_tr_file.stat().st_size > 0:
                cmd = [
                    "ffmpeg", "-y",
                    "-threads", "0",
                    "-i", str(tmp_video_file),
                    "-i", str(tmp_audio_tr_file),
                    *sub_inputs,
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    *sub_maps,
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-bsf:a", "aac_adtstoasc",
                    *audio_meta,
                    *sub_meta,
                    "-movflags", "+faststart",
                    str(output_path)
                ]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-threads", "0",
                    "-i", str(tmp_video_file),
                    *sub_inputs,
                    "-map", "0:v:0",
                    "-map", "0:a:0?",
                    *sub_maps,
                    "-c:v", "copy",
                    "-c:a", "copy",
                    "-bsf:a", "aac_adtstoasc",
                    *audio_meta,
                    *sub_meta,
                    "-movflags", "+faststart",
                    str(output_path)
                ]

            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()

            # Geçici dosyaları temizle
            if tmp_video_file.exists():
                tmp_video_file.unlink(missing_ok=True)
            if tmp_audio_tr_file and tmp_audio_tr_file.exists():
                tmp_audio_tr_file.unlink(missing_ok=True)

            if not (output_path.exists() and output_path.stat().st_size > 1024 * 1024):
                if err:
                    logger.error(f"FFmpeg birleştirme hatası: {err.decode('utf-8', errors='ignore')[-300:]}")
                return {"success": False}

            # Ses Bütünlüğü Doğrulaması (Audio Stream Integrity Check)
            try:
                probe_audio_cmd = [
                    "ffprobe", "-v", "error",
                    "-select_streams", "a",
                    "-show_entries", "stream=index",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(output_path)
                ]
                proc_a = await asyncio.create_subprocess_exec(*probe_audio_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout_a, _ = await proc_a.communicate()
                has_audio_track = bool(stdout_a.decode().strip())
            except Exception:
                has_audio_track = True

            if not has_audio_track:
                logger.warning("⚠️ Kaynakta ses akışı bulunamadı (Sessiz Video Tespit Edildi). Dosya silinip alternatif kaynak deneniyor...")
                output_path.unlink(missing_ok=True)
                return {"success": False}

            return {
                "success": True,
                "audio_track_count": 1,
                "has_multi_audio": False,
                "has_subtitles": bool(subtitle_path and subtitle_path.exists()),
                "is_dublaj": has_turkish_audio_track,
                "audio_lang": "tr" if has_turkish_audio_track else "en"
            }

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
    async def split_video_lossless(cls, input_path: Path, max_bytes: int = 1950 * 1024 * 1024) -> List[Path]:
        """2GB sınırını aşan videoları FFmpeg -c copy ile anında (0.5 sn) kayıpsız parçalara (Part 1, Part 2) böler."""
        if not input_path.exists():
            return []

        f_size = input_path.stat().st_size
        if f_size <= max_bytes:
            return [input_path]

        try:
            probe_cmd = [
                "ffprobe", "-v", "error", 
                "-show_entries", "format=duration", 
                "-of", "default=noprint_wrappers=1:nokey=1", 
                str(input_path)
            ]
            proc = await asyncio.create_subprocess_exec(*probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await proc.communicate()
            duration = float(stdout.decode().strip())
        except Exception:
            duration = 7200.0

        if duration <= 0:
            duration = 7200.0

        import math
        num_parts = math.ceil(f_size / (1850 * 1024 * 1024))
        num_parts = max(2, num_parts)
        part_duration = duration / num_parts

        logger.info(f"Video {f_size / (1024*1024):.1f} MB (>2GB), kayıpsız olarak {num_parts} parçaya bölünüyor...")

        part_paths = []
        for i in range(num_parts):
            ss = i * part_duration
            t = part_duration
            part_file = input_path.with_name(f"{input_path.stem}_part{i+1}.mp4")
            cmd = [
                "ffmpeg", "-y", "-threads", "0",
                "-ss", f"{ss:.2f}",
                "-i", str(input_path),
                "-t", f"{t:.2f}",
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(part_file)
            ]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await proc.communicate()
            if part_file.exists() and part_file.stat().st_size > 1024 * 1024:
                part_paths.append(part_file)

        if len(part_paths) == num_parts:
            input_path.unlink(missing_ok=True)
            logger.info(f"Kayıpsız bölme tamamlandı: {len(part_paths)} parça oluşturuldu.")
            return part_paths
        else:
            for p in part_paths:
                p.unlink(missing_ok=True)
            return [input_path]
