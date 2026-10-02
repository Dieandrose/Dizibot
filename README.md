# DiziBot - Otonom Telegram Video ve Dizi Yükleyici

DarkBox eklenti sağlayıcılarından dizi ve film içeriklerini otomatik arayan, < 2 GB boyut sınırına ve Türkçe ses kanalına göre filtreleyen, ses-görüntü senkronizasyonu yaparak Telegram forum konularına (Topic) aktaran otonom yükleyici bot.

---

## 🚀 Özellikler

- **Çoklu Eklenti Araması:** Tek bir kaynağa bağlı kalmadan DarkBox genelindeki tüm eklentilerde arama ve en kaliteli akışı seçme.
- **2 GB Limit Koruması:** 2 GB ve üzerindeki hantal kaynakları otomatik eleyip kompakt (< 1.9 GB) ve kayıpsız 720p/1080p alternatiflere yönelme.
- **HLS Türkçe Ses Ayıklama:** Ayrık ses akışına sahip master manifestlerde `tur` / `tr` / `Turkish` kanalını otomatik tespit edip indirme.
- **Kusursuz Senkron (Muxing):** `aresample=async=1000:first_pts=0` filtresi ile ses ve görüntüyü tam kare/zaman senkronuyla birleştirme.
- **Telegram Forum Topic Desteği:** Hedef gruptaki mevcut dizi konusunu (`topic_id`) otomatik bulma veya yeni konu oluşturup içine yükleme (`supports_streaming=True`).

---

## 🛠️ Kurulum

```bash
# Depoyu klonlayın
git clone git@github.com:Dieandrose/dizibot.git
cd dizibot

# Sanal ortam oluşturun ve gereksinimleri yükleyin
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Yapılandırmayı oluşturun
cp .env.example .env
nano .env
```

---

## 💻 Kullanım

### Belirli Bir Dizi / Sezon / Bölüm Arama ve Yükleme
```bash
python tg_uploader_bot.py --search-and-upload "Mezarlık" 2 1
```

### Genel Daemon Modu
```bash
python tg_uploader_bot.py
```

---

## ⚙️ Systemd Servisi

```bash
cp dizibot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now dizibot.service
```
