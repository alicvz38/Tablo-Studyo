# Tablo Stüdyo

Cam tablo satışı için **web mağazası** ve **yönetim paneli**. Ürünler, müşterinin görebileceği gerçekçi oda fotoğraflarına yerleştirilmiş olarak sunulur: tablo, odanın perspektifine ve yansıma ayarlarına göre sunucuda çizilir.

*Web store and admin panel for glass wall art: products are rendered into real room photos with perspective and reflections (OpenCV). Built with Python / FastAPI.*

## Özellikler

**Mağaza (herkese açık):** ürünler ve ürün sayfaları, ürünlerin kayıtlı odalarda gösterimi, sepet ve favoriler (cihazlar arası eşitlenir), üyelik, sipariş takibi, iptal ve iade talepleri, ürün değerlendirmeleri, canlı destek sohbeti.

**Panel (`/panel`, satıcılar için):**
- **Oda Sunumları:** oda fotoğrafı yükleme, köşeleri sürükleyerek duvar alanını belirleme
- **Tablo Ekle:** tablonun odaya yerleştirilmiş görselini oluşturma
- Ürün ekleme ve düzenleme, sipariş yönetimi (onay, kargo takip no, teslim, iptal / iade), satış ve ciro
- Canlı destek ve değerlendirme yönetimi
- Aynı hesapla açılan ekranlar **WebSocket** ile canlı eşitlenir

**Ödeme:** iyzico ile kartla ödeme (3D Secure), iptal ve iade. Kart bilgisi saklanmaz.

**Güvenlik:** şifreler tuzlanmış PBKDF2-SHA256, oturum anahtarları SHA-256 özeti olarak saklanır; sipariş fiyatları her zaman sunucudaki ürün kaydından alınır.

## Teknik

Python 3.12+ · FastAPI · Uvicorn · OpenCV · NumPy · SQLite · WebSocket

## Kurulum

```bash
git clone https://github.com/alicvz38/Tablo-Studyo.git
cd Tablo-Studyo
pip install -r requirements.txt
```

**1. Panel şablonu:** Panel, ücretli [Equation](https://themeforest.net/) Bootstrap 5 şablonunu kullanır; lisansı gereği depoda yoktur. Satın aldığınız kopyadan kurun:
```bash
python sablon_kur.py "C:/yol/equation-v2.2.0"
```
Betik yalnızca gereken dosyaları `static/panel/sablon/` altına kopyalar ve panel adını ve logoyu ayarlar.

**2. Çalıştırın:**
```bash
python sunucu.py
```
Windows'ta `baslat.bat` da kullanılabilir. Mağaza: http://localhost:8000 · Panel: http://localhost:8000/panel. Açılan ilk hesap satıcı (yönetici) olur.

**Docker ile:**
```bash
docker compose up -d
```
Veriler `./veri` klasöründe kalıcı tutulur.

### Ortam değişkenleri
| Değişken | Açıklama |
|---|---|
| `STUDYO_PORT` | Dinlenecek port (varsayılan 8000) |
| `STUDYO_VERI` | Veri klasörü (varsayılan: `veri/`) |
| `STUDYO_OTOMATIK` | `0` yapılırsa kod değişince sunucu kendini yeniden başlatmaz |

### Online ödeme (iyzico)
Anahtarlar koda yazılmaz; `veri/odeme.json` dosyasına girilir:
```json
{"etkin": true, "api_key": "...", "secret_key": "...", "base_url": "sandbox-api.iyzipay.com", "uc_boyutlu": true}
```
`sandbox-api.iyzipay.com` test ortamıdır. Canlıya geçerken canlı anahtarları ve `api.iyzipay.com` adresini yazın.

## Veriler

Hesaplar, odalar, ürünler, siparişler, ödeme ayarları ve yüklenen resimler `veri/` klasöründe oluşur ve **depoya eklenmez**.
