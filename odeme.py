"""Online kart ödemesi (iyzico): 3D Secure ya da doğrudan ödeme, iptal / iade.

Ayarlar veri/odeme.json'dadır (anahtarlar koda ve kod klasörüne yazılmaz):
  {"etkin": true, "api_key": "...", "secret_key": "...", "base_url": "sandbox-api.iyzipay.com", "uc_boyutlu": true}
base_url "sandbox-..." iken test ortamıdır (gerçek para çekilmez, iyzico'nun test kartları kullanılır); canlıya
geçerken canlı anahtarlar ve "api.iyzipay.com" yazılır. Dosya değişince sunucuyu yeniden başlatmak gerekmez.

Kart bilgileri hiçbir yere kaydedilmez ve yazdırılmaz: sadece iyzico'ya iletilir; siparişte kartın markası ve son
dört hanesi kalır. İstekler iyzico'nun IYZWSv2 imzasıyla, süre sınırıyla doğrudan gönderilir (iyzipay paketi
başlıkları sınıf düzeyinde paylaştığı için aynı anda gelen ödemelerde karışabilir ve süre sınırı yoktur).
"""
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
from datetime import datetime

ZAMAN_ASIMI = 40          # saniye: iyzico yanıt vermezse ödeme isteği sonsuza kadar beklemesin
TC_KIMLIK_VARSAYILAN = "11111111111"   # iyzico alıcıdan TC kimlik no ister; toplanmıyorsa bu kullanılır


class OdemeHatasi(Exception):
    """Müşteriye gösterilecek ödeme hatası."""


def luhn_gecerli(numara):
    toplam = 0
    for i, ch in enumerate(reversed(numara)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        toplam += d
    return toplam % 10 == 0


def kart_dogrula(kart):
    """Formdan gelen kartı denetler ve iyzico'nun beklediği biçime çevirir. Hatalıysa OdemeHatasi."""
    kart = kart or {}
    ad = " ".join(str(kart.get("ad") or "").split())
    numara = re.sub(r"\D", "", str(kart.get("numara") or ""))
    ay = re.sub(r"\D", "", str(kart.get("ay") or ""))
    yil = re.sub(r"\D", "", str(kart.get("yil") or ""))
    cvc = re.sub(r"\D", "", str(kart.get("cvc") or ""))
    if len(ad) < 3 or len(ad) > 60:
        raise OdemeHatasi("Kart üzerindeki adı yazın.")
    if not 13 <= len(numara) <= 19 or not luhn_gecerli(numara):
        raise OdemeHatasi("Kart numarası geçersiz; lütfen kontrol edin.")
    if len(yil) == 2:
        yil = "20" + yil
    try:
        ay_i, yil_i = int(ay), int(yil)
    except ValueError:
        raise OdemeHatasi("Kartın son kullanma tarihini yazın (AA/YY).")
    simdi = datetime.now()
    if not 1 <= ay_i <= 12 or (yil_i, ay_i) < (simdi.year, simdi.month) or yil_i > simdi.year + 25:
        raise OdemeHatasi("Kartın son kullanma tarihi geçersiz.")
    if not 3 <= len(cvc) <= 4:
        raise OdemeHatasi("Kartın güvenlik kodunu (CVC) yazın.")
    return {"cardHolderName": ad, "cardNumber": numara, "expireMonth": f"{ay_i:02d}", "expireYear": str(yil_i),
            "cvc": cvc, "registerCard": "0"}


def tutar(n):
    """iyzico tutar biçimi: '1250.0', '999.9', '12.34'."""
    s = f"{float(n):.2f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


class KartOdeme:
    def __init__(self, ayar_yolu):
        self.ayar_yolu = ayar_yolu
        self._ayar = (None, {})

    def ayarlar(self):
        try:
            zaman = os.path.getmtime(self.ayar_yolu)
        except OSError:
            return {}
        if self._ayar[0] != zaman:
            try:
                with open(self.ayar_yolu, encoding="utf-8") as f:
                    self._ayar = (zaman, json.load(f))
            except (OSError, ValueError):
                self._ayar = (zaman, {})
        return self._ayar[1]

    def acik(self):
        a = self.ayarlar()
        return bool(a.get("etkin", True) and a.get("api_key") and a.get("secret_key") and a.get("base_url"))

    def test_modu(self):
        return "sandbox" in str(self.ayarlar().get("base_url", ""))

    def uc_boyutlu(self):
        return bool(self.ayarlar().get("uc_boyutlu", True))

    def durum(self):
        """Mağazanın ödeme sayfası için (anahtarlar gönderilmez)."""
        return {"kart": self.acik(), "test": self.test_modu(), "uc_boyutlu": self.uc_boyutlu()}

    # ------------------------------------------------------------------
    def _istek(self, yol, govde):
        """iyzico'ya imzalı istek (IYZWSv2). Ağ hatasında OdemeHatasi."""
        a = self.ayarlar()
        if not self.acik():
            raise OdemeHatasi("Kartla ödeme şu an kullanılamıyor.")
        metin = json.dumps(govde)
        rastgele = secrets.token_hex(8)
        imza = hmac.new(a["secret_key"].encode(), (rastgele + yol + metin).encode(), hashlib.sha256).hexdigest()
        yetki = base64.b64encode(f"apiKey:{a['api_key']}&randomKey:{rastgele}&signature:{imza}".encode()).decode()
        basliklar = {"Accept": "application/json", "Content-Type": "application/json", "x-iyzi-rnd": rastgele,
                     "Authorization": f"IYZWSv2 {yetki}", "x-iyzi-client-version": "tablo-studyo-1.0"}
        baglanti = http.client.HTTPSConnection(a["base_url"], timeout=ZAMAN_ASIMI)
        try:
            baglanti.request("POST", yol, metin, basliklar)
            yanit = baglanti.getresponse()
            veri = yanit.read().decode("utf-8")
        except (OSError, http.client.HTTPException):
            raise OdemeHatasi("Ödeme sistemine ulaşılamadı; lütfen biraz sonra tekrar deneyin.")
        finally:
            baglanti.close()
        try:
            return json.loads(veri)
        except ValueError:
            raise OdemeHatasi("Ödeme sisteminden anlaşılmayan bir yanıt geldi.")

    @staticmethod
    def _hata(yanit, varsayilan="Ödeme alınamadı."):
        mesaj = yanit.get("errorMessage") or varsayilan
        return OdemeHatasi(f"{mesaj}" if mesaj.endswith(".") else f"{mesaj}.")

    @staticmethod
    def _istek_govdesi(siparis, musteri, ip, anahtar):
        """Ödeme isteğinin kart dışındaki kısmı: tutarlar, sepet, alıcı ve adresler (siparişten)."""
        t = siparis["teslimat"]
        adlar = (t.get("ad") or musteri.get("ad") or "Müşteri").split()
        ad, soyad = (" ".join(adlar[:-1]), adlar[-1]) if len(adlar) > 1 else (adlar[0], adlar[0])
        rakam = re.sub(r"\D", "", t.get("telefon", ""))
        telefon = "+90" + rakam[-10:] if len(rakam) >= 10 else ""
        eposta = musteri.get("eposta") or f"{musteri.get('kullanici_adi', 'musteri')}@tablostudyo.com"
        acik_adres = f"{t.get('adres', '')}, {t.get('ilce', '')}"
        adres = {"contactName": t.get("ad") or musteri.get("ad"), "city": t.get("il") or "-", "country": "Turkey",
                 "address": acik_adres}
        sepet = [{"id": str(k["urun_id"]), "name": k["ad"][:100], "category1": "Cam Tablo",
                  "category2": k.get("tip") or "Tablo", "itemType": "PHYSICAL", "price": tutar(k["fiyat"] * k["adet"])}
                 for k in siparis["kalemler"]]
        toplam = tutar(sum(k["fiyat"] * k["adet"] for k in siparis["kalemler"]))
        govde = {
            "locale": "tr", "conversationId": anahtar, "price": toplam, "paidPrice": toplam, "currency": "TRY",
            "installment": 1, "basketId": siparis["no"], "paymentChannel": "WEB", "paymentGroup": "PRODUCT",
            "buyer": {"id": str(musteri["id"]), "name": ad, "surname": soyad, "email": eposta,
                      "identityNumber": TC_KIMLIK_VARSAYILAN, "registrationAddress": acik_adres, "ip": ip or "127.0.0.1",
                      "city": t.get("il") or "-", "country": "Turkey"},
            "shippingAddress": adres, "billingAddress": adres, "basketItems": sepet,
        }
        if telefon:
            govde["buyer"]["gsmNumber"] = telefon
        return govde

    @staticmethod
    def _kart_ozeti(yanit, kart):
        marka = {"MASTER_CARD": "Mastercard", "VISA": "Visa", "AMERICAN_EXPRESS": "Amex", "TROY": "Troy"}.get(
            yanit.get("cardAssociation") or "", yanit.get("cardAssociation") or "Kart")
        son = (yanit.get("lastFourDigits") or (kart or {}).get("cardNumber", "")[-4:])
        return f"{marka} •••• {son}" if son else marka

    # ------------------------------------------------------------------
    def odeme_al(self, siparis, musteri, kart, ip, anahtar):
        """Doğrudan (3D'siz) ödeme. Başarılıysa {"odeme_id", "kart"}; değilse OdemeHatasi."""
        govde = self._istek_govdesi(siparis, musteri, ip, anahtar)
        govde["paymentCard"] = kart
        yanit = self._istek("/payment/auth", govde)
        if yanit.get("status") != "success":
            raise self._hata(yanit)
        self._tutari_dogrula(yanit, siparis)
        return {"odeme_id": yanit.get("paymentId", ""), "kart": self._kart_ozeti(yanit, kart)}

    def uc_boyut_baslat(self, siparis, musteri, kart, ip, anahtar, donus_adresi):
        """3D Secure başlatır; bankanın doğrulama sayfasına giden HTML'i döndürür (tarayıcıda gösterilir)."""
        govde = self._istek_govdesi(siparis, musteri, ip, anahtar)
        govde["paymentCard"] = kart
        govde["callbackUrl"] = donus_adresi
        yanit = self._istek("/payment/3dsecure/initialize", govde)
        if yanit.get("status") != "success" or not yanit.get("threeDSHtmlContent"):
            raise self._hata(yanit, "3D Secure doğrulaması başlatılamadı.")
        return base64.b64decode(yanit["threeDSHtmlContent"]).decode("utf-8", "replace")

    def uc_boyut_tamamla(self, siparis, form):
        """Bankadan dönüşte (callbackUrl'e gelen form) ödemeyi tamamlar. Başarılıysa {"odeme_id", "kart"}."""
        if form.get("status") != "success" or str(form.get("mdStatus")) != "1":
            raise OdemeHatasi("Kart doğrulaması (3D Secure) tamamlanamadı; ödeme alınmadı.")
        govde = {"locale": "tr", "conversationId": form.get("conversationId", ""), "paymentId": form.get("paymentId", "")}
        if form.get("conversationData"):
            govde["conversationData"] = form["conversationData"]
        yanit = self._istek("/payment/3dsecure/auth", govde)
        if yanit.get("status") != "success":
            raise self._hata(yanit)
        self._tutari_dogrula(yanit, siparis)
        return {"odeme_id": yanit.get("paymentId", ""), "kart": self._kart_ozeti(yanit, None)}

    @staticmethod
    def _tutari_dogrula(yanit, siparis):
        """Çekilen tutar siparişin tutarıyla aynı mı (yanlış tutarla tamamlanan ödeme kabul edilmesin)."""
        try:
            odenen = float(yanit.get("paidPrice"))
        except (TypeError, ValueError):
            return
        if abs(odenen - float(siparis["toplam"])) > 0.01:
            raise OdemeHatasi("Ödenen tutar sipariş tutarıyla uyuşmuyor; lütfen mağazayla iletişime geçin.")

    def iptal_et(self, odeme_id, ip=None):
        """Ödemeyi iptal eder (aynı gün içinde tam iade). Başarısızsa OdemeHatasi (iade iyzico panelinden yapılır)."""
        yanit = self._istek("/payment/cancel", {"locale": "tr", "conversationId": secrets.token_hex(6),
                                                "paymentId": odeme_id, "ip": ip or "127.0.0.1"})
        if yanit.get("status") != "success":
            raise self._hata(yanit, "Ödeme iade edilemedi.")
        return True

    def tutar_iade_et(self, odeme_id, tutar_tl, ip=None):
        """Ödemenin bir kısmını ya da tamamını karta iade eder (gün sonu geçmiş ödemelerde de çalışır)."""
        yanit = self._istek("/v2/payment/refund", {"locale": "tr", "conversationId": secrets.token_hex(6),
                                                   "paymentId": odeme_id, "price": tutar(tutar_tl), "currency": "TRY",
                                                   "ip": ip or "127.0.0.1"})
        if yanit.get("status") != "success":
            raise self._hata(yanit, "Ödeme iade edilemedi.")
        return True

    def karta_iade(self, odeme_id, tutar_tl, ip=None):
        """Tam iade: önce iptal (aynı gün, işlem hiç görünmez), olmazsa tutar iadesi. Dönüş: yapılan işlemin adı."""
        try:
            self.iptal_et(odeme_id, ip)
            return "iptal"
        except OdemeHatasi:
            self.tutar_iade_et(odeme_id, tutar_tl, ip)
            return "iade"
