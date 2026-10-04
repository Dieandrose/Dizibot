# 🎬 DiziBot - Otonom Telegram Medya Dağıtım ve Takip Botu

DarkBox eklenti ekosistemindeki 50+ kaynağı (DizipalX, SineWix, RecTV, Vizyona, Dizibal, Dizibol, DiziMom, Dizi65, FilmModu, FullHDFilmizlesene, Selcukflix, RoketDizi vb.) paralel tarayan, en kaliteli akışları seçip indiren, Telegram forum konularına (`Forum Topics`) doğrudan video olarak aktaran ve otomatik yeni bölüm takibi yapan yapay zeka destekli medya otomasyon botudur.

---

## 🚀 Öne Çıkan Özellikler

### 1. 🌐 Akıllı Dil & Altyazı Pipeline'ı (1. ve 2. Plan Mimarisi)
- **1. Plan (Birincil Öncelik - Türkçe Dublaj):** DarkBox eklentilerinde Türkçe dublaj veya stüdyo dublaj akışı varsa öncelikli olarak Türkçe sesli indirilir.
- **2. Plan (İkincil Fallback - Orijinal Ses + OpenSubtitles TR Altyazı):**
  - Türkçe dublajı bulunmayan yabancı içerikler için TMDB & IMDB API üzerinden içerik kimliği otomatik çözülür.
  - **OpenSubtitles v3** üzerinden bölüme tam uyumlu Türkçe altyazı (`.srt`) otomatik indirilir.
  - **Kusursuz Türkçe Karakter Onarımı (UTF-8):** `windows-1254`, `iso-8859-9` ve CP1252 karakter bozuklukları (`ý, þ, ð, Ý, Þ, Ð` $\rightarrow$ `ı, ş, ğ, İ, Ş, Ğ`) tamamen onarılır.
  - **Kayıpsız Soft-Sub Gömme (`mov_text`):** Altyazı videonun içine 0.06 saniyede açılıp-kapanabilir yerel altyazı izi olarak gömülür. Telegram oynatıcısında doğrudan **[CC] Türkçe Altyazı** olarak oynatılır.

### 2. ⚡ 0.00ms Ses Senkronizasyonu & Dinamik CDN Ayna Kurtarma
- **Dinamik CDN Ayna Kurtarma:** Kaynak sunucuda Cloudflare 403 veya ölü domainlere yönlendirilen ses parçaları, aktif CDN ayna sunucusundan otomatik kurtarılır (%100 parça bütünlüğü).
- **Zaman Damgalı Sessiz TS Padding:** Eksik parçaların yerine aynı sürede (`#EXTINF`) sessiz ses bloğu eklenerek ses çizgisinin milisaniye dahi kayması önlenir (0.00ms lip-sync).
- **Kayıpsız Passthrough Muxing:** CPU'yu yoran yeniden kodlama adımları kaldırıldı; ses ve video doğrudan akış kopyalama (`-c copy -bsf:a aac_adtstoasc`) ile 0.3 saniyede MP4'e paketlenir.

### 3. ✂️ 2GB Üzeri Dosyalarda Kayıpsız Tek Geçişte Bölme (Lossless Splitting)
- Telegram'ın 2.0 GB sınırını aşan uzun filmler veya yüksek bitrate'li bölümler, görüntü kalitesi bozulmadan doğrudan tek geçişte `[Parça 1/2]`, `[Parça 2/2]` olarak bölünür ve yüklenir.

### 4. 📌 Kendi Kendini Onaran Forum Konuları (Self-Healing Topics)
- **Canlı Konu Doğrulama:** Yükleme öncesinde veritabanındaki konu ID'si Telegram API ile canlı test edilir.
- **Otomatik Yeniden Açma:** Telegram grubunda silinmiş veya geçersiz kalmış eski konu ID'leri anında tespit edilir, veritabanından temizlenir ve grupta **`🎬 <Dizi Adı>`** adıyla yeni forum konusu açılarak yükleme oraya yönlendirilir.
- **Filmler İçin Tekil Konu:** Filmler otomatik olarak tekil **`🎬 Filmler`** konusuna aktarılır.

### 5. 🔒 Güvenlik & Rol Tabanlı Yönetim (Admin-Only Controls)
- **Kullanıcı Yetkilendirmesi:** Durum (`/durum`, `/kuyruk`) ekranında `❌ #ID İptal Et`, `🛑 Tüm Kuyruğu Temizle` butonları ve sunucu disk kullanım bilgisi **sadece yöneticilere (`admin_ids`)** gösterilir; normal kullanıcılar sadece canlı ilerleme çubuğu ve `[🔄 Canlı Yenile]` butonunu görür.
- **Hızlı Görev Sonlandırma:** İptal komutu verildiğinde indirme soketi, FFmpeg süreci ve yükleme anında sonlandırılır, geçici dosyalar diskten temizlenir.

### 6. 💬 Chat-Ops & Menü Navigasyonu
- **Genişletilmiş Paralel Arama (`/ara <içerik>`):** 50+ eklentide eşzamanlı arama yapar ve en yüksek alaka puanına sahip sonuçları butonlarla listeler.
- **Kesintisiz Menü Navigasyonu:** `🔙 Sezon Seçimine Dön` ve `🔙 Arama Sonuçlarına Dön` butonları ile akıcı gezinme.
- **Tüm Sezonları Tek Tıkla İndir (`🔥 TÜM SEZONLARI İNDİR`):** Dizinin tüm sezon ve bölümlerini sırayla kuyruğa ekler.
- **Seçili Sezonu İndir (`📥 X. SEZONU İNDİR`):** İlgili sezonun tüm bölümlerini tek tıkla indirir veya tekil bölüm seçimi sunar.

### 7. 📊 Canlı MTProto İlerleme ve Yükleme Takibi
- Video kaynak siteden indirilirken ve ardından Telegram sunucularına aktarılırken anlık hız (MB/s), yüklenen MB miktarı ve tahmini kalan süre (ETA) canlı gösterilir.
- `[🔄 Canlı Yenile]` butonu ile mesaj kirliliği olmadan durum yenilenir.

### 8. 🛡️ Takip & Eksik Bölüm Tamamlama (Auto-Tracker & Auto-Healer)
- **Watchlist (`/takip <dizi>`):** Takipteki dizileri periyodik tarar, yeni bölüm geldiğinde otomatik indirip yükler.
- **Eksik Bölüm Tamamlama (`/kontrol <dizi>`):** Dizinin eklentilerdeki bölümleri ile gruptaki yüklemeleri karşılaştırır ve eksik kalan bölümleri sıraya alır.
- **Grup İstek Sistemi (`/istek <içerik>`):** Üye istekleri yöneticilere `[✅ Onayla]` / `[❌ Reddet]` butonlarıyla iletilir; yöneticiler talep ettiğinde doğrudan kuyruğa eklenir.

---

## 💻 Komut Referansı

| Komut | Açıklama | Yetki |
| :--- | :--- | :--- |
| `/start`, `/yardim` | Bot yardım menüsü ve genel komut listesi | Herkes |
| `/ara <isim>` | 50+ eklentide paralel arama (Tüm Sezon / Sezon / Bölüm seçimi) | Herkes |
| `/durum` | Canlı indirme & Telegram yükleme hızı, ETA ve `[🔄 Yenile]` | Herkes (İptal butonları sadece Admin) |
| `/kuyruk` | Sırada bekleyen indirme işlemlerini listeler | Herkes |
| `/istek <içerik>` | İstek talebi oluşturur (Yöneticiler için doğrudan tüm sezonları kuyruğa alır) | Herkes |
| `/kontrol <dizi>` | Dizinin gruptaki eksik bölümlerini tarar ve otomatik tamamlar | Admin |
| `/takip <dizi>` | Diziyi otomatik yeni bölüm takibine alır | Admin |
| `/takiplistesi` | Takip edilen dizileri listeler | Admin |
| `/takipbirak <dizi>` | Diziyi takip listesinden çıkarır | Admin |
| `/iptal <id>` | Belirli bir işlem ID'sini anında durdurur ve temizler | Admin |
| `/iptal hepsi` | Tüm aktif ve bekleyen indirmeleri derhal sonlandırır | Admin |
| `/konular` | Kayıtlı forum konularını listeler | Admin |
| `/konubagla <dizi>` | Bulunulan veya belirtilen forum konusunu diziye bağlar | Admin |
| `/konusil <dizi>` | Kayıtlı forum konusu eşleşmesini siler | Admin |

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
