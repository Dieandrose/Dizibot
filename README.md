# DiziBot - Otonom Telegram Medya Dağıtım ve Takip Botu

DarkBox eklenti ekosistemindeki kaynakları (Dizi65, DizipalX, RecTV, Vizyona vb.) tarayan, en uygun akışları seçip indiren, Telegram konularına (Forum Topic) doğrudan video olarak aktaran ve otomatik yeni bölüm takibi yapan interaktif medya botudur.

---

## 🚀 Yeni Eklenen Özellikler

### 1. 💬 Telegram Üzerinden Canlı Kontrol (Chat-Ops & Butonlar)
- **İnteraktif Arama (`/ara <içerik>`):** DarkBox genelinde arama yapar, butonlu sonuç listesi sunar. Sezon ve bölümü butonlarla seçerek tek tıkla indirmeyi başlatır.
- **Canlı Durum (`/durum`):** Aktif indirmeyi, ilerleme çubuğunu (`progress bar`), işlem durumunu ve yüklenenleri gösterir.
- **Kuyruk Yönetimi (`/kuyruk` & `/iptal <id>`):** Kuyruktaki işleri listeleme ve yönetici iptal desteği.

### 2. ⚡ Otomatik Yeni Bölüm Takipçisi (Auto-Tracker Daemon)
- **Watchlist (`/takip <dizi>`):** Takip listesine eklenen dizileri arka planda periyodik olarak tarar.
- **Otomatik Yükleme:** Yeni bir bölüm yayınlandığında kimse komut vermeden otomatik olarak tespit eder, indirir ve ilgili forum konusuna yükler.
- **Liste Yönetimi:** `/takiplistesi` ve `/takipbirak <dizi>`.

### 3. 🛡️ Akıllı Hata Yönetimi & Çoklu Kaynak Zinciri (Fallback Chain)
- **Çoklu Kaynak:** Bir kaynakta 403, yavaş hız veya bozuk ses oluştuğunda durmadan sıradaki en uygun eklentiye (`Dizi65` ➔ `DizipalX` ➔ `RecTV` ➔ `Vizyona` ➔ `SineWix`...) otomatik geçer.
- **HLS Türkçe Ses Tespiti:** Master M3U8'deki `LANGUAGE="tr"` / `LANGUAGE="tur"` / `NAME="Türkçe"` etiketlerini otomatik yakalar ve video ile senkronize eder.
- **2 GB Limit Koruması:** 2 GB üzerindeki hantal dosyaları otomatik eleyerek optimize HD versiyonu seçer.
- **Kusursuz Senkron:** `aresample=async=1000:first_pts=0` filtresi ile kayıpsız ses/görüntü birleştirme.

### 4. 👥 Grup İstek & Yönetici Onay Sistemi
- **Üye İstekleri (`/istek <içerik>`):** Grup üyeleri içerik talebinde bulunur.
- **Admin Onay Butonları:** Yöneticilere `[✅ Onayla & Yükle]` ve `[❌ Reddet]` butonlu bildirim düşer.
- **Otomasyon:** Yönetici onayladığı an içerik kuyruğa alınır, indirilir ve yüklendiğinde istek sahibine bildirim gider.

### 5. 🛡️ Bütünlük Denetimi & Eksik Bölüm Tamamlama (Auto-Healer)
- **Otomatik Yükleme Kontrolü:** Her bölüm yüklendikten sonra dizinin ilgili sezonundaki tüm bölümler denetlenir; arada atlanmış, yüklenmemiş veya başarısız olmuş eksik bölüm tespit edilirse otomatik kuyruğa alınıp indirilir.
- **Manuel Denetim Komutu (`/kontrol <dizi>`):** Tek komutla dizinin eklentideki tüm bölümleriyle gruptaki bölümlerini karşılaştırır ve eksikleri anında tamamlar.

### 6. ⚡ Eşzamanlı Çoklu İndirme & Satır İçi Arama
- **Multi-Worker Pool:** Aynı anda birden fazla bölüm paralel indirilerek aktarım hızı artırılır.
- **Satır İçi Arama (Inline Query):** Herhangi bir sohbette `@diyzybot <dizi>` yazılarak anında kartlı arama yapılır.

---

## 🛠️ Kurulum & Yapılandırma

```bash
# Depoyu klonlayın
git clone git@github.com:Dieandrose/Dizibot.git
cd Dizibot

# Sanal ortam ve paketler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Yapılandırma dosyasını oluşturun
cp .env.example .env
nano .env
```

### Ortam Değişkenleri (`.env`)
```ini
TG_BOT_TOKEN="123456789:ABCDefGhIjKlMnOpQrStUvWxYz"
TG_API_ID="2040"
TG_API_HASH="b18441a1ff607e10a989891a5462e627"
TG_TARGET_CHAT_ID="-1001909587016"
ADMIN_IDS="1080169172"
TG_UPLOADER_PROXY="http://127.0.0.1:4000"
TRACKER_INTERVAL="900"
DARKBOX_ROOT="/root/Darkbox"
```

---

## 💻 Kullanılabilir Komutlar

| Komut | Açıklama |
| :--- | :--- |
| `/start`, `/yardim` | Bot yardım menüsü ve komut listesi |
| `/ara <isim>` | Butonlu interaktif arama ve bölüm seçimi |
| `/indir <dizi> <s_no> <b_no>` | Hızlı bölüm indirme ve yükleme |
| `/durum` | Anlık indirme ilerlemesi ve işlem durumu |
| `/kuyruk` | Kuyruktaki bekleyen görevler |
| `/iptal <id>` | Kuyruktaki görevi iptal etme |
| `/takip <dizi>` | Diziyi otomatik yeni bölüm takibine alma |
| `/takiplistesi` | Takip edilen dizileri listeleme |
| `/takipbirak <dizi>` | Diziyi takipten çıkarma |
| `/istek <içerik>` | Dizi / film istek talebi oluşturma |

---

## 🔄 Systemd Servis Yönetimi

```bash
cp dizibot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dizibot.service

# Canlı logları izleme
journalctl -u dizibot.service -f
```
