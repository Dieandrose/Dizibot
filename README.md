# 🎬 DiziBot - Otonom Telegram Medya Dağıtım ve Takip Botu

DarkBox eklenti ekosistemindeki 50+ kaynağı (Dizi65, DizipalX, RecTV, Vizyona, SineWix, FilmMakinesi, FullHDFilmizlesene, HDFilmCehennemi, Selcukflix, RoketDizi vb.) paralel tarayan, en kaliteli akışları seçip indiren, Telegram forum konularına (`Forum Topics`) doğrudan video olarak aktaran ve otomatik yeni bölüm takibi yapan yapay zeka destekli medya otomasyon botudur.

---

## 🚀 Öne Çıkan Özellikler

### 1. 💬 İnteraktif Chat-Ops & Gelişmiş Navigasyon
- **Genişletilmiş Paralel Arama (`/ara <içerik>`):** DarkBox ekosistemindeki 50+ eklentide eşzamanlı (25 paralel işçi) arama yapar ve en yüksek alaka puanına sahip **25 farklı sonucu** butonlarla listeler.
- **Kesintisiz Menü Navigasyonu:** Bölüm listesindeyken **`🔙 Sezon Seçimine Dön`**, sezon/film listesindeyken **`🔙 Arama Sonuçlarına Dön`** butonları ile kolay gezinme.
- **Tüm Sezonları Tek Tıkla İndir (`🔥 TÜM SEZONLARI İNDİR`):** Dizinin tüm sezon ve bölümlerini (`S01`, `S02`, `S03`...) sırayla kuyruğa ekler.
- **Seçili Sezonu İndir (`📥 X. SEZONU İNDİR`):** İlgili sezonun tüm bölümlerini tek tıkla indirir veya altındaki butonlardan tekil bölüm seçimi sunar.
- **Film / Dizi Ayrımı:** Filmleri otomatik algılayarak tekil **`🎬 Filmler`** konusuna yönlendirir; başlıklardan gereksiz etiketleri temizler.

### 2. 🎛️ Çoklu Ses (Multi-Audio) & Açılıp-Kapanabilir Altyazı (Soft-Sub / CC)
- **Çoklu Ses Desteği:** Kaynakta mevcutsa **Türkçe Dublaj** ve **İngilizce (Orijinal)** ses kanalları ayrı ayrı MP4 konteynerine işlenir (`-metadata:s:a:0 title="Türkçe Dublaj" -metadata:s:a:1 title="İngilizce (Orijinal)"`).
- **Telegram Oynatıcı Entegre Altyazı (Soft-Sub):** Türkçe `.vtt` / `.srt` altyazıları video görüntüsüne gömülmeden, yerel altyazı izi (`mov_text`) olarak eklenir. İzleyiciler Telegram oynatıcısındaki **CC** butonundan altyazıyı dilediği gibi açıp kapatabilir.
- **1-2 Saniyede Kayıpsız Birleştirme (Instant Stream Copy):** Tüm ses ve altyazı kanalları re-encode olmadan doğrudan kopyalanır (`-c copy -c:s mov_text -bsf:a aac_adtstoasc`).
- **Otomatik Telegram Bilgi Rozetleri:** Yüklenen videoların açıklama kartına `🎙️ Ses: 🇹🇷 Türkçe Dublaj | 🇬🇧 Orijinal` ve `💬 Altyazı: 🇹🇷 Türkçe (Açılıp Kapanabilir / CC)` rozetleri otomatik eklenir.

### 3. ⚡ Kusursuz Ses & Video Senkronizasyonu (Zero-Desync)
- **Gerçek Zaman Damgası Kilitleme:** Kaynak sunucudaki PTS/DTS zaman damgaları korunarak milisaniyelik ses kayması ve dudak senkron (drift) sorunları engellenir.
- **Stüdyo Master Çok Dilli Akış Önceliği:** Ayrı video ve Türkçe dublaj kanalı barındıran HLS kaynakları önceliklendirilir.

### 4. 🛑 Tam Entegre İptal ve Süreç Yönetimi (Instant Cancellation)
- **Anında Görev Sonlandırma:** `/iptal <id>`, `/iptal hepsi` veya butonla iptal yapıldığında çalışan HTTP indirme soketi, FFmpeg süreci ve Telegram upload görevi **o milisaniyede derhal öldürülür**.
- **Canlı Butonlu İptal (`/durum`):** Durum ekranında her aktif indirme için **`[❌ #ID İptal Et]`** ve tek tıkla her şeyi durduran **`[🛑 Tüm Kuyruğu Temizle]`** butonları bulunur.
- **Otomatik Geçici Dosya Temizliği:** İptal edilen veya tamamlanan işlerin tüm geçici dosyaları (`.ts`, `.mp4`, `.jpg`) anında diskten silinir.

### 5. 📊 Canlı İlerleme ve Yükleme Takibi (`/durum`)
- **Canlı MTProto Yükleme Hızı:** Video kaynak siteden indirilirken ve ardından **Telegram sunucularına yüklenirken** anlık hız (MB/s), yüklenen MB miktarı ve tahmini kalan süre (ETA) gösterilir.
- **`[🔄 Canlı Yenile]` Butonu:** Mesaj kirliliği yaratmadan tek tıkla mevcut durum mesajını anında günceller.
- **Kuyruk Sıralaması:** Sezon ve bölüm sırasına (`season ASC, episode ASC`) göre mutlak sıralı işleme pipeline'ı.

### 6. 🎯 Kaynak Seviyesinde 720p/HD Akıllı Varyant Seçimi (Re-encode Yok)
- **Kayıpsız & Hızlı:** Telegram'ın 2.0 GB sınırına takılmamak için HLS Master Playlist içinden en uygun 720p / optimum bitrate akışını otomatik seçer.
- **Auto-Compress Motoru (Fallback):** 1.95 GB üzerindeki istisnai uzun yayınlarda CPU dostu dinamik bitrate optimizasyonu uygulayarak yüklemenin durmasını engeller.

### 7. 📌 Mükerrer Konu Önleme & Konu Yönetimi
- **Unicode & Emoji Normalizasyonu:** Başlıklardaki emojileri (`🎬`, `🍿`, `📺`), özel karakterleri ve boşlukları temizleyerek arar; asla çift/mükerrer forum konusu açmaz.
- **Otomatik Konu Yakalama (Auto-Learn Listener):** Grupta yeni bir forum konusu açıldığında veya düzenlendiğinde bot bunu anında algılar ve veritabanına kaydeder.
- **Manuel Konu Bağlama:**
  - `/konular` ➔ Kayıtlı tüm forum konularını ve ID'lerini listeler.
  - `/konubagla <Dizi Adı> [Konu_ID]` ➔ Konu içindeyken tek komutla diziyi konuya eşler.

### 8. ⚡ Otomatik Yeni Bölüm Takipçisi (Auto-Tracker Daemon)
- **Watchlist (`/takip <dizi>`):** Takip listesindeki dizileri arka planda periyodik olarak tarar.
- **Otonom Yükleme:** Yeni bölüm yayınlandığında otomatik tespit eder, indirir ve ilgili forum konusuna aktarır.

### 9. 🛡️ Bütünlük Denetimi & Eksik Bölüm Tamamlama (Auto-Healer)
- **Yükleme Sonrası Otomatik Denetim:** Bir bölüm yüklendiğinde sezonun önceki bölümlerinde atlanmış/eksik varsa otomatik sıraya alır.
- **Manuel Denetim (`/kontrol <dizi>`):** Dizinin eklentideki tüm bölümleriyle gruptaki yüklemeleri karşılaştırır ve eksik kalanları tamamlar.

### 10. 👥 Grup İstek & Yönetici Onay Sistemi
- **Üye İstekleri (`/istek <içerik>`):** Grup üyeleri talep oluşturur; yöneticilere `[✅ Onayla]` / `[❌ Reddet]` butonları gider.
- **Yönetici/Dark İsteği:** Yöneticiler `/istek <dizi>` yazdığında onaysız doğrudan tüm sezonlar sıraya alınır.

---

## 💻 Komut Referansı

| Komut | Açıklama |
| :--- | :--- |
| `/start`, `/yardim` | Bot yardım menüsü ve genel komut listesi |
| `/ara <isim>` | 50+ eklentide paralel arama (Tüm Sezon / Sezon / Bölüm seçimi) |
| `/durum` | Canlı indirme & Telegram yükleme hızı, ETA, iptal butonları ve `[🔄 Yenile]` |
| `/kuyruk` | Sırada bekleyen indirme işlemlerini listeler |
| `/iptal <id>` | Belirli bir işlem ID'sini anında durdurur ve temizler |
| `/iptal hepsi` | Tüm aktif ve bekleyen indirmeleri derhal sonlandırır, kuyruğu ve temp dosyaları temizler |
| `/istek <dizi/film>` | İstek talebi oluşturur (Yöneticiler için tüm sezonları doğrudan sıraya alır) |
| `/kontrol <dizi>` | Dizinin gruptaki eksik bölümlerini tarar ve otomatik tamamlar |
| `/takip <dizi>` | Diziyi otomatik yeni bölüm takibine alır |
| `/takiplistesi` | Takip edilen dizileri listeler |
| `/takipbirak <dizi>` | Diziyi takip listesinden çıkarır |
| `/konular` | Kayıtlı forum konularını listeler |
| `/konubagla <dizi>` | Bulunulan veya belirtilen forum konusunu diziye bağlar |

---

## 🛠️ Kurulum & Servis Yönetimi

```bash
# Depoyu klonlayın
git clone git@github.com:Dieandrose/Dizibot.git
cd Dizibot

# Sanal ortam ve paketler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Yapılandırma
cp .env.example .env
nano .env
```

### Systemd ile Sürekli Çalıştırma

```bash
systemctl enable --now dizibot.service

# Canlı log takibi
journalctl -u dizibot.service -f
```
