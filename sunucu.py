"""Tablo Stüdyosu Web: masaüstü tablo_studyo.py'nin tarayıcıdan, kullanıcı hesaplarıyla kullanılan sürümü ve mağaza.

  /          mağaza (herkese açık): ürünler, ürün sayfaları; ürünler kayıtlı odalarda cam tablo olarak gösterilir
  /panel     yönetim paneli (giriş gerekir): Oda Sunumları, Tablo Ekle, ürün ekle / düzenle (magaza.py)

Herkes kendi hesabıyla kayıt olup girer; odalar, tablo resimleri ve ayarlar hesaba özeldir, başkası göremez.
Aynı hesapla birden fazla bilgisayarda giriş yapılırsa ekranlar canlı eşitlenir: bir bilgisayarda oda yüklenir,
köşe sürüklenir ya da odaya resim eklenirse diğerinde anında görünür (WebSocket). Görüntüler sunucuda çizilir
(cekirdek.py), bu yüzden sonuçlar masaüstü programla birebir aynıdır.

Çalıştırma:  python sunucu.py          (ya da: uvicorn sunucu:app --host 0.0.0.0 --port 8000)
Ortam değişkenleri:
  STUDYO_PORT        dinlenecek port (varsayılan 8000)
  STUDYO_VERI        veri klasörü (varsayılan: bu dosyanın yanındaki veri/)
"""
import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import time
import traceback
import uuid
import zipfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime
from html import escape
from threading import Lock
from urllib.parse import quote, urlparse

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import cekirdek as ck
from hesaplar import Hesaplar, HesapHatasi
from magaza import GORSEL_BOYUTLARI, Bellek, Magaza, MagazaHatasi
from siparisler import (AKIS, DURUMLAR, ODEME_SEKILLERI, EN_FAZLA_ADET, IADE_SURESI_GUN, TALEP_DURUMLARI,
                        TALEP_NEDENLERI, TALEP_TURLERI, SiparisHatasi, Siparisler)
from destek import Destek, DestekHatasi
from odeme import KartOdeme, OdemeHatasi, kart_dogrula
from sepet import Sepetler
from degerlendirme import Degerlendirmeler, DegerlendirmeHatasi

KOK = os.path.dirname(os.path.abspath(__file__))
VERI = ck.VERI_KLASORU
KULLANICI_VERI = os.path.join(VERI, "kullanicilar")   # her kullanıcının klasörü: kullanicilar/<id>/
os.makedirs(KULLANICI_VERI, exist_ok=True)
hesaplar = Hesaplar(os.path.join(VERI, "hesaplar.db"))
dukkan = Magaza(os.path.join(VERI, "magaza"))          # mağazadaki ürünler ve odalardaki görselleri
siparisler = Siparisler(os.path.join(VERI, "magaza", "siparisler.db"))
destek = Destek(os.path.join(VERI, "magaza", "destek.db"))   # canlı destek sohbetleri
kart_odeme = KartOdeme(os.path.join(VERI, "odeme.json"))      # online kart ödemesi (iyzico); anahtarlar bu dosyada
sepetler = Sepetler(os.path.join(VERI, "magaza", "sepet.db"))  # üyelerin sepeti ve favorileri (hesaba bağlı)
degerlendirmeler = Degerlendirmeler(os.path.join(VERI, "magaza", "degerlendirme.db"))   # ürün yıldız / yorumları
_puan_bellek = [0.0, {}]   # (zaman, urun_id -> (ortalama, sayı)): vitrinde her kart için ayrı sorgu olmasın


def puan_ozetleri():
    if time.monotonic() - _puan_bellek[0] > 30:
        _puan_bellek[:] = [time.monotonic(), degerlendirmeler.ozetler()]
    return _puan_bellek[1]

OTURUM_CEREZI = "studyo_oturum"
DESTEK_CEREZI = "destek_anahtar"   # ziyaretçinin kendi sohbetine bağlandığı gizli anahtar
RESIM_UZANTILARI = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

ONIZLEME_KALITELI = 1600   # önizleme resminin uzun kenarı (piksel)
ONIZLEME_HIZLI = 900       # sürüklerken / kaydırıcı oynarken: önce bu boyutta hızlı çizilir
ESER_MAKS = 2600           # tablo resimleri en fazla bu boyutta tutulur (odaya yerleşirken zaten küçülür)


# Arayüz kontrolleri sunucudan gelir: ayar listesi tek yerde (cekirdek.py) kalsın
KONTROLLER = {
    "taraflar": ck.YANSIMA_TARAFLARI,
    "guc_araligi": ck.YANSIMA_GUC_ARALIGI,
    "tipler": ck.TABLO_TIPLERI,
    "simgeler": ck.TABLO_TIPI_SIMGE,
    "resim_tipleri": ck.RESIM_TIPLERI,
    "varsayilan": ck.YANSIMA_VARSAYILAN,
    "temperli": ck.TEMPERLI_KONTROLLERI,
    "yansima": ck.YANSIMA_KONTROLLERI,
}
TEMPERLI_ANAHTARLARI = ["temperli"] + [k for k, *_ in ck.TEMPERLI_KONTROLLERI]
AYAR_ANAHTARLARI = {"acik", "guc", "taraf", *TEMPERLI_ANAHTARLARI, *(k for k, *_ in ck.YANSIMA_KONTROLLERI)}


class Hata(Exception):
    """Kullanıcıya gösterilecek hata (sadece işlemi yapan cihaza gider)."""


# ----------------------------------------------------------------------
# YARDIMCILAR
# ----------------------------------------------------------------------
def resim_coz(veri):
    return cv2.imdecode(np.frombuffer(veri, np.uint8), cv2.IMREAD_COLOR)


def jpeg(resim, kalite=88):
    ok, buf = cv2.imencode(".jpg", resim, [cv2.IMWRITE_JPEG_QUALITY, kalite])
    if not ok:
        raise IOError("Resim kodlanamadı")
    return buf.tobytes()


def kucult(resim, maks):
    h, w = resim.shape[:2]
    o = maks / max(h, w)
    return cv2.resize(resim, None, fx=o, fy=o, interpolation=cv2.INTER_AREA) if o < 1 else resim


def uzanti(ad, varsayilan=".jpg"):
    u = os.path.splitext(ad or "")[1].lower()
    return u if u in RESIM_UZANTILARI else varsayilan


def dosya_sil(yol):
    try:
        if yol and os.path.isfile(yol):
            os.remove(yol)
    except OSError:
        pass


def indirme_basligi(dosya_adi):
    """Türkçe karakterli dosya adları için Content-Disposition."""
    ascii_ad = dosya_adi.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    return {"Content-Disposition": f"attachment; filename=\"{ascii_ad}\"; filename*=UTF-8''{quote(dosya_adi)}"}


def oda_klasor_adi(oda):
    """Odanın alt klasör adı; aynı adlı odalar karışmasın diye id de eklenir."""
    oda_adi = "".join(c if c.isalnum() or c in "-_ " else "_" for c in oda["ad"]).strip()
    return f"{oda_adi or 'oda'}_{oda['id']:04d}"


def json_cevir(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return list(o)
    raise TypeError(type(o).__name__)


def json_metni(veri):
    return json.dumps(veri, ensure_ascii=False, default=json_cevir)


class Onbellek:
    """Küçük, iş parçacığı güvenli LRU önbellek."""

    def __init__(self, boyut):
        self.boyut, self.veri, self.kilit = boyut, OrderedDict(), Lock()

    def al(self, anahtar, uret):
        with self.kilit:
            if anahtar in self.veri:
                self.veri.move_to_end(anahtar)
                return self.veri[anahtar]
        deger = uret()   # kilit dışında: uzun süren üretim diğer çizimleri bekletmesin
        with self.kilit:
            self.veri[anahtar] = deger
            while len(self.veri) > self.boyut:
                self.veri.popitem(last=False)
        return deger

    def temizle(self):
        with self.kilit:
            self.veri.clear()


# ----------------------------------------------------------------------
# BAĞLI CİHAZLAR
# ----------------------------------------------------------------------
class Istemci:
    """Bir tarayıcı bağlantısı. Mesajlar kuyruktan gönderilir: yavaş bir bağlantı diğerlerini bekletmez."""

    def __init__(self, ws, ad):
        self.id = uuid.uuid4().hex[:8]
        self.ws = ws
        self.ad = ad
        self.kuyruk = asyncio.Queue()
        self.gorev = asyncio.create_task(self.gonderici())

    async def gonderici(self):
        try:
            while True:
                await self.ws.send_text(await self.kuyruk.get())
        except Exception:
            pass

    def gonder(self, metin):
        if self.kuyruk.qsize() < 1000:
            self.kuyruk.put_nowait(metin)


class Kisi:
    """HTTP yüklemelerinde işlemi yapan kişi (WebSocket istemcisi yerine)."""

    def __init__(self, ad):
        self.ad, self.id = ad, None


# ----------------------------------------------------------------------
# ÖNİZLEME ÇİZİCİ
# ----------------------------------------------------------------------
class Cizici:
    """Bir görünümün (oda / tablo) önizleme resmini arka planda çizer ve hazır olunca duyurur.
    Arka arkaya gelen istekler tek çizimde birleşir (gecikme birikmez). hizli=True istekte önce küçük boyutta
    hızlı çizilir; değişiklik 350 ms durunca kaliteli çizim gelir (masaüstündeki önizleme / tam çizim gibi)."""

    def __init__(self, ad, studyo, anlik, ciz, hizli_mod):
        self.ad, self.studyo, self.anlik, self.ciz, self.hizli_mod = ad, studyo, anlik, ciz, hizli_mod
        self.surum = 0
        self.resim = None
        self.olay = None
        self.kaliteli = False
        self.zamanlayici = None

    def baslat(self):
        self.olay = asyncio.Event()
        asyncio.create_task(self.dongu())

    def degisti(self, hizli=True):
        if self.zamanlayici is not None:
            self.zamanlayici.cancel()
            self.zamanlayici = None
        if hizli and self.hizli_mod:
            self.zamanlayici = asyncio.get_running_loop().call_later(0.35, self._kaliteli_iste)
        else:
            self.kaliteli = True
        self.olay.set()

    def _kaliteli_iste(self):
        self.zamanlayici = None
        self.kaliteli = True
        self.olay.set()

    async def dongu(self):
        while True:
            await self.olay.wait()
            self.olay.clear()
            kaliteli, self.kaliteli = self.kaliteli, False
            anlik = self.anlik()
            try:
                resim = await asyncio.to_thread(self.ciz, anlik, kaliteli)
                veri = await asyncio.to_thread(jpeg, resim, 90 if kaliteli else 80) if resim is not None else None
            except Exception as e:
                traceback.print_exc()
                veri = None
                self.studyo.yayinla({"tip": "bildirim", "metin": f"Önizleme çizilemedi: {e}", "tur": "danger"})
            self.surum += 1
            self.resim = veri
            self.studyo.yayinla(self.mesaj())

    def mesaj(self):
        return {"tip": "goruntu", "gorunum": self.ad, "v": self.surum, "yok": self.resim is None}


# ----------------------------------------------------------------------
# KULLANICININ ÇALIŞMA ALANI
# ----------------------------------------------------------------------
class Studyo:
    """Bir kullanıcının çalışma alanı: kendi odaları, tablo resimleri, ayarları ve bağlı cihazları."""

    def __init__(self, kullanici):
        self.kullanici = kullanici
        self.klasor = os.path.join(KULLANICI_VERI, str(kullanici["id"]))
        self.oda_klasoru = os.path.join(self.klasor, "odalar")       # kayıtlı odalar (odalar.db + resimler)
        self.gecici = os.path.join(self.klasor, "gecici")            # kaydedilmemiş oda / tablo resimleri
        self.tablolar = os.path.join(self.klasor, "tablolar")        # 2. sekmeye yüklenen tablo resimleri
        self.kucuk = os.path.join(self.tablolar, "kucuk")            # tablo resimlerinin küçük halleri
        self.indirmeler = os.path.join(self.klasor, "indirmeler")    # toplu indirme zip dosyaları
        self.durum_dosyasi = os.path.join(self.klasor, "durum.json")
        for k in (self.oda_klasoru, self.gecici, self.tablolar, self.kucuk, self.indirmeler):
            os.makedirs(k, exist_ok=True)

        self.istemciler = {}
        self.kilit = None          # aksiyonlar sırayla uygulanır (iki cihaz aynı anda değiştirse de durum bozulmaz)
        self.dongu = None
        self.hazir = None
        self.sekme = "oda"

        # 1. sekme: Oda Ayarla
        self.oda = self.bos_oda_durumu()
        self.oda_resmi = None      # sunucu belleğindeki resimler (durum dosyasına yazılmaz)
        self.oda_surum = 0
        self.isik_analizi = None
        self.tablo_resmi = None
        self.tablo_surum = 0

        # 2. sekme: Tablo Ekle
        self.tablo = {"eserler": [], "sayac": 0, "atamalar": {}, "secili": [], "aktif_eser": None,
                      "yansima_acik": True, "bilgi": None}
        self.odalar = []
        self.odalar_surum = 0
        self.tablo_goruntu_anahtari = None
        self.indirme_suruyor = False

        self.kucuk_onbellek = Onbellek(4)    # önizleme boyutuna küçültülmüş oda resmi
        self.tablo_onbellek = Onbellek(4)    # çizim boyutuna küçültülmüş tablo resmi
        self.oda_onbellek = Onbellek(12)     # 2. sekme: oda id -> (resim, köşeler, ışık analizi)
        self.eser_onbellek = Onbellek(16)    # 2. sekme: eser id -> resim

        self.oda_cizici = Cizici("oda", self, self.oda_anlik, self.oda_ciz, hizli_mod=True)
        self.tablo_cizici = Cizici("tablo", self, self.tablo_anlik, self.tablo_ciz, hizli_mod=False)
        self.kayit_zamanlayici = None

    @staticmethod
    def bos_oda_durumu():
        return {"dosya": None, "ad": "", "genislik": 0, "yukseklik": 0, "kose": [],
                "tip": "yatay", "tip_elle": False,
                "cerceve_ekle": False,      # çerçevesiz cam: kenar parlaklığı ve duvar gölgesi çizilsin mi
                "otomatik_duvar": False,    # köşeler boş duvara otomatik mı yerleştirildi
                "kose_elle_degisti": False,
                "aktif_oda_id": None,       # veritabanından açılan / kaydedilen odanın id'si
                "tablo_dosya": None, "tablo_adi": "",
                "ayar": dict(ck.YANSIMA_VARSAYILAN),
                "isik_ozeti": "", "bilgi": None}

    # ------------------------------------------------------------------
    # BAŞLATMA / KALICI DURUM
    # ------------------------------------------------------------------
    async def baslat(self):
        self.dongu = asyncio.get_running_loop()
        self.kilit = asyncio.Lock()
        try:
            await asyncio.to_thread(self.durumu_yukle)
        except Exception:
            traceback.print_exc()
        self.odalar_yenile()
        self.oda_cizici.baslat()
        self.tablo_cizici.baslat()
        self.oda_cizici.degisti(hizli=False)
        self.tablo_goruntu_kontrol()

    @staticmethod
    def dosya_bul(yol, *klasorler):
        """Durum dosyasındaki resim yolunu kullanıcının kendi klasörlerinde bulur (veri klasörü taşınsa da çalışsın,
        başka klasördeki bir dosyaya asla işaret etmesin)."""
        if not yol:
            return None
        for k in klasorler:
            aday = os.path.join(k, os.path.basename(yol))
            if os.path.isfile(aday):
                return aday
        return None

    def durumu_yukle(self):
        if not os.path.isfile(self.durum_dosyasi):
            return
        with open(self.durum_dosyasi, encoding="utf-8") as f:
            veri = json.load(f)
        self.sekme = veri.get("sekme", "oda")
        oda = {**self.bos_oda_durumu(), **veri.get("oda", {})}
        eski_ayar = oda.get("ayar", {})
        oda["ayar"] = ck.yansima_ayari_duzelt(eski_ayar)   # eski sürümün ayarları yok sayılır
        oda["dosya"] = self.dosya_bul(oda["dosya"], self.oda_klasoru, self.gecici)
        oda["tablo_dosya"] = self.dosya_bul(oda["tablo_dosya"], self.gecici)
        resim = ck.turkce_karakter_destekli_imread(oda["dosya"]) if oda["dosya"] else None
        if resim is None:
            oda = {**self.bos_oda_durumu(), "tablo_dosya": oda["tablo_dosya"], "tablo_adi": oda["tablo_adi"]}
        else:
            self.oda_resmi = resim
            self.isik_analizi = ck.isik_kaynaklarini_bul(resim)
        if oda["tablo_dosya"]:
            self.tablo_resmi = ck.turkce_karakter_destekli_imread(oda["tablo_dosya"])
        if self.tablo_resmi is None:
            oda["tablo_dosya"], oda["tablo_adi"] = None, ""
        self.oda = oda
        self.oda_surum += 1
        self.tablo_surum += 1

        tablo = {**self.tablo, **veri.get("tablo", {})}
        tablo["eserler"] = [e for e in tablo["eserler"] if os.path.isfile(os.path.join(self.tablolar, e["dosya"]))]
        self.tablo = tablo

    def kaydet_planla(self):
        if self.kayit_zamanlayici is None:
            self.kayit_zamanlayici = self.dongu.call_later(1.0, self.durumu_kaydet)

    def durumu_kaydet(self):
        self.kayit_zamanlayici = None
        try:
            gecici = self.durum_dosyasi + ".yeni"
            with open(gecici, "w", encoding="utf-8") as f:
                f.write(json_metni({"sekme": self.sekme, "oda": self.oda, "tablo": self.tablo}))
            os.replace(gecici, self.durum_dosyasi)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------
    # YAYIN (bu kullanıcının bağlı cihazlarına)
    # ------------------------------------------------------------------
    def yayinla(self, mesaj, sadece=None):
        metin = json_metni(mesaj)
        for ist in list(self.istemciler.values()):
            if sadece is None or ist.id == sadece:
                ist.gonder(metin)

    def bolum_verisi(self, bolum):
        t = self.tablo
        if bolum == "oda":
            v = {k: d for k, d in self.oda.items() if k not in ("dosya", "tablo_dosya")}
            v["oda_var"] = self.oda_resmi is not None
            v["tablo_var"] = self.tablo_resmi is not None
            return v
        if bolum == "odalar":
            return [{"id": o["id"], "ad": o["ad"], "tip": o["tip"], "bos_duvar": o["bos_duvar"],
                     "guncelleme": o.get("guncelleme", ""),
                     "yansima": o["yansima"]["guc"] if o["yansima"]["acik"] and o["yansima"]["guc"] > 0
                     else None} for o in self.odalar]
        if bolum == "eserler":
            return [{"id": e["id"], "ad": e["ad"], "tip": e["tip"]} for e in t["eserler"]]
        if bolum == "atamalar":
            return t["atamalar"]
        if bolum == "secim":
            return {"secili": t["secili"], "aktif_eser": t["aktif_eser"], "yansima_acik": t["yansima_acik"],
                    "bilgi": t["bilgi"], "indirme_suruyor": self.indirme_suruyor}
        if bolum == "sekme":
            return self.sekme
        raise ValueError(bolum)

    def durum_yayinla(self, *bolumler):
        for b in bolumler:
            self.yayinla({"tip": "durum", "bolum": b, "veri": self.bolum_verisi(b)})
        self.kaydet_planla()

    def cihazlari_yayinla(self):
        self.yayinla({"tip": "kullanicilar", "liste": [{"id": i.id, "ad": i.ad} for i in self.istemciler.values()]})

    def hos_geldin(self, ist):
        return {"tip": "hos_geldin", "id": ist.id, "kontroller": KONTROLLER,
                "kullanici": {"ad": self.kullanici["ad"], "kullanici_adi": self.kullanici["kullanici_adi"]},
                "durum": {b: self.bolum_verisi(b) for b in ("oda", "odalar", "eserler", "atamalar", "secim", "sekme")},
                "goruntuler": [self.oda_cizici.mesaj(), self.tablo_cizici.mesaj()]}

    def oda_bilgi(self, kim, metin, tur="info"):
        self.oda["bilgi"] = {"metin": metin, "tur": tur, "kim": kim}

    def tablo_bilgi(self, kim, metin, tur="info"):
        self.tablo["bilgi"] = {"metin": metin, "tur": tur, "kim": kim}

    # ------------------------------------------------------------------
    # AKSİYONLAR (tarayıcıdan gelen işlemler)
    # ------------------------------------------------------------------
    async def aksiyon(self, ist, mesaj):
        islem = getattr(self, "a_" + str(mesaj.get("ad", "")), None)
        if islem is None:
            return
        try:
            async with self.kilit:
                await islem(ist, mesaj)
        except Hata as e:
            self.yayinla({"tip": "hata", "metin": str(e)}, sadece=ist.id)
        except Exception as e:
            traceback.print_exc()
            self.yayinla({"tip": "hata", "metin": f"İşlem yapılamadı: {e}"}, sadece=ist.id)

    async def a_sekme(self, ist, m):
        if m.get("sekme") in ("oda", "tablo"):
            self.sekme = m["sekme"]
            self.durum_yayinla("sekme")

    # ---------------- 1. sekme: Oda Ayarla ----------------
    def tip_tahmin(self):
        o = self.oda
        if len(o["kose"]) == 4 and not o["tip_elle"]:
            o["tip"] = ck.tablo_tipi_tahmin(o["kose"])

    def oda_degisti(self, hizli=False):
        self.oda_cizici.degisti(hizli)
        self.durum_yayinla("oda")

    async def oda_resmi_ayarla(self, ist, resim, yol, ad):
        """Yeni (kaydedilmemiş) oda resmi: köşeler ve ayarlar sıfırlanır, pencereler / ışık bulunur."""
        analiz = await asyncio.to_thread(ck.isik_kaynaklarini_bul, resim)
        self.gecici_oda_sil()
        h, w = resim.shape[:2]
        o = self.bos_oda_durumu()
        o.update(dosya=yol, ad=ad, genislik=w, yukseklik=h,
                 tablo_dosya=self.oda["tablo_dosya"], tablo_adi=self.oda["tablo_adi"])
        self.oda, self.oda_resmi, self.isik_analizi = o, resim, analiz
        self.oda_surum += 1
        o["isik_ozeti"] = self.isik_ozeti()
        # Zaman kazanmak için tablo alanı yüklenir yüklenmez otomatik aranır (yanlışsa elle düzeltilir)
        await self.a_otomatik_bul(ist, {"ozet": o["isik_ozeti"]})
        o["ayar"] = await asyncio.to_thread(self.otomatik_yansima, o["ayar"])
        self.oda_degisti()

    def otomatik_yansima(self, ayar):
        """Ayarın yansıma geometrisi / kontrastı / temperli camı otomatik değerlere (kullanıcının kayıtlı odalarından)."""
        o = self.oda
        if self.oda_resmi is None or len(o["kose"]) != 4:
            return ayar
        return ck.otomatik_yansima_ayari(self.oda_resmi, self.isik_analizi, o["kose"], o["tip"], ayar,
                                         cercevesiz=True if o["otomatik_duvar"] else None)

    def gecici_oda_sil(self):
        if self.oda["dosya"] and os.path.dirname(os.path.abspath(self.oda["dosya"])) == os.path.abspath(self.gecici):
            dosya_sil(self.oda["dosya"])

    def isik_ozeti(self):
        analiz = self.isik_analizi
        pencereler = analiz["pencereler"] if analiz else []
        tavan = analiz.get("tavan_pencereleri", []) if analiz else []
        parcalar = []
        if pencereler:
            W = analiz["boyut"][0]
            def yer(p):
                x = p["quad"].mean(axis=0)[0] / W
                return "solda" if x < 0.4 else "sağda" if x > 0.6 else "ortada"
            parcalar.append(f"{len(pencereler)} pencere bulundu: " + ", ".join(f"{p['nx']}x{p['ny']} {yer(p)}" for p in pencereler))
        if tavan:
            parcalar.append("tavanda ışıklık bulundu (tablonun üstündeyse ışık üstten gelir)")
        return "; ".join(parcalar) or "pencere görünmüyor, yansıma odanın ışığının geldiği yana göre"

    async def a_kose(self, ist, m):
        if self.oda_resmi is None:
            return
        kose = np.float32(m["kose"]).reshape(4, 2)
        h, w = self.oda_resmi.shape[:2]
        kose[:, 0] = np.clip(kose[:, 0], 0, w)
        kose[:, 1] = np.clip(kose[:, 1], 0, h)
        o = self.oda
        o["kose"] = kose.tolist()
        o["kose_elle_degisti"] = True
        self.tip_tahmin()
        if m.get("bitti"):
            self.oda_bilgi(ist.ad, "Tablo alanı taşındı. Köşe, kenar veya tablonun ortasından sürükleyerek ayarlayabilirsiniz.")
        self.oda_degisti(hizli=not m.get("bitti"))

    async def a_ayar(self, ist, m):
        anahtar, deger = m.get("anahtar"), m.get("deger")
        if anahtar not in AYAR_ANAHTARLARI:
            return
        eski = self.oda["ayar"]
        self.oda["ayar"] = ck.yansima_ayari_duzelt({**eski, anahtar: deger})
        if anahtar == "taraf" and deger != eski["taraf"]:
            # Yansıma otomatik değerlerdeyse yeni tarafın otomatik değerlerine geçsin (açı tarafa göre değişir)
            if await asyncio.to_thread(lambda: ck.otomatik_mi(eski, self.otomatik_yansima(eski))):
                self.oda["ayar"] = await asyncio.to_thread(self.otomatik_yansima, self.oda["ayar"])
        self.oda_degisti(hizli=not m.get("bitti"))

    async def a_tip(self, ist, m):
        if m.get("tablo_tipi") in ck.TABLO_TIPLERI:
            self.oda["tip"] = m["tablo_tipi"]
            self.oda["tip_elle"] = True   # artık köşe oranından otomatik değiştirme
            self.oda_degisti()

    async def a_otomatik_bul(self, ist, m):
        if self.oda_resmi is None:
            return
        ek = f" ({m['ozet']})" if m.get("ozet") else ""
        ic, tip = await asyncio.to_thread(ck.tablo_yerini_bul, self.oda_resmi)
        if ic is None:
            # Odada tablo yok: boş duvara perspektife uygun yeni bir tablo alanı aç
            await self.a_bos_duvar(ist, m)
            self.oda_bilgi(ist.ad, f"Odada tablo bulunamadı; boş duvara duvar açısına uygun bir tablo alanı yerleştirildi{ek}.")
            self.oda_degisti()
            return
        o = self.oda
        o.update(kose=[[float(p[0]), float(p[1])] for p in ic], cerceve_ekle=False, otomatik_duvar=False,
                 kose_elle_degisti=False)
        if tip == "yuvarlak":
            # Yuvarlak tablo bulundu: tip sabitlensin (köşe sürüklenince kare / yatay tahminine dönmesin)
            o.update(tip="yuvarlak", tip_elle=True)
            self.oda_bilgi(ist.ad, f"Yuvarlak tablo otomatik bulundu{ek}! Köşeleri, kenarları ya da ortasını sürükleyerek ayarlayabilirsiniz.")
        else:
            o["tip_elle"] = False   # dikdörtgen bulundu: tip köşelerin oranından tahmin edilsin
            self.tip_tahmin()
            self.oda_bilgi(ist.ad, f"Çerçeve içi otomatik bulundu{ek}! Köşeleri, kenarları ya da ortasını sürükleyerek ayarlayabilirsiniz.")
        self.oda_degisti()

    async def a_bos_duvar(self, ist, m):
        if self.oda_resmi is None:
            return
        if self.tablo_resmi is not None:
            h_art, w_art = self.tablo_resmi.shape[:2]
            oran = w_art / h_art
        else:
            oran = 1.4  # varsayılan yatay tablo
        pts = await asyncio.to_thread(ck.duvara_tablo_yerlestir, self.oda_resmi, oran)
        o = self.oda
        o.update(kose=[[float(p[0]), float(p[1])] for p in pts], cerceve_ekle=True, otomatik_duvar=True,
                 kose_elle_degisti=False)
        self.tip_tahmin()
        if self.tablo_resmi is None:
            self.oda_bilgi(ist.ad, "Boş duvara tablo alanı yerleştirildi. Şimdi tablo içine resim seçin; köşeleri sürükleyerek ayarlayabilirsiniz.")
        self.oda_degisti()

    async def a_cesit(self, ist, m):
        """Başka bir yansıma dene: aynı odanın ışığına uygun farklı bir pencere / konum."""
        o = self.oda
        o["ayar"] = ck.yansima_ayari_duzelt({**o["ayar"], "acik": True, "cesit": o["ayar"]["cesit"] + 1})
        self.oda_bilgi(ist.ad, f"Yansıma çeşidi {o['ayar']['cesit'] + 1}: {self.yansima_ozeti()}.")
        self.oda_degisti()

    async def a_sifirla(self, ist, m):
        # Temperli cam ayarları ayrı sıfırlanır (yansımayı otomatiğe döndürmek onları bozmasın)
        temperli = {k: self.oda["ayar"][k] for k in TEMPERLI_ANAHTARLARI}
        ayar = ck.yansima_ayari_duzelt({**ck.YANSIMA_VARSAYILAN, **temperli})
        self.oda["ayar"] = await asyncio.to_thread(self.otomatik_yansima, ayar)
        self.oda_bilgi(ist.ad, f"Yansıma otomatik hale döndü: {self.yansima_ozeti()}.")
        self.oda_degisti()

    async def a_temperli_sifirla(self, ist, m):
        varsayilan = {k: ck.YANSIMA_VARSAYILAN[k] for k in TEMPERLI_ANAHTARLARI}
        self.oda["ayar"] = ck.yansima_ayari_duzelt({**self.oda["ayar"], **varsayilan})
        self.oda_bilgi(ist.ad, "Temperli cam ayarları varsayılana döndü.")
        self.oda_degisti()

    def yansima_ozeti(self):
        """Otomatik yansımanın kısa açıklaması (bilgi satırı için)."""
        o = self.oda
        if self.isik_analizi is None or len(o["kose"]) != 4:
            return "tablo alanı belirlenince hesaplanır"
        return ck.yansima_aciklamasi(ck.cam_yansimasi_plani(self.isik_analizi, o["kose"], o["ayar"], o["tip"]))

    def yansima_verisi(self):
        """Veritabanına yazılacak yansıma ayarları ve algılanan pencere / ışık bilgisi."""
        o, ayar, analiz = self.oda, self.oda["ayar"], self.isik_analizi
        isik = None
        if analiz is not None:
            isik = {
                "pencereler": [{
                    "koseler": np.round(p["quad"].astype(float), 1).tolist(),
                    "bolme": f"{p['nx']}x{p['ny']}",
                    "renk_bgr": np.round(p["renk"]).astype(int).tolist(),
                    "parlaklik": round(float(p["parlaklik"]), 3),
                    "egim": round(float(p.get("egim", 0.0)), 3),
                } for p in analiz["pencereler"]],
                "isik_kaynagi": {
                    "konum": np.round(analiz["isik"]["konum"].astype(float), 1).tolist(),
                    "renk_bgr": np.round(analiz["isik"]["renk"]).astype(int).tolist(),
                    "parlaklik": round(float(analiz["isik"]["parlaklik"]), 3),
                },
            }
        return {
            **ck.yansima_kaydi(ayar),
            "isik_verisi": json.dumps(isik, ensure_ascii=False) if isik else None,
        }

    async def a_kaydet(self, ist, m):
        o = self.oda
        if self.oda_resmi is None or len(o["kose"]) != 4:
            raise Hata("Önce oda resmi yükleyip tablo alanını belirleyin!")
        koordinatlar = [float(v) for pt in ck.kose_noktalarini_sirala(np.float32(o["kose"])) for v in pt]
        simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        h, w = self.oda_resmi.shape[:2]
        yansima = {**self.yansima_verisi(), "tablo_tipi": o["tip"]}

        conn = ck.veritabani_baglan(self.oda_klasoru)
        try:
            # Kayıtlı bir oda açıksa onu güncelle
            if o["aktif_oda_id"] is not None and conn.execute(
                    "SELECT 1 FROM odalar WHERE id = ?", (o["aktif_oda_id"],)).fetchone():
                atama = ", ".join(f"{s} = ?" for s in ck.KOORDINAT_SUTUNLARI + list(yansima))
                conn.execute(f"UPDATE odalar SET {atama}, bos_duvar = ?, guncelleme = ? WHERE id = ?",
                             koordinatlar + list(yansima.values()) + [int(o["otomatik_duvar"]), simdi, o["aktif_oda_id"]])
                conn.commit()
                self.oda_bilgi(ist.ad, f"Oda #{o['aktif_oda_id']} güncellendi (koordinatlar, tablo tipi ve yansıma ayarları).", "success")
            else:
                ad = str(m.get("oda_adi") or "").strip()
                if not ad:
                    o["aktif_oda_id"] = None
                    self.durum_yayinla("oda")
                    raise Hata("Bu oda kayıtlarda yok (silinmiş olabilir). Yeni oda olarak kaydetmek için tekrar 'Odayı Kaydet'e basın.")
                ek_sutunlar = ck.KOORDINAT_SUTUNLARI + list(yansima)
                cur = conn.execute(
                    f"INSERT INTO odalar (ad, dosya, genislik, yukseklik, {', '.join(ek_sutunlar)}, bos_duvar, "
                    f"kayit_tarihi, guncelleme) VALUES (?, '', ?, ?, {', '.join('?' * len(ek_sutunlar))}, ?, ?, ?)",
                    [ad, w, h] + koordinatlar + list(yansima.values()) + [int(o["otomatik_duvar"]), simdi, simdi])
                oda_id = cur.lastrowid

                # Oda resmini kullanıcının odalar/ klasörüne kopyala
                uz = uzanti(o["dosya"])
                dosya_adi = f"oda_{oda_id:04d}{uz}"
                hedef = os.path.join(self.oda_klasoru, dosya_adi)
                if o["dosya"] and os.path.isfile(o["dosya"]):
                    shutil.copy2(o["dosya"], hedef)
                else:
                    ck.resim_yaz(hedef, self.oda_resmi)
                conn.execute("UPDATE odalar SET dosya = ? WHERE id = ?", (dosya_adi, oda_id))
                conn.commit()
                self.gecici_oda_sil()
                o.update(dosya=hedef, aktif_oda_id=oda_id, ad=ad)
                self.oda_bilgi(ist.ad, f"Oda #{oda_id} '{ad}' kaydedildi; 'Tablo Ekle' sekmesindeki listede görünecek.", "success")
        except Hata:
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self.yayinla({"tip": "bildirim", "metin": o["bilgi"]["metin"], "tur": "success", "kim": ist.ad})
        self.odalar_yenile()
        self.durum_yayinla("oda", "odalar", "secim", "atamalar")
        self.tablo_goruntu_kontrol()
        isitmayi_planla(self.kullanici["id"])   # mağazadaki ürünlerin bu odadaki görselleri yenilensin

    async def a_kayitli_ac(self, ist, m):
        oda_id = int(m["id"])
        ayar_sutunlari = ck.YANSIMA_AYAR_SUTUNLARI
        conn = ck.veritabani_baglan(self.oda_klasoru)
        try:
            satir = conn.execute(
                f"SELECT ad, dosya, bos_duvar, tablo_tipi, {', '.join(ck.KOORDINAT_SUTUNLARI + ayar_sutunlari)} "
                f"FROM odalar WHERE id = ?", (oda_id,)).fetchone()
        finally:
            conn.close()
        if satir is None:
            raise Hata("Kayıt bulunamadı!")
        ad, dosya, bos_duvar, tablo_tipi, *degerler = satir
        k = degerler[:8]
        if None in k:
            raise Hata("Bu odanın tablo koordinatları eksik.")
        kayitli = dict(zip(ayar_sutunlari, degerler[8:]))
        yol = os.path.join(self.oda_klasoru, os.path.basename(dosya))
        resim = await asyncio.to_thread(ck.turkce_karakter_destekli_imread, yol) if os.path.isfile(yol) else None
        if resim is None:
            raise Hata(f"Oda resmi bulunamadı: {dosya}")
        analiz = await asyncio.to_thread(ck.isik_kaynaklarini_bul, resim)

        self.gecici_oda_sil()
        h, w = resim.shape[:2]
        o = self.bos_oda_durumu()
        o.update(dosya=yol, ad=ad, genislik=w, yukseklik=h,
                 tablo_dosya=self.oda["tablo_dosya"], tablo_adi=self.oda["tablo_adi"],
                 kose=[[k[0], k[1]], [k[2], k[3]], [k[4], k[5]], [k[6], k[7]]],
                 otomatik_duvar=bool(bos_duvar), cerceve_ekle=bool(bos_duvar),
                 kose_elle_degisti=True,   # kayıtlı koordinatlar resim seçilince yeniden hesaplanmasın
                 aktif_oda_id=oda_id)
        # Kayıtlı yansıma ayarları (boş değerler varsayılan kalır)
        o["ayar"] = ck.yansima_ayarini_coz(kayitli)
        # Kayıtlı tip korunur; eski kayıtlarda tip yoksa koordinattan tahmin edilir
        o["tip"] = tablo_tipi if tablo_tipi in ck.TABLO_TIPLERI else ck.tablo_tipi_tahmin(o["kose"])
        o["tip_elle"] = tablo_tipi in ck.TABLO_TIPLERI

        self.oda, self.oda_resmi, self.isik_analizi = o, resim, analiz
        self.oda_surum += 1
        o["isik_ozeti"] = self.isik_ozeti()
        self.oda_bilgi(ist.ad, f"Kayıtlı oda #{oda_id} '{ad}' açıldı. Köşeleri ayarlayıp tekrar kaydedebilirsiniz.")
        self.oda_degisti()

    async def a_oda_sil(self, ist, m):
        oda_id = int(m["id"])
        conn = ck.veritabani_baglan(self.oda_klasoru)
        try:
            satir = conn.execute("SELECT dosya, ad FROM odalar WHERE id = ?", (oda_id,)).fetchone()
            conn.execute("DELETE FROM odalar WHERE id = ?", (oda_id,))
            conn.commit()
        finally:
            conn.close()
        if satir and satir[0]:
            yol = os.path.join(self.oda_klasoru, os.path.basename(satir[0]))
            if self.oda["aktif_oda_id"] == oda_id:
                # Açık oda silindi: resim bellekte kalsın, istenirse yeni oda olarak kaydedilebilir
                gecici = os.path.join(self.gecici, f"oda_{uuid.uuid4().hex}{uzanti(yol)}")
                if os.path.isfile(yol):
                    shutil.copy2(yol, gecici)
                self.oda.update(aktif_oda_id=None, dosya=gecici)
            dosya_sil(yol)
        self.yayinla({"tip": "bildirim", "metin": f"Oda #{oda_id} {satir[1] if satir else ''} silindi.",
                      "tur": "warning", "kim": ist.ad})
        self.odalar_yenile()
        self.durum_yayinla("oda", "odalar", "secim", "atamalar")
        self.tablo_goruntu_kontrol()
        isitmayi_planla(self.kullanici["id"])

    # ---------------- 1. sekme çizimi ----------------
    def oda_anlik(self):
        o = self.oda
        return {"resim": self.oda_resmi, "oda_surum": self.oda_surum,
                "tablo": self.tablo_resmi, "tablo_surum": self.tablo_surum,
                "isik": self.isik_analizi, "kose": [list(p) for p in o["kose"]], "tip": o["tip"],
                "cerceve": o["cerceve_ekle"], "ayar": dict(o["ayar"])}

    def oda_ciz(self, a, kaliteli, tam=False):
        """tam=True: indirilecek tam boy, tam kalite sonuç (masaüstündeki 'Sonucu İndir' ile aynı)."""
        resim = a["resim"]
        if resim is None:
            return None
        H, W = resim.shape[:2]
        s = 1.0 if tam else min(1.0, (ONIZLEME_KALITELI if kaliteli else ONIZLEME_HIZLI) / max(H, W))
        oda = resim
        if s < 1.0:
            oda = self.kucuk_onbellek.al((a["oda_surum"], s), lambda: cv2.resize(
                resim, None, fx=s, fy=s, interpolation=cv2.INTER_AREA))
        if a["tablo"] is None or len(a["kose"]) != 4:
            return oda
        dst = ck.kose_noktalarini_sirala(np.float32(a["kose"]))
        cizim = (dst * s).astype(np.float32)
        art = self.olcekli_tablo(a, cizim, 1.25 if tam else 1.0)
        return ck.tabloyu_sahneye_yerlestir(oda, art, cizim, dst, a["tip"], a["cerceve"], a["isik"], a["ayar"],
                                            hizli=not kaliteli and not tam)

    def olcekli_tablo(self, a, cizim, pay):
        """Çizim boyutuna küçültülmüş tablo resmi; tablo ve ölçek değişmedikçe önbellekten."""
        olcek = ck.tablo_olcegi(a["tablo"], cizim, pay)
        return self.tablo_onbellek.al((a["tablo_surum"], olcek), lambda: ck.tablo_resmi_hazirla(a["tablo"], cizim, pay))

    # ---------------- 2. sekme: Tablo Ekle ----------------
    def odalar_yenile(self):
        try:
            odalar = ck.kayitli_odalari_getir(self.oda_klasoru)
            conn = ck.veritabani_baglan(self.oda_klasoru)
            try:
                tarihler = dict(conn.execute("SELECT id, guncelleme FROM odalar").fetchall())
            finally:
                conn.close()
        except Exception as e:
            traceback.print_exc()
            self.tablo_bilgi("", f"Veritabanı okunamadı: {e}", "danger")
            odalar, tarihler = [], {}
        for o in odalar:
            o["guncelleme"] = tarihler.get(o["id"], "")
        self.odalar = odalar
        self.odalar_surum += 1
        self.oda_onbellek.temizle()
        self.tablo_normalize()

    def tablo_normalize(self):
        """Silinen odaları seçimden / eklemelerden çıkar; aktif resim önizlenen odanın tipinde değilse o tipteki
        ilk resme geç (masaüstündeki guncelle ile aynı)."""
        t = self.tablo
        idler = [o["id"] for o in self.odalar]
        secili = set(t["secili"])
        t["secili"] = [i for i in idler if i in secili]
        if not t["secili"] and idler:
            t["secili"] = [idler[0]]
        id_kume = set(idler)
        t["atamalar"] = {k: v for k, v in t["atamalar"].items() if int(k) in id_kume}
        eser_idler = {e["id"] for e in t["eserler"]}
        if t["aktif_eser"] not in eser_idler:
            t["aktif_eser"] = t["eserler"][0]["id"] if t["eserler"] else None
        oda = self.onizlenen_oda()
        sira = self.oda_tipindeki(oda) if oda else []
        if sira and t["aktif_eser"] not in sira:
            t["aktif_eser"] = sira[0]

    def onizlenen_oda(self):
        s = self.tablo["secili"]
        return next((o for o in self.odalar if o["id"] == s[0]), None) if s else None

    def oda_tipindeki(self, oda):
        """Odaya uyan tipteki resimlerin id'leri (yuvarlak odada kare resimler)."""
        rt = ck.oda_resim_tipi(oda)
        return [e["id"] for e in self.tablo["eserler"] if e["tip"] == rt]

    def eser(self, eser_id):
        return next((e for e in self.tablo["eserler"] if e["id"] == eser_id), None)

    def ekli_mi(self, oda, eser_id):
        return eser_id in self.tablo["atamalar"].get(str(oda["id"]), [])

    def odaya_eklenenler(self, oda):
        ekli = set(self.tablo["atamalar"].get(str(oda["id"]), []))
        rt = ck.oda_resim_tipi(oda)
        return [e for e in self.tablo["eserler"] if e["id"] in ekli and e["tip"] == rt]

    def ata(self, odalar, eser_id, ekle):
        for oda in odalar:
            liste = self.tablo["atamalar"].setdefault(str(oda["id"]), [])
            if ekle and eser_id not in liste:
                liste.append(eser_id)
            elif not ekle and eser_id in liste:
                liste.remove(eser_id)

    def tablo_degisti(self, *bolumler):
        self.tablo_normalize()
        self.durum_yayinla(*bolumler)
        self.tablo_goruntu_kontrol()

    async def a_sec(self, ist, m):
        self.tablo["secili"] = [int(i) for i in m.get("secili", [])]
        self.tablo_degisti("secim")

    async def a_eser_goster(self, ist, m):
        if self.eser(m.get("id")) is not None:
            self.tablo["aktif_eser"] = m["id"]
            self.tablo_degisti("secim")

    async def a_eser_degistir(self, ist, m):
        """Önizlenen odanın tipindeki resimler arasında gezinir (sonda başa döner)."""
        oda = self.onizlenen_oda()
        if oda is None:
            return
        sira = self.oda_tipindeki(oda)
        if len(sira) < 2:
            return
        konum = sira.index(self.tablo["aktif_eser"]) if self.tablo["aktif_eser"] in sira else 0
        self.tablo["aktif_eser"] = sira[(konum + int(m.get("adim", 1))) % len(sira)]
        self.tablo_degisti("secim")

    async def a_eser_tipi(self, ist, m):
        e = self.eser(self.tablo["aktif_eser"])
        if e is None or m.get("resim_tipi") not in ck.RESIM_TIPLERI:
            return
        e["tip"] = m["resim_tipi"]
        oda = self.onizlenen_oda()
        if oda is not None and e["tip"] != ck.oda_resim_tipi(oda):
            yer = "kare ve yuvarlak" if e["tip"] == "kare" else ck.TABLO_TIPLERI[e["tip"]].lower()
            self.tablo_bilgi(ist.ad, f"'{e['ad']}' artık {ck.tip_etiketi(e['tip'])}; {yer} odalarda görünecek.", "warning")
        self.tablo_degisti("eserler", "secim")

    async def a_ata_degistir(self, ist, m):
        """Önizlenen resmi önizlenen odaya (ve aynı tipteki diğer seçili odalara) ekler ya da çıkarır."""
        oda, e = self.onizlenen_oda(), self.eser(self.tablo["aktif_eser"])
        if oda is None or e is None or e["tip"] != ck.oda_resim_tipi(oda):
            return
        secili = set(self.tablo["secili"])
        hedef = [o for o in self.odalar if o["id"] in secili and ck.oda_resim_tipi(o) == e["tip"]]
        ekle = not self.ekli_mi(oda, e["id"])
        self.ata(hedef, e["id"], ekle)
        self.tablo_bilgi(ist.ad, f"'{e['ad']}' {len(hedef)} odaya {'eklendi' if ekle else 'çıkarıldı'}.")
        self.tablo_degisti("atamalar", "secim")

    async def a_kutu(self, ist, m):
        oda = self.onizlenen_oda()
        if oda is None or self.eser(m.get("eser")) is None:
            return
        self.ata([oda], m["eser"], bool(m.get("ekle")))
        self.tablo_degisti("atamalar")

    async def a_hepsini(self, ist, m):
        """Önizlenen odanın tipindeki tüm resimleri o odaya ekler / hepsini çıkarır."""
        oda = self.onizlenen_oda()
        if oda is None:
            return
        for eser_id in self.oda_tipindeki(oda):
            self.ata([oda], eser_id, bool(m.get("ekle")))
        self.tablo_degisti("atamalar")

    async def a_otomatik_ata(self, ist, m):
        """Her resmi, kendi tipindeki tüm odalara ekler."""
        for oda in self.odalar:
            for eser_id in self.oda_tipindeki(oda):
                self.ata([oda], eser_id, True)
        toplam = sum(len(self.odaya_eklenenler(o)) for o in self.odalar)
        self.tablo_bilgi(ist.ad, f"Tipine uyan resimler tüm odalara eklendi ({toplam} ekleme).")
        self.tablo_degisti("atamalar", "secim")

    async def a_eserleri_temizle(self, ist, m):
        for e in self.tablo["eserler"]:
            dosya_sil(os.path.join(self.tablolar, e["dosya"]))
            dosya_sil(os.path.join(self.kucuk, f"{e['kucuk']}_k.jpg"))
            dosya_sil(os.path.join(self.kucuk, f"{e['kucuk']}_o.jpg"))
        self.tablo.update(eserler=[], atamalar={}, aktif_eser=None)
        self.eser_onbellek.temizle()
        self.tablo_bilgi(ist.ad, "Tablo resimleri temizlendi.")
        self.tablo_degisti("eserler", "atamalar", "secim")

    async def a_yansima(self, ist, m):
        self.tablo["yansima_acik"] = bool(m.get("acik"))
        self.tablo_degisti("secim")

    async def a_listeyi_yenile(self, ist, m):
        self.odalar_yenile()
        self.tablo_bilgi(ist.ad, f"Oda listesi yenilendi ({len(self.odalar)} kayıtlı oda).")
        self.tablo_degisti("odalar", "secim", "atamalar")

    async def a_duzenle(self, ist, m):
        """Tablo Ekle sekmesindeki odayı Oda Ayarla sekmesinde açar."""
        await self.a_kayitli_ac(ist, m)
        self.sekme = "oda"
        self.durum_yayinla("sekme")

    async def a_toplu_indir(self, ist, m):
        if self.indirme_suruyor:
            raise Hata("Bir toplu indirme zaten sürüyor, bitmesini bekleyin.")
        isler = [(dict(oda), dict(e)) for oda in self.odalar for e in self.odaya_eklenenler(oda)]
        if not isler:
            raise Hata("Henüz hiçbir odaya resim eklenmedi.")
        self.indirme_suruyor = True
        self.tablo_bilgi(ist.ad, f"Toplu indirme hazırlanıyor ({len(isler)} görsel)…", "warning")
        self.durum_yayinla("secim")
        asyncio.create_task(self.toplu_indir(ist, isler, self.tablo["yansima_acik"]))

    async def toplu_indir(self, ist, isler, yansima_acik):
        try:
            for eski in os.listdir(self.indirmeler):   # eski zip'ler yer kaplamasın
                dosya_sil(os.path.join(self.indirmeler, eski))
            ad = f"{uuid.uuid4().hex}.zip"
            ozet, hatalar = await asyncio.to_thread(self.zip_hazirla, isler, yansima_acik,
                                                    os.path.join(self.indirmeler, ad))
            self.tablo_bilgi(ist.ad, ozet, "warning" if hatalar else "success")
            self.yayinla({"tip": "indirme_hazir", "url": f"/api/indirme/{ad}", "istemci": ist.id,
                          "ozet": ozet, "hatalar": hatalar, "kim": ist.ad})
        except Exception as e:
            traceback.print_exc()
            self.tablo_bilgi(ist.ad, f"Toplu indirme yapılamadı: {e}", "danger")
        finally:
            self.indirme_suruyor = False
            self.durum_yayinla("secim")

    def zip_hazirla(self, isler, yansima_acik, hedef):
        """Önce tipe, sonra odaya göre ayrılır: <Tip>/<oda>_<id>/<resim>.jpg"""
        basarili, hatalar, klasorler, tip_sayilari, kullanilan = 0, [], set(), {}, set()
        with zipfile.ZipFile(hedef, "w", zipfile.ZIP_STORED) as z:   # jpg zaten sıkıştırılmış
            for i, (oda, e) in enumerate(isler, 1):
                self.dongu.call_soon_threadsafe(self.yayinla, {
                    "tip": "ilerleme", "deger": i - 1, "toplam": len(isler),
                    "metin": f"Hazırlanıyor {i}/{len(isler)}: {ck.TABLO_TIPLERI[oda['tip']]} / {oda['ad']} — {e['ad']}"})
                try:
                    klasor = f"{ck.TABLO_TIPLERI[oda['tip']]}/{oda_klasor_adi(oda)}"
                    eser_adi = os.path.splitext(e["ad"])[0]
                    yol, n = f"{klasor}/{eser_adi}.jpg", 2
                    while yol.lower() in kullanilan:   # farklı klasörlerden gelen aynı adlı resimler
                        yol, n = f"{klasor}/{eser_adi}_{n}.jpg", n + 1
                    kullanilan.add(yol.lower())
                    z.writestr(yol, jpeg(self.oda_sonucu(oda, self.eser_yukle(e), yansima_acik), 95))
                    klasorler.add(klasor)
                    tip_sayilari[oda["tip"]] = tip_sayilari.get(oda["tip"], 0) + 1
                    basarili += 1
                except Exception as hata:
                    hatalar.append(f"#{oda['id']} {oda['ad']} / {e['ad']}: {hata}")
        self.dongu.call_soon_threadsafe(self.yayinla, {"tip": "ilerleme", "deger": len(isler), "toplam": len(isler),
                                                       "bitti": True})
        dagilim = ", ".join(f"{ck.TABLO_TIPLERI[t]}: {tip_sayilari[t]}" for t in ck.TABLO_TIPLERI if t in tip_sayilari)
        return f"{basarili}/{len(isler)} görsel, {len(klasorler)} oda klasörüne hazırlandı ({dagilim}).", hatalar

    # ---------------- 2. sekme çizimi ----------------
    def oda_yukle(self, oda):
        """Oda resmi, resim boyutuna ölçeklenmiş köşe koordinatları ve pencere / ışık analizi."""
        def uret():
            yol = os.path.join(self.oda_klasoru, os.path.basename(oda["dosya"]))
            resim = ck.turkce_karakter_destekli_imread(yol) if os.path.isfile(yol) else None
            if resim is None:
                raise FileNotFoundError(f"Oda resmi bulunamadı: {oda['dosya']}")
            # Resim kayıttan sonra yeniden boyutlandırıldıysa koordinatları ölçekle
            h, w = resim.shape[:2]
            kose = oda["kose"].copy()
            if oda["genislik"] and oda["yukseklik"] and (w, h) != (oda["genislik"], oda["yukseklik"]):
                kose[:, 0] *= w / oda["genislik"]
                kose[:, 1] *= h / oda["yukseklik"]
            return resim, kose, ck.isik_kaynaklarini_bul(resim)
        return self.oda_onbellek.al((oda["id"], oda.get("guncelleme")), uret)

    def eser_yukle(self, e):
        def uret():
            resim = ck.turkce_karakter_destekli_imread(os.path.join(self.tablolar, e["dosya"]))
            if resim is None:
                raise FileNotFoundError(f"Tablo resmi okunamadı: {e['ad']}")
            return kucult(resim, ESER_MAKS)
        return self.eser_onbellek.al(e["id"], uret)

    def oda_sonucu(self, oda, eser, yansima_acik, onizleme=False):
        """Odaya eklenmiş tablo görseli. onizleme=True: önizleme boyutunda (hızlı) çizilir."""
        resim, kose, analiz = self.oda_yukle(oda)
        ayar = {**oda["yansima"], "acik": oda["yansima"]["acik"] and yansima_acik}
        olcek = min(1.0, ONIZLEME_KALITELI / max(resim.shape[:2])) if onizleme else 1.0
        return ck.tabloyu_odaya_ekle(resim, kose, eser, cercevesiz=oda["bos_duvar"], isik_analizi=analiz,
                                     yansima_ayari=ayar, tip=oda["tip"], olcek=olcek)

    def tablo_onizleme_secimi(self):
        oda = self.onizlenen_oda()
        e = self.eser(self.tablo["aktif_eser"])
        if oda is None or e is None or e["tip"] != ck.oda_resim_tipi(oda):
            e = None
        return oda, e

    def tablo_goruntu_kontrol(self):
        """Önizlenen oda / resim / yansıma değiştiyse yeniden çiz (eklemeler görüntüyü değiştirmez)."""
        oda, e = self.tablo_onizleme_secimi()
        anahtar = (oda["id"] if oda else None, self.odalar_surum, e["id"] if e else None, self.tablo["yansima_acik"])
        if anahtar != self.tablo_goruntu_anahtari:
            self.tablo_goruntu_anahtari = anahtar
            self.tablo_cizici.degisti(hizli=False)

    def tablo_anlik(self):
        oda, e = self.tablo_onizleme_secimi()
        return (dict(oda) if oda else None, dict(e) if e else None, self.tablo["yansima_acik"])

    def tablo_ciz(self, anlik, kaliteli):
        oda, e, yansima_acik = anlik
        if oda is None:
            return None
        if e is None:
            # Bu tipte resim yoksa odanın kayıtlı tablo yerini göster
            resim, kose, _ = self.oda_yukle(oda)
            s = min(1.0, ONIZLEME_KALITELI / max(resim.shape[:2]))
            gosterim = cv2.resize(resim, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else resim.copy()
            cv2.polylines(gosterim, [np.int32(ck.tablo_hatti(kose * s, oda["tip"]))], True, (0, 255, 0), 3, cv2.LINE_AA)
            return gosterim
        return self.oda_sonucu(oda, self.eser_yukle(e), yansima_acik, onizleme=True)

    # ------------------------------------------------------------------
    # YÜKLEMELER (HTTP)
    # ------------------------------------------------------------------
    async def oda_yukle_http(self, ad, veri):
        resim = await asyncio.to_thread(resim_coz, veri)
        if resim is None:
            raise Hata("Seçilen resim dosyası okunamadı!")
        yol = os.path.join(self.gecici, f"oda_{uuid.uuid4().hex}{uzanti(ad)}")
        with open(yol, "wb") as f:
            f.write(veri)
        async with self.kilit:
            await self.oda_resmi_ayarla(Kisi(self.kullanici["ad"]), resim, yol, os.path.splitext(ad)[0])

    async def tablo_resmi_http(self, ad, veri):
        resim = await asyncio.to_thread(resim_coz, veri)
        if resim is None:
            raise Hata("Seçilen resim dosyası okunamadı!")
        async with self.kilit:
            if self.oda_resmi is None or len(self.oda["kose"]) != 4:
                raise Hata("Önce tablo alanı belirlenmelidir!")
            # Oda resminden büyük tablo resmi gereksiz; çizim hızlı kalsın diye küçült
            resim = kucult(resim, max(self.oda_resmi.shape[:2]))
            yol = os.path.join(self.gecici, f"tablo_{uuid.uuid4().hex}.png")
            await asyncio.to_thread(ck.resim_yaz, yol, resim)
            dosya_sil(self.oda["tablo_dosya"])
            self.tablo_resmi = resim
            self.tablo_surum += 1
            self.oda.update(tablo_dosya=yol, tablo_adi=ad)
            kisi = Kisi(self.kullanici["ad"])
            if self.oda["otomatik_duvar"] and not self.oda["kose_elle_degisti"]:
                await self.a_bos_duvar(kisi, {})   # otomatik alanı resmin en-boy oranına göre yeniden hesapla
            self.oda_bilgi(kisi.ad, f"'{ad}' tabloya yerleştirildi. Yansımayı sağdaki ayarlarla düzenleyebilirsiniz.")
            self.oda_degisti()

    def eser_hazirla(self, ad, veri):
        """Yüklenen tablo resmini diske yazar, küçük hallerini üretir (iş parçacığında)."""
        resim = resim_coz(veri)
        if resim is None:
            return None
        anahtar = uuid.uuid4().hex
        dosya = f"{anahtar}{uzanti(ad)}"
        with open(os.path.join(self.tablolar, dosya), "wb") as f:
            f.write(veri)
        for ek, boyut in (("k", (64, 64)), ("o", (320, 240))):
            h, w = resim.shape[:2]
            o = min(boyut[0] / w, boyut[1] / h, 1.0)
            kucuk = cv2.resize(resim, (max(1, int(w * o)), max(1, int(h * o))), interpolation=cv2.INTER_AREA)
            with open(os.path.join(self.kucuk, f"{anahtar}_{ek}.jpg"), "wb") as f:
                f.write(jpeg(kucuk, 85))
        return {"ad": ad, "dosya": dosya, "kucuk": anahtar, "tip": ck.resim_tipi_tahmin(resim),
                "ozet": hashlib.sha1(veri).hexdigest()}

    async def eserler_http(self, dosyalar):
        mevcut = {e["ozet"] for e in self.tablo["eserler"]}
        yeni, okunamayan = [], []
        for ad, veri in dosyalar:
            ozet = hashlib.sha1(veri).hexdigest()
            if ozet in mevcut:
                continue   # aynı resim iki kez eklenmesin
            e = await asyncio.to_thread(self.eser_hazirla, ad, veri)
            if e is None:
                okunamayan.append(ad)
                continue
            mevcut.add(ozet)
            yeni.append(e)
        async with self.kilit:
            for e in yeni:
                self.tablo["sayac"] += 1
                e["id"] = self.tablo["sayac"]
                self.tablo["eserler"].append(e)
            if yeni:
                self.tablo_bilgi(self.kullanici["ad"], f"{len(yeni)} tablo resmi yüklendi (toplam {len(self.tablo['eserler'])}).")
                self.tablo_degisti("eserler", "secim")
        return {"eklenen": len(yeni), "okunamayan": okunamayan}


# ----------------------------------------------------------------------
# ÇALIŞMA ALANLARI: her kullanıcı için bir tane, ilk girişte açılır
# ----------------------------------------------------------------------
calismalar = {}


async def calisma_al(kullanici):
    s = calismalar.get(kullanici["id"])
    if s is None:
        s = calismalar[kullanici["id"]] = Studyo(kullanici)
        s.hazir = asyncio.create_task(s.baslat())
    await s.hazir
    return s


def eski_veriyi_tasi(kullanici):
    """Hesaplardan önceki tek kullanıcılı sürümün verisi (veri/odalar, veri/tablolar, veri/durum.json) ilk açılan
    hesaba taşınır; böylece mevcut odalar kaybolmaz."""
    hedef = os.path.join(KULLANICI_VERI, str(kullanici["id"]))
    os.makedirs(hedef, exist_ok=True)
    tasinan = []
    for ad in ("odalar", "tablolar", "gecici", "durum.json"):
        kaynak = os.path.join(VERI, ad)
        if os.path.exists(kaynak) and not os.path.exists(os.path.join(hedef, ad)):
            shutil.move(kaynak, os.path.join(hedef, ad))
            tasinan.append(ad)
    shutil.rmtree(os.path.join(VERI, "indirmeler"), ignore_errors=True)
    return tasinan


# ----------------------------------------------------------------------
# WEB UYGULAMASI
# ----------------------------------------------------------------------
def kopan_baglantiyi_yoksay(loop):
    """Windows'ta (Proactor döngüsü) tarayıcı bağlantıyı aniden kapatınca (sayfa yenileme, sekme kapatma, canlı yenileme
    yoklaması) asyncio, zaten kopmuş soketi kapatmaya çalışırken ConnectionResetError (WinError 10054) yazdırır.
    Zararsızdır; sadece bu durum susturulur, diğer hatalar eskisi gibi yazılır."""
    onceki = loop.get_exception_handler()

    def isleyici(loop, baglam):
        if isinstance(baglam.get("exception"), ConnectionResetError) and \
                "_call_connection_lost" in repr(baglam.get("handle", "")):
            return
        if onceki:
            onceki(loop, baglam)
        else:
            loop.default_exception_handler(baglam)
    loop.set_exception_handler(isleyici)


@asynccontextmanager
async def omur(_app):
    kopan_baglantiyi_yoksay(asyncio.get_running_loop())
    zaman_asimi = asyncio.create_task(destek_zaman_asimi_dongusu())
    yield
    zaman_asimi.cancel()
    for s in calismalar.values():
        s.durumu_kaydet()


app = FastAPI(title="Tablo Stüdyosu", lifespan=omur, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(KOK, "static")), name="static")

@app.exception_handler(Exception)
async def beklenmeyen_hata(request: Request, e: Exception):
    """Beklenmeyen hata: ayrıntısı sunucu penceresine yazılır, panelde boş "500" yerine kısa açıklama görünür."""
    traceback.print_exception(e)
    return JSONResponse({"hata": f"Sunucu hatası: {type(e).__name__}: {e}"}, status_code=500)


# Yollar üç gruba ayrılır: mağaza herkese açık; hesap sayfaları (siparişler, ödeme) her üyeye; panel, stüdyo ve
# ürün / sipariş yönetimi sadece satıcıya.
# /odeme/3d-donus: bankanın 3D Secure sayfasından dönüş (başka siteden POST: oturum çerezi gelmez, sipariş gizli
# anahtarla bulunur ve ödeme iyzico'ya sorularak doğrulanır)
ACIK_YOLLAR = {"/", "/giris", "/kayit", "/odeme/3d-donus"}
ACIK_ONEKLER = ("/static/", "/urun/", "/api/magaza/", "/magaza/")
UYE_YOLLARI = {"/hesabim", "/odeme", "/cikis", "/api/sifre"}
UYE_ONEKLERI = ("/api/hesap/",)


def acik_yol(yol):
    return yol in ACIK_YOLLAR or yol.startswith(ACIK_ONEKLER)


def uye_yolu(yol):
    return yol in UYE_YOLLARI or yol.startswith(UYE_ONEKLERI)


def guvenli_sonra(sonra):
    """Girişten sonra dönülecek adres: sadece bu sitedeki bir yol (başka siteye yönlendirme yapılamasın)."""
    sonra = str(sonra or "")
    return sonra if sonra.startswith("/") and not sonra.startswith("//") and "\\" not in sonra else ""


@app.middleware("http")
async def giris_kontrol(request: Request, call_next):
    """Mağaza dışındaki her istek oturum ister (kullanıcı request.state.kullanici'ye yazılır); panel ve stüdyo
    sadece satıcıya açıktır."""
    yol = request.url.path
    if acik_yol(yol):
        return await call_next(request)
    kullanici = await asyncio.to_thread(hesaplar.oturum_kullanici, request.cookies.get(OTURUM_CEREZI))
    if kullanici is None:
        if yol.startswith("/api/"):
            return JSONResponse({"hata": "Oturum kapalı, lütfen tekrar giriş yapın."}, status_code=401)
        return RedirectResponse(f"/giris?sonra={quote(yol)}", status_code=303)
    if kullanici["rol"] != "satici" and not uye_yolu(yol):
        if yol.startswith("/api/"):
            return JSONResponse({"hata": "Bu işlem için satıcı hesabı gerekir."}, status_code=403)
        return RedirectResponse("/hesabim", status_code=303)
    request.state.kullanici = kullanici
    return await call_next(request)


# ---------------- giriş / kayıt sayfaları ----------------
SAYFA_KALIBI = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{baslik} — Tablo Stüdyo</title>
<link rel="icon" type="image/svg+xml" href="/static/panel/logo.svg">
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&display=swap" rel="stylesheet">
<script src="/static/tema.js"></script>
<style>
:root{{--zemin:#f6f3ee;--yuzey:#fff;--kenar:#e3ddd4;--yazi:#222;--ters:#fff;--soluk:#595959;--vurgu:#c8552b;--kirmizi:#b3261e;
--yesil:#2f7a3d;color-scheme:light}}
html[data-tema=koyu]{{--zemin:#121211;--yuzey:#1c1c1a;--kenar:#34312d;--yazi:#f2f0ec;--ters:#161615;--soluk:#b3aea6;
--vurgu:#e2703f;--kirmizi:#ff8a80;--yesil:#6cc37c;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;background:var(--zemin);
color:var(--yazi);font:15px/1.5 -apple-system,"Segoe UI",Roboto,Arial,sans-serif;padding:28px 16px}}
.logo{{font-family:"DM Serif Display",Georgia,serif;font-size:30px;color:var(--vurgu);text-decoration:none;margin-bottom:22px}}
.logo span{{color:var(--yazi)}}
form{{background:var(--yuzey);border:1px solid var(--kenar);padding:30px;border-radius:18px;width:min(420px,100%);
box-shadow:0 10px 30px rgba(34,34,34,.08)}}
h1{{font-size:24px;margin:0 0 4px;font-weight:700}} .alt{{color:var(--soluk);margin:0 0 20px;font-size:14px}}
label{{display:block;font-size:14px;font-weight:700;margin:14px 0 6px}}
input{{width:100%;height:46px;padding:0 14px;border-radius:10px;border:1px solid var(--kenar);background:var(--yuzey);color:inherit;font-size:15px}}
input:focus{{outline:none;border-color:var(--vurgu);box-shadow:0 0 0 4px rgba(200,85,43,.15)}}
button{{margin-top:22px;width:100%;height:48px;border:0;border-radius:99px;background:var(--yazi);color:var(--ters);font-size:15px;
font-weight:700;cursor:pointer}} button:hover{{opacity:.88}}
.hata{{background:rgba(179,38,30,.1);border:1px solid rgba(179,38,30,.3);color:var(--kirmizi);padding:10px 14px;border-radius:10px;font-size:14px;margin:0 0 6px}}
.bilgi{{background:rgba(47,122,61,.1);border:1px solid rgba(47,122,61,.3);color:var(--yesil);padding:10px 14px;border-radius:10px;font-size:14px;margin:0 0 6px}}
.ipucu{{font-size:12.5px;color:var(--soluk);margin-top:5px}}
.gecis{{text-align:center;margin:18px 0 0;font-size:14px;color:var(--soluk)}} a{{color:var(--yazi);font-weight:700}}
.ayrac{{border-top:1px solid var(--kenar);margin-top:22px}}
</style></head><body><a class="logo" href="/">Tablo<span>Stüdyo</span></a>{govde}
<script src="/static/canli.js"></script></body></html>"""


def sayfa(baslik, govde, durum=200):
    return HTMLResponse(SAYFA_KALIBI.format(baslik=baslik, govde=govde), status_code=durum)


def _gizli_sonra(sonra):
    sonra = guvenli_sonra(sonra)
    return f'<input type="hidden" name="sonra" value="{escape(sonra)}">' if sonra else ""


def _sonrali(yol, sonra):
    sonra = guvenli_sonra(sonra)
    return f"{yol}?sonra={quote(sonra)}" if sonra else yol


def giris_formu(hata="", kullanici_adi="", bilgi="", sonra=""):
    odeme = guvenli_sonra(sonra) == "/odeme"
    return sayfa("Giriş Yap", f"""<form method="post" action="/giris">
<h1>Giriş yap</h1><p class="alt">{"Siparişinizi tamamlamak için giriş yapın." if odeme else "Siparişlerinizi takip etmek için hesabınıza girin."}</p>
{f'<p class="hata">{escape(hata)}</p>' if hata else ''}{f'<p class="bilgi">{escape(bilgi)}</p>' if bilgi else ''}
<label for="k">Kullanıcı adı veya e-posta</label>
<input id="k" name="kullanici_adi" value="{escape(kullanici_adi)}" autocomplete="username" required autofocus>
<label for="s">Şifre</label>
<input id="s" type="password" name="sifre" autocomplete="current-password" required>
{_gizli_sonra(sonra)}
<button>Giriş Yap</button>
<div class="ayrac"></div>
<p class="gecis">Üye değil misiniz? <a href="{escape(_sonrali('/kayit', sonra))}">Hemen üye olun</a></p>
<p class="gecis"><a href="/">← Mağazaya dön</a></p></form>""", 401 if hata else 200)


def ilk_hesap_mi():
    return hesaplar.kullanici_sayisi() == 0


def kayit_formu(hata="", ad="", kullanici_adi="", sonra="", eposta=""):
    if ilk_hesap_mi():
        # Hiç hesap yok: ilk hesap mağazanın satıcısı (panelin sahibi) olur, kullanıcı adıyla
        eski_veri_var = os.path.isdir(os.path.join(VERI, "odalar"))
        govde = f"""<form method="post" action="/kayit">
<h1>Satıcı hesabı oluşturun</h1><p class="alt">İlk hesap mağazanın satıcı hesabıdır; diğer satıcıları panelden eklersiniz.</p>
{f'<p class="hata">{escape(hata)}</p>' if hata else ''}
{'<p class="bilgi">Daha önce kaydedilmiş odalar bu hesaba aktarılır.</p>' if eski_veri_var else ''}
<label for="a">Ad soyad</label>
<input id="a" name="ad" value="{escape(ad)}" autocomplete="name" maxlength="50" required autofocus>
<label for="k">Kullanıcı adı</label>
<input id="k" name="kullanici_adi" value="{escape(kullanici_adi)}" autocomplete="username" pattern="[A-Za-z0-9._\\-]{{3,30}}" maxlength="30" required>
<p class="ipucu">3-30 karakter: harf (Türkçe karakter olmadan), rakam, nokta, tire, alt çizgi.</p>"""
    else:
        govde = f"""<form method="post" action="/kayit">
<h1>Üye olun</h1><p class="alt">Sipariş verin, siparişlerinizi adım adım takip edin.</p>
{f'<p class="hata">{escape(hata)}</p>' if hata else ''}
<label for="a">Ad soyad</label>
<input id="a" name="ad" value="{escape(ad)}" autocomplete="name" maxlength="50" required autofocus>
<label for="k">Kullanıcı adı</label>
<input id="k" name="kullanici_adi" value="{escape(kullanici_adi)}" autocomplete="username" pattern="[A-Za-z0-9._\\-]{{3,30}}" maxlength="30" required>
<p class="ipucu">3-30 karakter: harf (Türkçe karakter olmadan), rakam, nokta, tire, alt çizgi.</p>
<label for="e">E-posta adresi</label>
<input id="e" type="email" name="eposta" value="{escape(eposta)}" autocomplete="email" maxlength="100" required>
<p class="ipucu">Kullanıcı adınız ya da e-postanızla giriş yapabilirsiniz.</p>"""
    return sayfa("Üye Ol", f"""{govde}
<label for="s">Şifre</label>
<input id="s" type="password" name="sifre" autocomplete="new-password" minlength="6" required>
<p class="ipucu">En az 6 karakter.</p>
<label for="s2">Şifre (tekrar)</label>
<input id="s2" type="password" name="sifre2" autocomplete="new-password" minlength="6" required>
{_gizli_sonra(sonra)}
<button>{"Hesap Oluştur" if ilk_hesap_mi() else "Üye Ol"}</button>
<div class="ayrac"></div>
<p class="gecis">Zaten üye misiniz? <a href="{escape(_sonrali('/giris', sonra))}">Giriş yapın</a></p>
<p class="gecis"><a href="/">← Mağazaya dön</a></p></form>""", 400 if hata else 200)


def yonlendirme_adresi(kullanici, sonra):
    """Girişten sonra: istenen sayfa (rolüne açıksa), yoksa satıcı panele, müşteri mağazaya."""
    sonra = guvenli_sonra(sonra)
    if sonra and (kullanici["rol"] == "satici" or acik_yol(sonra.split("?")[0]) or uye_yolu(sonra.split("?")[0])):
        return sonra
    return "/panel" if kullanici["rol"] == "satici" else "/"


def oturumla_yonlendir(request, kullanici, sonra=""):
    yanit = RedirectResponse(yonlendirme_adresi(kullanici, sonra), status_code=303)
    guvenli = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    yanit.set_cookie(OTURUM_CEREZI, hesaplar.oturum_ac(kullanici["id"]), max_age=30 * 24 * 3600,
                     httponly=True, samesite="lax", secure=guvenli)
    return yanit


# Kaba kuvvet denemelerine karşı: aynı adres + kullanıcı adıyla 15 dakikada en fazla 10 hatalı giriş
_hatali_girisler = {}


def giris_engelli(anahtar):
    simdi = time.time()
    liste = [t for t in _hatali_girisler.get(anahtar, []) if simdi - t < 900]
    _hatali_girisler[anahtar] = liste
    return len(liste) >= 10


@app.get("/giris")
async def giris_sayfasi(request: Request, sonra: str = ""):
    k = await asyncio.to_thread(hesaplar.oturum_kullanici, request.cookies.get(OTURUM_CEREZI))
    if k:
        return RedirectResponse(yonlendirme_adresi(k, sonra), status_code=303)
    if ilk_hesap_mi():
        return RedirectResponse("/kayit", status_code=303)
    return giris_formu(sonra=sonra)


@app.post("/giris")
async def giris(request: Request, kullanici_adi: str = Form(""), sifre: str = Form(""), sonra: str = Form("")):
    anahtar = (request.client.host if request.client else "", kullanici_adi.strip().lower())
    if giris_engelli(anahtar):
        return giris_formu("Çok fazla hatalı deneme yapıldı. 15 dakika sonra tekrar deneyin.", kullanici_adi, sonra=sonra)
    kullanici = await asyncio.to_thread(hesaplar.giris, kullanici_adi, sifre)
    if kullanici is None:
        _hatali_girisler.setdefault(anahtar, []).append(time.time())
        return giris_formu("Kullanıcı adı / e-posta ya da şifre yanlış.", kullanici_adi, sonra=sonra)
    _hatali_girisler.pop(anahtar, None)
    return oturumla_yonlendir(request, kullanici, sonra)


@app.get("/kayit")
async def kayit_sayfasi(request: Request, sonra: str = ""):
    k = await asyncio.to_thread(hesaplar.oturum_kullanici, request.cookies.get(OTURUM_CEREZI))
    if k:
        return RedirectResponse(yonlendirme_adresi(k, sonra), status_code=303)
    return kayit_formu(sonra=sonra)


@app.post("/kayit")
async def kayit(request: Request, ad: str = Form(""), kullanici_adi: str = Form(""), sifre: str = Form(""),
                sifre2: str = Form(""), sonra: str = Form(""), eposta: str = Form("")):
    """Mağazadan üyelik müşteri hesabı açar. Hiç hesap yokken açılan ilk hesap satıcıdır (panelin sahibi);
    sonraki satıcıları satıcılar panelden ekler."""
    if sifre != sifre2:
        return kayit_formu("Şifreler birbirini tutmuyor.", ad, kullanici_adi, sonra, eposta)
    ilk_hesap = ilk_hesap_mi()
    try:
        if ilk_hesap:
            kullanici = await asyncio.to_thread(hesaplar.kayit, kullanici_adi, ad, sifre, "satici")
        else:
            kullanici = await asyncio.to_thread(hesaplar.musteri_kayit, kullanici_adi, eposta, ad, sifre)
    except HesapHatasi as e:
        return kayit_formu(str(e), ad, kullanici_adi, sonra, eposta)
    if ilk_hesap:
        await asyncio.to_thread(eski_veriyi_tasi, kullanici)
    return oturumla_yonlendir(request, kullanici, sonra)


@app.post("/cikis")
async def cikis(request: Request):
    anahtar = request.cookies.get(OTURUM_CEREZI)
    await asyncio.to_thread(hesaplar.oturum_kapat, anahtar)
    # Bu oturumla açık sekmeler de kapansın
    s = calismalar.get(request.state.kullanici["id"])
    if s is not None:
        for ist in list(s.istemciler.values()):
            if getattr(ist, "oturum", None) == anahtar:
                await ist.ws.close(code=4401)
    # Satıcı panelden çıkınca giriş sayfasına, müşteri mağazaya döner
    yanit = RedirectResponse("/giris" if request.state.kullanici["rol"] == "satici" else "/", status_code=303)
    yanit.delete_cookie(OTURUM_CEREZI)
    return yanit


@app.post("/api/sifre")
async def sifre_degistir(request: Request):
    veri = await request.json()
    k = request.state.kullanici
    try:
        await asyncio.to_thread(hesaplar.sifre_degistir, k["id"], str(veri.get("eski", "")), str(veri.get("yeni", "")))
    except HesapHatasi as e:
        return JSONResponse({"hata": str(e)}, status_code=400)
    await asyncio.to_thread(hesaplar.diger_oturumlari_kapat, k["id"], request.cookies.get(OTURUM_CEREZI))
    return {"tamam": True}


# ---------------- sayfaların kendiliğinden yenilenmesi ----------------
# Açık sayfalar (static/canli.js) birkaç saniyede bir sürüme bakar: static/ altındaki bir dosya kaydedilince ya da
# sunucu .py değişikliğiyle yeniden başlayınca sürüm değişir ve sayfa kendini yeniler. STUDYO_OTOMATIK=0 kapatır.
CANLI_YENILEME = os.environ.get("STUDYO_OTOMATIK", "1") != "0"
SUNUCU_BASLANGICI = uuid.uuid4().hex[:8]   # sunucu her yeniden başladığında değişir
_dosya_surumu = {"zaman": 0.0, "deger": ""}


def static_surumu():
    """static/ altındaki en son değişen dosyanın zamanı (1 saniye bellekte tutulur)."""
    simdi = time.time()
    if simdi - _dosya_surumu["zaman"] > 1:
        en_son = 0.0
        for kok, _, dosyalar in os.walk(os.path.join(KOK, "static")):
            for ad in dosyalar:
                try:
                    en_son = max(en_son, os.path.getmtime(os.path.join(kok, ad)))
                except OSError:
                    pass
        _dosya_surumu.update(zaman=simdi, deger=str(int(en_son * 1000)))
    return _dosya_surumu["deger"]


@app.get("/api/magaza/canli-surum")
async def canli_surum():
    if not CANLI_YENILEME:
        return {"surum": None}
    return {"surum": f"{SUNUCU_BASLANGICI}-{await asyncio.to_thread(static_surumu)}"}


def html_sayfasi(dosya):
    """static/ altındaki sayfa. Kendi js / css dosyalarımız değişince tarayıcı eskisini önbellekten kullanmasın diye
    adreslerine değişiklik zamanı eklenir (şablon dosyaları değişmez, onlara eklenmez)."""
    with open(os.path.join(KOK, "static", dosya), encoding="utf-8") as f:
        metin = f.read()

    def surumlu(m):
        yol = os.path.join(KOK, m.group(1).lstrip("/").replace("/", os.sep))
        return f'"{m.group(1)}?v={int(os.path.getmtime(yol))}"' if os.path.isfile(yol) else m.group(0)
    metin = re.sub(r'"(/static/(?:(?:panel|magaza)/)?[^"?]+?\.(?:js|css))"', surumlu, metin)
    return HTMLResponse(metin, headers={"Cache-Control": "no-cache"})


@app.get("/")
async def magaza_sayfasi():
    return html_sayfasi("magaza/index.html")


@app.get("/urun/{yol}")
async def urun_sayfasi(yol: str):
    # Adres: /urun/12-kahve-fincani-cam-tablo (sayfa ürünü id'sinden AJAX ile yükler)
    return html_sayfasi("magaza/urun.html")


@app.get("/panel")
async def panel_sayfasi():
    return html_sayfasi("panel/index.html")


@app.get("/hesabim")
async def hesap_sayfasi():
    return html_sayfasi("magaza/hesap.html")


@app.get("/odeme")
async def odeme_sayfasi():
    return html_sayfasi("magaza/odeme.html")


def ayni_kaynak(ws):
    """Başka bir siteden açılan WebSocket bağlantılarını reddet (çerez ile kimliğe bürünmeyi engeller)."""
    kaynak = ws.headers.get("origin")
    if not kaynak:
        return True
    izinli = {ws.headers.get("host"), ws.headers.get("x-forwarded-host")}
    return urlparse(kaynak).netloc in izinli


@app.websocket("/ws")
async def ws_baglanti(ws: WebSocket):
    anahtar = ws.cookies.get(OTURUM_CEREZI)
    kullanici = await asyncio.to_thread(hesaplar.oturum_kullanici, anahtar)
    if kullanici is None or kullanici["rol"] != "satici" or not ayni_kaynak(ws):
        # Önce kabul edip 4401 ile kapat: tarayıcı kodu görüp giriş sayfasına geçsin (reddedilen el sıkışmada
        # kod görünmez, sayfa boşuna yeniden bağlanmaya çalışırdı)
        await ws.accept()
        await ws.close(code=4401)
        return
    s = await calisma_al(kullanici)
    await ws.accept()
    ist = Istemci(ws, kullanici["ad"])
    ist.oturum = anahtar
    s.istemciler[ist.id] = ist
    ist.gonder(json_metni(s.hos_geldin(ist)))
    s.cihazlari_yayinla()
    try:
        while True:
            m = await ws.receive_json()
            if m.get("tip") == "aksiyon":
                await s.aksiyon(ist, m)
            elif m.get("tip") == "imlec":
                # Aynı hesabın diğer cihazında fare imleci (oda resmi koordinatlarında)
                metin = json_metni({"tip": "imlec", "id": ist.id, "ad": ist.ad, "x": m.get("x"), "y": m.get("y"),
                                    "gorunum": m.get("gorunum")})
                for diger in list(s.istemciler.values()):
                    if diger.id != ist.id:
                        diger.gonder(metin)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        s.istemciler.pop(ist.id, None)
        ist.gorev.cancel()
        s.cihazlari_yayinla()
        s.yayinla({"tip": "imlec", "id": ist.id, "x": None})


def hata_yaniti(e):
    return JSONResponse({"hata": str(e)}, status_code=400)


@app.post("/api/oda/yukle")
async def oda_yukle(request: Request, dosya: UploadFile = File(...)):
    s = await calisma_al(request.state.kullanici)
    try:
        await s.oda_yukle_http(dosya.filename or "oda.jpg", await dosya.read())
    except Hata as e:
        return hata_yaniti(e)
    return {"tamam": True}


@app.post("/api/oda/tablo-resmi")
async def tablo_resmi(request: Request, dosya: UploadFile = File(...)):
    s = await calisma_al(request.state.kullanici)
    try:
        await s.tablo_resmi_http(dosya.filename or "tablo.jpg", await dosya.read())
    except Hata as e:
        return hata_yaniti(e)
    return {"tamam": True}


@app.post("/api/eserler")
async def eserler(request: Request, dosyalar: list[UploadFile] = File(...)):
    s = await calisma_al(request.state.kullanici)
    okunan = [(d.filename or "resim.jpg", await d.read()) for d in dosyalar]
    return await s.eserler_http(okunan)


@app.get("/api/goruntu/{gorunum}")
async def goruntu(request: Request, gorunum: str):
    s = await calisma_al(request.state.kullanici)
    cizici = {"oda": s.oda_cizici, "tablo": s.tablo_cizici}.get(gorunum)
    if cizici is None or cizici.resim is None:
        return Response(status_code=404)
    return Response(cizici.resim, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/eser/{eser_id}/{boyut}.jpg")
async def eser_kucuk(request: Request, eser_id: int, boyut: str):
    s = await calisma_al(request.state.kullanici)
    e = s.eser(eser_id)
    if e is None or boyut not in ("k", "o"):
        return Response(status_code=404)
    return FileResponse(os.path.join(s.kucuk, f"{e['kucuk']}_{boyut}.jpg"), headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/oda/sonuc")
async def oda_sonuc(request: Request):
    """1. sekmedeki görüntünün tam boy, tam kalite hali."""
    s = await calisma_al(request.state.kullanici)
    anlik = s.oda_anlik()
    if anlik["resim"] is None:
        return Response(status_code=404)
    resim = await asyncio.to_thread(s.oda_ciz, anlik, True, True)
    veri = await asyncio.to_thread(jpeg, resim, 95)
    return Response(veri, media_type="image/jpeg", headers=indirme_basligi(f"{s.oda['ad'] or 'oda'}_sunum.jpg"))


@app.get("/api/tablo/indir")
async def tablo_indir(request: Request, oda: int, eser: int):
    """2. sekmedeki önizlemenin tam boy, tam kalite hali."""
    s = await calisma_al(request.state.kullanici)
    o = next((x for x in s.odalar if x["id"] == oda), None)
    e = s.eser(eser)
    if o is None or e is None:
        return Response(status_code=404)
    try:
        resim = await asyncio.to_thread(lambda: s.oda_sonucu(o, s.eser_yukle(e), s.tablo["yansima_acik"]))
    except Exception as hata:
        return hata_yaniti(hata)
    veri = await asyncio.to_thread(jpeg, resim, 95)
    return Response(veri, media_type="image/jpeg",
                    headers=indirme_basligi(f"{oda_klasor_adi(o)}_{os.path.splitext(e['ad'])[0]}.jpg"))


@app.get("/api/indirme/{ad}")
async def indirme(request: Request, ad: str):
    s = await calisma_al(request.state.kullanici)
    yol = os.path.join(s.indirmeler, os.path.basename(ad))
    if not os.path.isfile(yol):
        return Response("Bu indirme artık yok; tekrar hazırlayın.", status_code=404)
    tarih = time.strftime("%Y-%m-%d_%H-%M")
    return FileResponse(yol, media_type="application/zip", headers=indirme_basligi(f"Tablo Sunumları {tarih}.zip"))


# ----------------------------------------------------------------------
# MAĞAZA: ürünlerin odalardaki görselleri, herkese açık ürün listesi ve paneldeki ürün işlemleri
# ----------------------------------------------------------------------
cizim_siniri = asyncio.Semaphore(2)    # aynı anda en fazla iki görsel çizilsin (sunucu tıkanmasın)
oda_kucukleri = Bellek(64)             # paneldeki oda küçük resimleri


def oda_klasoru(kullanici_id):
    return os.path.join(KULLANICI_VERI, str(kullanici_id), "odalar")


def kullanici_odalari(kullanici_id):
    return dukkan.odalari_getir(oda_klasoru(kullanici_id))


def gorsel_adresi(urun, oda, boyut):
    if oda is None:
        return f"/magaza/gorsel/{urun['id']}/resim-{boyut}.jpg?v={dukkan.surum(urun)}"
    return f"/magaza/gorsel/{urun['id']}/{oda['id']}-{boyut}.jpg?v={dukkan.surum(urun, oda)}"


def urun_ozeti(u, odalar):
    """Vitrin kartı: kapak (ilk oda) ve fareyle üstüne gelince görünen ikinci görsel."""
    sunum = dukkan.urun_odalari(u, odalar)
    kapak = gorsel_adresi(u, sunum[0] if sunum else None, "o")
    ikinci = gorsel_adresi(u, sunum[1], "o") if len(sunum) > 1 else gorsel_adresi(u, None, "o")
    puan, puan_sayi = puan_ozetleri().get(u["id"], (0, 0))
    return {"id": u["id"], "ad": u["ad"], "fiyat": u["fiyat"], "eski_fiyat": u["eski_fiyat"], "tip": u["tip"],
            "puan": puan, "puan_sayi": puan_sayi,
            "tip_adi": ck.TABLO_TIPLERI[u["tip"]], "yayinda": u["yayinda"], "kapak": kapak, "ikinci": ikinci,
            "oda_sayisi": len(sunum), "adres": f"/urun/{u['id']}-{u['kisa_ad']}",
            "olusturma": u["olusturma"], "guncelleme": u["guncelleme"]}


def urun_detayi(u, odalar):
    sunum = dukkan.urun_odalari(u, odalar)
    gorseller = [{"oda_id": o["id"], "oda": o["ad"], "k": gorsel_adresi(u, o, "k"), "o": gorsel_adresi(u, o, "o"),
                  "b": gorsel_adresi(u, o, "b")} for o in sunum]
    gorseller.append({"oda_id": None, "oda": "Tablonun kendisi", "k": gorsel_adresi(u, None, "k"),
                      "o": gorsel_adresi(u, None, "o"), "b": gorsel_adresi(u, None, "b")})
    return {**urun_ozeti(u, odalar), "aciklama": u["aciklama"], "gorseller": gorseller,
            "odalar": u["odalar"], "kapak_oda": u["kapak_oda"], "genislik": u["genislik"], "yukseklik": u["yukseklik"]}


async def istekteki_kullanici(request):
    """Açık sayfalarda da (mağaza) giriş yapmış kişi: yayında olmayan ürünlerini önizleyebilsin."""
    k = getattr(request.state, "kullanici", None)
    if k is None:
        k = await asyncio.to_thread(hesaplar.oturum_kullanici, request.cookies.get(OTURUM_CEREZI))
    return k


async def gorunur_urun(request, urun_id, sahibe_acik=False):
    """Mağazada yayında olmayan ürün kimseye görünmez. sahibe_acik=True: sadece görselleri, panelde önizleme için
    ürünün sahibine açık."""
    u = await asyncio.to_thread(dukkan.urun, urun_id)
    if u is None:
        return None
    if not u["yayinda"]:
        if not sahibe_acik:
            return None
        k = await istekteki_kullanici(request)
        if k is None or k["id"] != u["kullanici_id"]:
            return None
    return u


def isitmayi_planla(kullanici_id, urun_id=None):
    """Ürün ya da oda kaydedilince vitrin görselleri arka planda hazırlansın (ilk ziyaretçi beklemesin)."""
    try:
        asyncio.get_running_loop().create_task(gorselleri_isit(kullanici_id, urun_id))
    except RuntimeError:
        pass


async def gorselleri_isit(kullanici_id, urun_id=None):
    await asyncio.sleep(0.5)
    try:
        odalar = await asyncio.to_thread(kullanici_odalari, kullanici_id)
        urunler = await asyncio.to_thread(dukkan.urunler, kullanici_id)
        for u in urunler:
            if urun_id is not None and u["id"] != urun_id:
                continue
            for o in dukkan.urun_odalari(u, odalar):
                for boyut in ("o", "k"):
                    async with cizim_siniri:
                        await asyncio.to_thread(dukkan.sunum_gorseli, u, oda_klasoru(kullanici_id), o, boyut)
    except Exception:
        traceback.print_exc()


def gorsel_yaniti(veri, surum_dogru):
    onbellek = "public, max-age=31536000, immutable" if surum_dogru else "no-cache"
    return Response(veri, media_type="image/jpeg", headers={"Cache-Control": onbellek})


@app.get("/api/magaza/urunler")
async def magaza_urunleri(ara: str = "", tip: str = "", sirala: str = "yeni"):
    urunler = await asyncio.to_thread(dukkan.urunler, None, True, ara, tip, sirala)
    sonuc = []
    for u in urunler:
        odalar = await asyncio.to_thread(kullanici_odalari, u["kullanici_id"])
        sonuc.append(urun_ozeti(u, odalar))
    tipler = {t: sum(1 for u in sonuc if u["tip"] == t) for t in ck.TABLO_TIPLERI} if not tip else None
    return {"urunler": sonuc, "toplam": len(sonuc), "tipler": tipler, "tip_adlari": ck.TABLO_TIPLERI}


@app.get("/api/magaza/urun/{urun_id}")
async def magaza_urunu(request: Request, urun_id: int):
    u = await gorunur_urun(request, urun_id)
    if u is None:
        return JSONResponse({"hata": "Ürün bulunamadı."}, status_code=404)
    odalar = await asyncio.to_thread(kullanici_odalari, u["kullanici_id"])
    benzer = [x for x in await asyncio.to_thread(dukkan.urunler, None, True) if x["id"] != u["id"]]
    benzer.sort(key=lambda x: (x["tip"] != u["tip"], -x["id"]))
    benzerler = []
    for x in benzer[:8]:
        benzerler.append(urun_ozeti(x, await asyncio.to_thread(kullanici_odalari, x["kullanici_id"])))
    return {**urun_detayi(u, odalar), "benzerler": benzerler, "tip_adlari": ck.TABLO_TIPLERI}


@app.get("/magaza/gorsel/{urun_id}/{ad}.jpg")
async def magaza_gorseli(request: Request, urun_id: int, ad: str, v: str = ""):
    m = re.fullmatch(r"(\d+|resim)-([kob])", ad)
    u = await gorunur_urun(request, urun_id, sahibe_acik=True) if m else None
    if u is None:
        return Response(status_code=404)
    boyut = m.group(2)
    try:
        if m.group(1) == "resim":
            veri = await asyncio.to_thread(dukkan.urun_resmi, u, boyut)
            return gorsel_yaniti(veri, v == dukkan.surum(u))
        odalar = await asyncio.to_thread(kullanici_odalari, u["kullanici_id"])
        oda = next((o for o in odalar if o["id"] == int(m.group(1))), None)
        if oda is None:
            return Response(status_code=404)
        async with cizim_siniri:
            veri = await asyncio.to_thread(dukkan.sunum_gorseli, u, oda_klasoru(u["kullanici_id"]), oda, boyut)
        return gorsel_yaniti(veri, v == dukkan.surum(u, oda))
    except Exception:
        traceback.print_exc()
        return Response(status_code=500)


# ---------------- panel: ürün işlemleri (giriş gerekir) ----------------
def magaza_hatasi(e):
    return JSONResponse({"hata": str(e)}, status_code=400)


@app.get("/api/panel/ozet")
async def panel_ozeti(request: Request):
    k = request.state.kullanici
    odalar = await asyncio.to_thread(kullanici_odalari, k["id"])
    urunler = await asyncio.to_thread(dukkan.urunler, k["id"])
    return {
        "kullanici": {"ad": k["ad"], "kullanici_adi": k["kullanici_adi"]},
        "oda_sayisi": len(odalar),
        "oda_tipleri": {t: sum(1 for o in odalar if o["tip"] == t) for t in ck.TABLO_TIPLERI},
        "urun_sayisi": len(urunler),
        "yayinda": sum(1 for u in urunler if u["yayinda"]),
        "urun_tipleri": {t: sum(1 for u in urunler if u["tip"] == t) for t in ck.TABLO_TIPLERI},
        "son_urunler": [urun_ozeti(u, odalar) for u in urunler[:6]],
        "tip_adlari": ck.TABLO_TIPLERI,
        "siparis": await asyncio.to_thread(siparisler.sayilar),
        "musteri_sayisi": await asyncio.to_thread(hesaplar.kullanici_sayisi, "musteri"),
    }


@app.get("/api/panel/odalar")
async def panel_odalari(request: Request):
    odalar = await asyncio.to_thread(kullanici_odalari, request.state.kullanici["id"])
    return [{"id": o["id"], "ad": o["ad"], "tip": o["tip"], "guncelleme": o["guncelleme"],
             "kucuk": f"/api/panel/oda/{o['id']}/kucuk.jpg?v={quote(o['guncelleme'])}"} for o in odalar]


@app.get("/api/panel/oda/{oda_id}/kucuk.jpg")
async def panel_oda_kucugu(request: Request, oda_id: int):
    klasor = oda_klasoru(request.state.kullanici["id"])
    oda = next((o for o in await asyncio.to_thread(kullanici_odalari, request.state.kullanici["id"])
                if o["id"] == oda_id), None)
    if oda is None:
        return Response(status_code=404)

    def uret():
        resim = ck.turkce_karakter_destekli_imread(os.path.join(klasor, os.path.basename(oda["dosya"])))
        if resim is None:
            return None
        return jpeg(kucult(resim, 480), 82)
    veri = await asyncio.to_thread(oda_kucukleri.al, (klasor, oda_id, oda["guncelleme"]), uret)
    if veri is None:
        return Response(status_code=404)
    return Response(veri, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/urunler")
async def panel_urunleri(request: Request, ara: str = "", tip: str = "", sirala: str = "yeni"):
    k = request.state.kullanici
    odalar = await asyncio.to_thread(kullanici_odalari, k["id"])
    urunler = await asyncio.to_thread(dukkan.urunler, k["id"], None, ara, tip, sirala)
    return {"urunler": [urun_ozeti(u, odalar) for u in urunler], "toplam": len(urunler)}


@app.get("/api/urunler/{urun_id}")
async def panel_urunu(request: Request, urun_id: int):
    k = request.state.kullanici
    u = await asyncio.to_thread(dukkan.urun, urun_id)
    if u is None or u["kullanici_id"] != k["id"]:
        return JSONResponse({"hata": "Ürün bulunamadı."}, status_code=404)
    odalar = await asyncio.to_thread(kullanici_odalari, k["id"])
    return urun_detayi(u, odalar)


@app.post("/api/urun/gecici")
async def urun_gecici_resim(dosya: UploadFile = File(...)):
    """Ürün formunda seçilen resim: kaydetmeden odalarda önizlenebilsin diye geçici olarak saklanır."""
    try:
        return await asyncio.to_thread(dukkan.gecici_kaydet, await dosya.read())
    except MagazaHatasi as e:
        return magaza_hatasi(e)


@app.post("/api/urun/gecici-eser/{eser_id}")
async def urun_gecici_eser(request: Request, eser_id: int):
    """'Tablo Ekle'ye yüklenmiş resimden ürün oluşturma."""
    s = await calisma_al(request.state.kullanici)
    e = s.eser(eser_id)
    if e is None:
        return JSONResponse({"hata": "Resim bulunamadı."}, status_code=404)
    try:
        sonuc = await asyncio.to_thread(dukkan.eser_kopyala, os.path.join(s.tablolar, e["dosya"]))
    except (MagazaHatasi, OSError) as hata:
        return magaza_hatasi(hata)
    return {**sonuc, "ad": os.path.splitext(e["ad"])[0], "tip_tahmini": e["tip"]}


@app.get("/api/urun/onizleme/{anahtar}/{oda_id}.jpg")
async def urun_onizleme(request: Request, anahtar: str, oda_id: int):
    k = request.state.kullanici
    oda = next((o for o in await asyncio.to_thread(kullanici_odalari, k["id"]) if o["id"] == oda_id), None)
    if oda is None:
        return Response(status_code=404)
    try:
        async with cizim_siniri:
            veri = await asyncio.to_thread(dukkan.gecici_gorsel, anahtar, oda_klasoru(k["id"]), oda)
    except MagazaHatasi:
        return Response(status_code=404)
    return Response(veri, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/urunler")
async def urun_ekle(request: Request):
    k = request.state.kullanici
    try:
        urun_id = await asyncio.to_thread(dukkan.kaydet, k["id"], await request.json())
    except MagazaHatasi as e:
        return magaza_hatasi(e)
    isitmayi_planla(k["id"], urun_id)
    odalar = await asyncio.to_thread(kullanici_odalari, k["id"])
    return {"tamam": True, "urun": urun_detayi(await asyncio.to_thread(dukkan.urun, urun_id), odalar)}


@app.put("/api/urunler/{urun_id}")
async def urun_guncelle(request: Request, urun_id: int):
    k = request.state.kullanici
    try:
        await asyncio.to_thread(dukkan.kaydet, k["id"], await request.json(), urun_id)
    except MagazaHatasi as e:
        return magaza_hatasi(e)
    isitmayi_planla(k["id"], urun_id)
    odalar = await asyncio.to_thread(kullanici_odalari, k["id"])
    return {"tamam": True, "urun": urun_detayi(await asyncio.to_thread(dukkan.urun, urun_id), odalar)}


@app.patch("/api/urunler/{urun_id}/yayin")
async def urun_yayin(request: Request, urun_id: int):
    veri = await request.json()
    try:
        await asyncio.to_thread(dukkan.yayin_degistir, request.state.kullanici["id"], urun_id, bool(veri.get("yayinda")))
    except MagazaHatasi as e:
        return magaza_hatasi(e)
    return {"tamam": True}


@app.delete("/api/urunler/{urun_id}")
async def urun_sil(request: Request, urun_id: int):
    try:
        u = await asyncio.to_thread(dukkan.sil, request.state.kullanici["id"], urun_id)
    except MagazaHatasi as e:
        return magaza_hatasi(e)
    return {"tamam": True, "ad": u["ad"]}


# ----------------------------------------------------------------------
# ÜYELİK VE SİPARİŞLER
# ----------------------------------------------------------------------
def tl(n):
    return f"{n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def hesap_ozeti(k):
    return {"giris": True, "id": k["id"], "ad": k["ad"], "rol": k["rol"], "kullanici_adi": k["kullanici_adi"],
            "eposta": k["eposta"], "telefon": k["telefon"], "adres": k["adres"]}


def kullanici_hatasi(e):
    return JSONResponse({"hata": str(e)}, status_code=400)


@app.get("/api/magaza/ben")
async def oturumdaki_kisi(request: Request):
    """Mağazanın üst kısmı için: giriş yapılmış mı, kim (Giriş Yap / Hesabım)."""
    k = await istekteki_kullanici(request)
    return hesap_ozeti(k) if k else {"giris": False}


@app.put("/api/hesap/bilgiler")
async def hesap_bilgileri(request: Request):
    """Ad, telefon, kullanıcı adı ve e-posta (müşteri Hesabım'dan, satıcı panelden)."""
    veri = await request.json()
    try:
        k = await asyncio.to_thread(hesaplar.bilgileri_guncelle, request.state.kullanici["id"],
                                    ad=veri.get("ad"), telefon=veri.get("telefon"),
                                    kullanici_adi=veri.get("kullanici_adi"), eposta=veri.get("eposta"))
    except HesapHatasi as e:
        return kullanici_hatasi(e)
    if k["id"] in calismalar:   # açık paneldeki ad / kullanıcı adı da güncel kalsın
        calismalar[k["id"]].kullanici.update(ad=k["ad"], kullanici_adi=k["kullanici_adi"])
    return hesap_ozeti(k)


def siparis_durum_bilgisi():
    return {"durumlar": DURUMLAR, "akis": AKIS, "odeme_sekilleri": ODEME_SEKILLERI, "talep_turleri": TALEP_TURLERI,
            "talep_durumlari": TALEP_DURUMLARI, "talep_nedenleri": TALEP_NEDENLERI, "iade_suresi_gun": IADE_SURESI_GUN}


@app.get("/api/magaza/odeme")
async def odeme_secenekleri():
    """Ödeme sayfası için: kartla ödeme açık mı, test ortamı mı (anahtarlar gönderilmez)."""
    return await asyncio.to_thread(kart_odeme.durum)


async def sepet_ozetleri(urun_idler):
    """Ürün kimliklerinden vitrin özetleri (güncel ad, fiyat, görsel); yayında olmayanlar atlanır."""
    odalar_bellek, ozetler = {}, {}
    for uid in dict.fromkeys(urun_idler):
        u = await asyncio.to_thread(dukkan.urun, uid)
        if u is None or not u["yayinda"]:
            continue
        if u["kullanici_id"] not in odalar_bellek:
            odalar_bellek[u["kullanici_id"]] = await asyncio.to_thread(kullanici_odalari, u["kullanici_id"])
        ozetler[uid] = urun_ozeti(u, odalar_bellek[u["kullanici_id"]])
    return ozetler


def _kimlikler(liste, adetli):
    sonuc = []
    for x in (liste or [])[:100]:
        try:
            sonuc.append((int(x["id"]), int(x.get("adet", 1))) if adetli else int(x["id"] if isinstance(x, dict) else x))
        except (KeyError, TypeError, ValueError):
            continue
    return sonuc


@app.get("/api/hesap/sepet")
async def sepetim(request: Request):
    """Üyenin sepeti ve favorileri (hesaba bağlı; başka kullanıcı göremez)."""
    sepet, favori = await asyncio.to_thread(sepetler.oku, request.state.kullanici["id"])
    ozet = await sepet_ozetleri([uid for uid, _ in sepet] + favori)
    return {"sepet": [{**ozet[uid], "adet": adet} for uid, adet in sepet if uid in ozet],
            "favoriler": [ozet[uid] for uid in favori if uid in ozet]}


@app.put("/api/hesap/sepet")
async def sepeti_kaydet(request: Request):
    veri = await request.json()
    yazildi = await asyncio.to_thread(sepetler.sepet_yaz, request.state.kullanici["id"],
                                      _kimlikler(veri.get("sepet"), True), veri.get("surum"))
    return {"tamam": True, "yazildi": yazildi}


@app.put("/api/hesap/favoriler")
async def favorileri_kaydet(request: Request):
    veri = await request.json()
    yazildi = await asyncio.to_thread(sepetler.favori_yaz, request.state.kullanici["id"],
                                      [uid for uid in _kimlikler(veri.get("favoriler"), False)], veri.get("surum"))
    return {"tamam": True, "yazildi": yazildi}


def istemci_ip(request):
    ileri = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return ileri or (request.client.host if request.client else "127.0.0.1")


def saticilara_siparis_bildir(s, musteri_adi):
    """Yeni sipariş: sipariş edilen ürünler müşterinin sepetinden çıkar, panelde açık olan satıcılara anında
    bildirilir."""
    try:
        sepetler.sepetten_cikar(s["musteri_id"], [k["urun_id"] for k in s["kalemler"]])
    except Exception:
        traceback.print_exc()
    for st in list(calismalar.values()):
        st.yayinla({"tip": "bildirim", "tur": "success",
                    "metin": f"🛍️ Yeni sipariş {s['no']}: {musteri_adi} — {tl(s['toplam'])}"
                             + (f" ({s['odeme_kart']} ile ödendi)" if s.get("odeme_kart") else "")})
        st.yayinla({"tip": "siparis", "no": s["no"]})


async def kart_iadesi(s, kim):
    """İptal edilen kartlı siparişin ödemesini iade eder (aynı gün iptal, sonrasında tutar iadesi). Dönüş: geçmişe
    yazılacak not."""
    if s.get("odeme") != "kart" or not s.get("odeme_ref"):
        return ""
    try:
        await asyncio.to_thread(kart_odeme.karta_iade, s["odeme_ref"], s["toplam"])
        return f"Kart ödemesi ({tl(s['toplam'])}) karta iade edildi."
    except OdemeHatasi as e:
        for st in list(calismalar.values()):
            st.yayinla({"tip": "bildirim", "tur": "danger",
                        "metin": f"Sipariş {s['no']} iptal edildi ama kart ödemesi iade edilemedi ({e}) "
                                 "iadeyi iyzico panelinden yapın."})
        return f"Kart ödemesi otomatik iade edilemedi ({e}); iade iyzico panelinden yapılacak."


@app.get("/api/hesap/siparisler")
async def siparislerim(request: Request):
    liste = await asyncio.to_thread(siparisler.listele, request.state.kullanici["id"])
    return {"siparisler": liste, **siparis_durum_bilgisi()}


@app.post("/api/hesap/siparisler")
async def siparis_ver(request: Request):
    """Sepetten sipariş: fiyatlar sunucudaki ürün kaydından alınır, sadece mağazada yayında olan ürünler."""
    k = request.state.kullanici
    veri = await request.json()
    kalemler, alinamayan = [], []
    birlesik = {}
    for kalem in veri.get("kalemler") or []:
        try:
            uid, adet = int(kalem["id"]), int(kalem.get("adet", 1))
        except (KeyError, TypeError, ValueError):
            continue
        birlesik[uid] = min(EN_FAZLA_ADET, birlesik.get(uid, 0) + max(1, adet))
    for uid, adet in birlesik.items():
        u = await asyncio.to_thread(dukkan.urun, uid)
        if u is None or not u["yayinda"]:
            alinamayan.append(uid)
            continue
        odalar = await asyncio.to_thread(kullanici_odalari, u["kullanici_id"])
        sunum = dukkan.urun_odalari(u, odalar)
        kalemler.append((u, adet, gorsel_adresi(u, sunum[0] if sunum else None, "k")))
    if alinamayan:
        return JSONResponse({"hata": "Sepetteki bazı ürünler artık satışta değil; sepetiniz güncellendi, lütfen "
                                     "kontrol edip tekrar deneyin.", "alinamayan": alinamayan}, status_code=409)
    kartla = veri.get("odeme") == "kart"
    if kartla:
        # Kart, sipariş oluşmadan denetlenir; kart bilgisi hiçbir yere yazılmaz, sadece iyzico'ya iletilir
        if not kart_odeme.acik():
            return kullanici_hatasi("Kartla ödeme şu an kullanılamıyor; lütfen başka bir ödeme şekli seçin.")
        try:
            kart = kart_dogrula(veri.get("kart"))
        except OdemeHatasi as e:
            return kullanici_hatasi(e)
    try:
        s = await asyncio.to_thread(siparisler.olustur, k, kalemler, veri.get("teslimat"), veri.get("odeme"))
    except SiparisHatasi as e:
        return kullanici_hatasi(e)
    if veri.get("adresi_kaydet"):
        t = s["teslimat"]
        await asyncio.to_thread(hesaplar.bilgileri_guncelle, k["id"], telefon=t["telefon"],
                                adres={"ad": t["ad"], "il": t["il"], "ilce": t["ilce"], "adres": t["adres"]})
    if kartla:
        anahtar = await asyncio.to_thread(siparisler.odeme_anahtari, s["id"])
        try:
            if kart_odeme.uc_boyutlu():
                # 3D Secure: tarayıcı bankanın doğrulama sayfasına gider, oradan /odeme/3d-donus'a döner
                html = await asyncio.to_thread(kart_odeme.uc_boyut_baslat, s, k, kart, istemci_ip(request), anahtar,
                                               str(request.base_url) + "odeme/3d-donus")
                return {"tamam": True, "uc_boyut_html": html}
            sonuc = await asyncio.to_thread(kart_odeme.odeme_al, s, k, kart, istemci_ip(request), anahtar)
        except OdemeHatasi as e:
            await asyncio.to_thread(siparisler.odeme_basarisiz, s["id"])
            return kullanici_hatasi(e)
        s, _ = await asyncio.to_thread(siparisler.odeme_tamamlandi, s["id"], sonuc["odeme_id"], sonuc["kart"], k["ad"])
    saticilara_siparis_bildir(s, k["ad"])
    return {"tamam": True, "siparis": s}


@app.post("/odeme/3d-donus")
async def uc_boyut_donus(request: Request):
    """Bankanın 3D Secure sayfasından dönüş: ödeme iyzico'da tamamlanır, müşteri siparişine ya da (başarısızsa)
    ödeme sayfasına yönlendirilir. Sipariş, istekte gelen gizli anahtarla (conversationId) bulunur."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    s = await asyncio.to_thread(siparisler.anahtarla_bul, form.get("conversationId", ""))
    if s is None:
        return RedirectResponse("/odeme?hata=" + quote("Ödeme oturumu bulunamadı ya da süresi doldu; lütfen tekrar deneyin."),
                                status_code=303)
    if s["durum"] != "odeme":   # çift dönüş (sayfa yenileme): zaten işlenmiş
        return RedirectResponse(f"/hesabim#siparis/{s['id']}/yeni", status_code=303)
    try:
        sonuc = await asyncio.to_thread(kart_odeme.uc_boyut_tamamla, s, form)
    except OdemeHatasi as e:
        await asyncio.to_thread(siparisler.odeme_basarisiz, s["id"])
        return RedirectResponse("/odeme?hata=" + quote(str(e)), status_code=303)
    musteri = await asyncio.to_thread(hesaplar.kullanici, s["musteri_id"])
    ad = musteri["ad"] if musteri else s["teslimat"].get("ad", "")
    s, yeni = await asyncio.to_thread(siparisler.odeme_tamamlandi, s["id"], sonuc["odeme_id"], sonuc["kart"], ad)
    if yeni:
        saticilara_siparis_bildir(s, ad)
    return RedirectResponse(f"/hesabim#siparis/{s['id']}/yeni", status_code=303)


@app.post("/api/hesap/siparisler/{siparis_id}/iptal")
async def siparis_iptal(request: Request, siparis_id: int):
    """Hazırlanmaya başlamamış siparişi müşteri hemen iptal eder (kartla ödendiyse para iade edilir)."""
    try:
        veri = await request.json()
    except ValueError:
        veri = {}
    try:
        s = await asyncio.to_thread(siparisler.musteri_iptal, siparis_id, request.state.kullanici,
                                    veri.get("neden", ""), veri.get("aciklama", ""))
    except SiparisHatasi as e:
        return kullanici_hatasi(e)
    iade = await kart_iadesi(s, request.state.kullanici["ad"])
    if iade:
        s = await asyncio.to_thread(siparisler.durum_degistir, siparis_id, "iptal", "Sistem", iade)
    for st in list(calismalar.values()):
        st.yayinla({"tip": "bildirim", "tur": "warning", "metin": f"Sipariş {s['no']} müşteri tarafından iptal edildi."})
        st.yayinla({"tip": "siparis", "no": s["no"]})
    return {"tamam": True, "siparis": s}


# ---------------- ürün değerlendirmeleri ----------------
@app.get("/api/magaza/urun/{urun_id}/degerlendirmeler")
async def urun_degerlendirmeleri(request: Request, urun_id: int):
    """Ürün sayfası: puan özeti, yorumlar; giriş yapmış müşteri için yazma hakkı ve kendi değerlendirmesi."""
    if await gorunur_urun(request, urun_id) is None:
        return JSONResponse({"hata": "Ürün bulunamadı."}, status_code=404)
    k = await istekteki_kullanici(request)
    sonuc = await asyncio.to_thread(degerlendirmeler.urun_icin, urun_id, k["id"] if k else None)
    teslim = await asyncio.to_thread(siparisler.teslim_alinanlar, k["id"]) if k else {}
    return {**sonuc, "giris": bool(k), "yazabilir": urun_id in teslim}


@app.put("/api/hesap/degerlendirme/{urun_id}")
async def degerlendirme_yaz(request: Request, urun_id: int):
    """Yıldız ve yorum: sadece ürünü satın alıp teslim almış müşteri (önceki değerlendirmesini günceller)."""
    k = request.state.kullanici
    veri = await request.json()
    u = await asyncio.to_thread(dukkan.urun, urun_id)
    if u is None:
        return JSONResponse({"hata": "Ürün bulunamadı."}, status_code=404)
    if urun_id not in await asyncio.to_thread(siparisler.teslim_alinanlar, k["id"]):
        return kullanici_hatasi("Sadece satın alıp teslim aldığınız ürünleri değerlendirebilirsiniz.")
    try:
        _, yeni = await asyncio.to_thread(degerlendirmeler.kaydet, urun_id, k, veri.get("puan"), veri.get("yorum"))
    except DegerlendirmeHatasi as e:
        return kullanici_hatasi(e)
    _puan_bellek[0] = 0   # vitrin puanları tazelensin
    if yeni:
        for st in list(calismalar.values()):
            st.yayinla({"tip": "bildirim", "tur": "info",
                        "metin": f"⭐ Yeni değerlendirme: {u['ad']} — {'★' * int(veri.get('puan') or 0)} ({k['ad']})"})
            st.yayinla({"tip": "degerlendirme"})
    return {"tamam": True, **await asyncio.to_thread(degerlendirmeler.urun_icin, urun_id, k["id"]), "yazabilir": True,
            "giris": True}


@app.delete("/api/hesap/degerlendirme/{urun_id}")
async def degerlendirme_sil(request: Request, urun_id: int):
    k = request.state.kullanici
    try:
        await asyncio.to_thread(degerlendirmeler.sil, urun_id, k["id"])
    except DegerlendirmeHatasi as e:
        return kullanici_hatasi(e)
    _puan_bellek[0] = 0
    teslim = await asyncio.to_thread(siparisler.teslim_alinanlar, k["id"])
    return {"tamam": True, **await asyncio.to_thread(degerlendirmeler.urun_icin, urun_id, k["id"]),
            "yazabilir": urun_id in teslim, "giris": True}


@app.get("/api/panel/degerlendirmeler")
async def panel_degerlendirmeleri(request: Request):
    """Satıcının ürünlerine yapılan bütün değerlendirmeler (gizlenenler dahil)."""
    urunler = {u["id"]: u for u in await asyncio.to_thread(dukkan.urunler, request.state.kullanici["id"])}
    odalar = await asyncio.to_thread(kullanici_odalari, request.state.kullanici["id"])
    liste = await asyncio.to_thread(degerlendirmeler.listele, list(urunler))
    ozet = {}
    for d in liste:
        u = urunler[d["urun_id"]]
        if u["id"] not in ozet:
            ozet[u["id"]] = urun_ozeti(u, odalar)
        d["urun"] = {"id": u["id"], "ad": u["ad"], "kapak": ozet[u["id"]]["kapak"], "adres": ozet[u["id"]]["adres"]}
    gorunen = [d for d in liste if d["gorunur"]]
    return {"liste": liste, "sayi": len(liste), "cevapsiz": sum(1 for d in liste if not d["satici_cevap"]),
            "ortalama": round(sum(d["puan"] for d in gorunen) / len(gorunen), 1) if gorunen else 0}


async def satici_degerlendirmesi(request, did):
    d = await asyncio.to_thread(degerlendirmeler.degerlendirme, did)
    u = await asyncio.to_thread(dukkan.urun, d["urun_id"]) if d else None
    return d if u and u["kullanici_id"] == request.state.kullanici["id"] else None


@app.post("/api/panel/degerlendirmeler/{did}/cevap")
async def degerlendirme_cevapla(request: Request, did: int):
    if await satici_degerlendirmesi(request, did) is None:
        return JSONResponse({"hata": "Değerlendirme bulunamadı."}, status_code=404)
    d = await asyncio.to_thread(degerlendirmeler.cevapla, did, (await request.json()).get("cevap", ""))
    return {"tamam": True, "degerlendirme": d}


@app.patch("/api/panel/degerlendirmeler/{did}")
async def degerlendirme_gorunurluk(request: Request, did: int):
    if await satici_degerlendirmesi(request, did) is None:
        return JSONResponse({"hata": "Değerlendirme bulunamadı."}, status_code=404)
    d = await asyncio.to_thread(degerlendirmeler.gorunurluk, did, bool((await request.json()).get("gorunur")))
    _puan_bellek[0] = 0
    return {"tamam": True, "degerlendirme": d}


def saticilara_talep_bildir(s, metin, tur="warning"):
    for st in list(calismalar.values()):
        st.yayinla({"tip": "bildirim", "tur": tur, "metin": metin})
        st.yayinla({"tip": "siparis", "no": s["no"]})


@app.post("/api/hesap/siparisler/{siparis_id}/talep")
async def talep_gonder(request: Request, siparis_id: int):
    """Müşterinin iptal (hazırlanırken) ya da iade (teslimden sonra) talebi; satıcı panelden yanıtlar."""
    veri = await request.json()
    k = request.state.kullanici
    try:
        s = await asyncio.to_thread(siparisler.talep_olustur, siparis_id, k, veri.get("tur"), veri.get("neden"),
                                    veri.get("aciklama", ""), veri.get("iban", ""))
    except SiparisHatasi as e:
        return kullanici_hatasi(e)
    saticilara_talep_bildir(s, f"↩️ {s['talep_adi']}: {s['no']} — {k['ad']} ({s['talep_neden']})")
    return {"tamam": True, "siparis": s}


@app.post("/api/hesap/siparisler/{siparis_id}/talep/geri-cek")
async def talep_geri_cek(request: Request, siparis_id: int):
    try:
        s = await asyncio.to_thread(siparisler.talep_geri_cek, siparis_id, request.state.kullanici)
    except SiparisHatasi as e:
        return kullanici_hatasi(e)
    saticilara_talep_bildir(s, f"{s['talep_adi']} geri çekildi: {s['no']}", "secondary")
    return {"tamam": True, "siparis": s}


# ---------------- panel: siparişler, müşteriler, satıcılar (sadece satıcı) ----------------
@app.post("/api/panel/siparisler/{siparis_id}/talep")
async def talep_yanitla(request: Request, siparis_id: int):
    """Satıcının iptal / iade talebine kararı: onayla, reddet, tamamla (iade: ürün geri geldi). Kartla ödenen
    siparişte para karta iade edilir; iade başarısızsa iade tamamlanmış sayılmaz."""
    veri = await request.json()
    karar, notu, kim = veri.get("karar"), veri.get("not", ""), request.state.kullanici["ad"]
    s = await asyncio.to_thread(siparisler.siparis, siparis_id)
    if s is None or not s.get("talep"):
        return JSONResponse({"hata": "Bu siparişte talep yok."}, status_code=404)
    iade_notu = ""
    if karar == "tamamla" and s["talep"] == "iade" and s["talep_durum"] == "onaylandi":
        if s["odeme"] == "kart" and s.get("odeme_ref"):
            try:
                await asyncio.to_thread(kart_odeme.karta_iade, s["odeme_ref"], s["toplam"])
            except OdemeHatasi as e:
                return kullanici_hatasi(f"Karta para iadesi yapılamadı: {e} İade tamamlanmadı; tekrar deneyin ya da "
                                        "iyzico panelinden iade edip sonra tekrar tamamlayın.")
            iade_notu = f"{tl(s['toplam'])} karta iade edildi."
        else:
            iade_notu = f"{tl(s['toplam'])} müşterinin IBAN'ına ({s['talep_iban']}) havaleyle iade edilir."
        notu = " ".join(x for x in (iade_notu, notu) if x)
    try:
        s = await asyncio.to_thread(siparisler.talep_karar, siparis_id, karar, kim, notu)
    except SiparisHatasi as e:
        return kullanici_hatasi(e)
    if karar == "onayla" and s["talep"] == "iptal":   # iptal talebi onaylandı: kartla ödendiyse para iadesi
        iade = await kart_iadesi(s, kim)
        if iade:
            s = await asyncio.to_thread(siparisler.durum_degistir, siparis_id, "iptal", "Sistem", iade)
    return {"tamam": True, "siparis": s}



@app.get("/api/panel/siparisler")
async def panel_siparisleri(durum: str = ""):
    liste = await asyncio.to_thread(siparisler.listele, None, durum)
    musteriler = {}
    for s in liste:
        if s["musteri_id"] not in musteriler:
            musteriler[s["musteri_id"]] = await asyncio.to_thread(hesaplar.kullanici, s["musteri_id"])
        m = musteriler[s["musteri_id"]]
        s["musteri"] = ({"ad": m["ad"], "eposta": m["eposta"], "kullanici_adi": m["kullanici_adi"]} if m
                        else {"ad": "(silinmiş hesap)", "eposta": "", "kullanici_adi": ""})
    sayilar = await asyncio.to_thread(siparisler.sayilar)
    return {"siparisler": liste, "sayilar": sayilar, **siparis_durum_bilgisi()}


@app.patch("/api/panel/siparisler/{siparis_id}")
async def siparis_guncelle(request: Request, siparis_id: int):
    veri = await request.json()
    try:
        onceki = await asyncio.to_thread(siparisler.siparis, siparis_id)
        s = await asyncio.to_thread(siparisler.durum_degistir, siparis_id, veri.get("durum"),
                                    request.state.kullanici["ad"], veri.get("aciklama", ""),
                                    veri.get("kargo_firmasi"), veri.get("takip_no"))
    except SiparisHatasi as e:
        return kullanici_hatasi(e)
    if s["durum"] == "iptal" and onceki and onceki["durum"] != "iptal":   # kartla ödendiyse iade
        iade = await kart_iadesi(s, request.state.kullanici["ad"])
        if iade:
            s = await asyncio.to_thread(siparisler.durum_degistir, siparis_id, "iptal", "Sistem", iade)
    return {"tamam": True, "siparis": s}


def kisi_satiri(k, ozet=None):
    n, toplam, son = (ozet or {}).get(k["id"], (0, 0, None))
    return {"id": k["id"], "ad": k["ad"], "kullanici_adi": k["kullanici_adi"], "eposta": k["eposta"], "aktif": k["aktif"],
            "telefon": k["telefon"], "kayit_tarihi": k["kayit_tarihi"], "son_giris": k["son_giris"],
            "siparis_sayisi": n, "harcama": toplam, "son_siparis": son}


@app.get("/api/panel/musteriler")
async def panel_musterileri():
    ozet = await asyncio.to_thread(siparisler.musteri_ozetleri)
    return [kisi_satiri(k, ozet) for k in await asyncio.to_thread(hesaplar.listele, "musteri")]


@app.get("/api/panel/saticilar")
async def panel_saticilari(request: Request):
    return {"saticilar": [kisi_satiri(k) for k in await asyncio.to_thread(hesaplar.listele, "satici")],
            "ben": request.state.kullanici["id"]}


@app.post("/api/panel/saticilar")
async def satici_ekle(request: Request):
    veri = await request.json()
    try:
        k = await asyncio.to_thread(hesaplar.kayit, str(veri.get("kullanici_adi", "")), str(veri.get("ad", "")),
                                    str(veri.get("sifre", "")), "satici", veri.get("eposta"))
    except HesapHatasi as e:
        return kullanici_hatasi(e)
    return {"tamam": True, "satici": kisi_satiri(k)}


@app.patch("/api/panel/kullanicilar/{kullanici_id}")
async def hesap_ac_kapat(request: Request, kullanici_id: int):
    """Satıcı ya da müşteri hesabını kapatır / açar. Kendi hesabınızı ve son açık satıcı hesabını kapatamazsınız."""
    aktif = bool((await request.json()).get("aktif"))
    k = await asyncio.to_thread(hesaplar.kullanici, kullanici_id)
    if k is None:
        return JSONResponse({"hata": "Hesap bulunamadı."}, status_code=404)
    if not aktif:
        if kullanici_id == request.state.kullanici["id"]:
            return kullanici_hatasi("Kendi hesabınızı kapatamazsınız.")
        if k["rol"] == "satici":
            acik = [s for s in await asyncio.to_thread(hesaplar.listele, "satici") if s["aktif"]]
            if len(acik) <= 1:
                return kullanici_hatasi("En az bir satıcı hesabı açık kalmalı.")
    await asyncio.to_thread(hesaplar.aktiflik, kullanici_id, aktif)
    if not aktif and kullanici_id in calismalar:   # açık panel sekmeleri kapansın
        for ist in list(calismalar[kullanici_id].istemciler.values()):
            await ist.ws.close(code=4401)
    return {"tamam": True}


# ----------------------------------------------------------------------
# CANLI DESTEK: ziyaretçi /ws/destek ile bağlanır; satıcılara panelin açık bağlantısından (/ws) haber gider
# ----------------------------------------------------------------------
destek_soketleri = {}   # sohbet id -> ziyaretçinin açık sekmeleri (WebSocket kümesi)


async def ziyaretciye(sohbet_id, mesaj):
    metin = json_metni(mesaj)
    for ws in list(destek_soketleri.get(sohbet_id, ())):
        try:
            await ws.send_text(metin)
        except Exception:
            destek_soketleri.get(sohbet_id, set()).discard(ws)


def saticilara(olay, **veri):
    for st in list(calismalar.values()):
        st.yayinla({"tip": "destek", "olay": olay, **veri})


async def destek_zaman_asimi_dongusu():
    """Mesaj gelmeyen açık sohbetleri kapatır (destek.ZAMAN_ASIMI)."""
    while True:
        await asyncio.sleep(30)
        try:
            for sid, mesaj in await asyncio.to_thread(destek.zaman_asimlari):
                await ziyaretciye(sid, {"tip": "kapandi", "neden": "zaman_asimi", "mesaj": mesaj})
                saticilara("kapandi", sohbet_id=sid, mesaj=mesaj, sohbet=await asyncio.to_thread(destek.sohbet, sid, False))
        except Exception:
            traceback.print_exc()


def ziyaretci_sohbeti(s):
    """Ziyaretçiye gösterilen sohbet (iç alanlar olmadan)."""
    if s is None:
        return None
    return {k: s[k] for k in ("id", "ad", "durum", "kapanis", "satici_ad", "puan", "yorum", "olusturma", "mesajlar")}


@app.get("/api/magaza/destek")
async def destek_durumu(request: Request):
    s = await asyncio.to_thread(destek.anahtarla, request.cookies.get(DESTEK_CEREZI))
    k = await istekteki_kullanici(request)
    return {"sohbet": ziyaretci_sohbeti(s), "kisi": {"ad": k["ad"], "eposta": k["eposta"]} if k else None}


@app.post("/api/magaza/destek")
async def destek_baslat(request: Request):
    veri = await request.json()
    eski = await asyncio.to_thread(destek.anahtarla, request.cookies.get(DESTEK_CEREZI))
    if eski and eski["durum"] == "acik":
        return {"sohbet": ziyaretci_sohbeti(eski)}
    k = await istekteki_kullanici(request)
    try:
        anahtar, s = await asyncio.to_thread(destek.baslat, veri.get("ad"), veri.get("eposta") or (k["eposta"] if k else ""),
                                             veri.get("metin"), k, veri.get("sayfa"))
    except DestekHatasi as e:
        return kullanici_hatasi(e)
    bildir_metni = f"💬 Canlı destek: {s['ad']} yazıyor — “{s['son']['metin'][:60]}”"
    for st in list(calismalar.values()):
        st.yayinla({"tip": "bildirim", "tur": "info", "metin": bildir_metni})
    saticilara("yeni", sohbet=await asyncio.to_thread(destek.sohbet, s["id"], False))
    yanit = JSONResponse({"sohbet": ziyaretci_sohbeti(s)})
    guvenli = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    yanit.set_cookie(DESTEK_CEREZI, anahtar, max_age=30 * 24 * 3600, httponly=True, samesite="lax", secure=guvenli)
    return yanit


@app.post("/api/magaza/destek/kapat")
async def destek_ziyaretci_kapat(request: Request):
    s = await asyncio.to_thread(destek.anahtarla, request.cookies.get(DESTEK_CEREZI))
    if s is None:
        return JSONResponse({"hata": "Sohbet bulunamadı."}, status_code=404)
    mesaj = await asyncio.to_thread(destek.kapat, s["id"], "musteri")
    if mesaj:
        await ziyaretciye(s["id"], {"tip": "kapandi", "neden": "musteri", "mesaj": mesaj})
        saticilara("kapandi", sohbet_id=s["id"], mesaj=mesaj, sohbet=await asyncio.to_thread(destek.sohbet, s["id"], False))
    return {"sohbet": ziyaretci_sohbeti(await asyncio.to_thread(destek.sohbet, s["id"]))}


@app.post("/api/magaza/destek/oy")
async def destek_oyla(request: Request):
    veri = await request.json()
    try:
        s = await asyncio.to_thread(destek.oyla, request.cookies.get(DESTEK_CEREZI), veri.get("puan"), veri.get("yorum"))
    except DestekHatasi as e:
        return kullanici_hatasi(e)
    saticilara("oy", sohbet=await asyncio.to_thread(destek.sohbet, s["id"], False))
    return {"sohbet": ziyaretci_sohbeti(s)}


@app.websocket("/ws/destek")
async def destek_baglanti(ws: WebSocket):
    """Ziyaretçinin sohbet bağlantısı: mesaj gönderir, "yazıyor" bildirir; cevaplar ve kapanış buradan gelir."""
    s = await asyncio.to_thread(destek.anahtarla, ws.cookies.get(DESTEK_CEREZI))
    await ws.accept()
    if s is None or not ayni_kaynak(ws):
        await ws.close(code=4404)
        return
    sid = s["id"]
    destek_soketleri.setdefault(sid, set()).add(ws)
    son_yaziyor = 0.0
    try:
        while True:
            m = await ws.receive_json()
            if m.get("tip") == "mesaj":
                try:
                    mesaj = await asyncio.to_thread(destek.mesaj_ekle, sid, "musteri", s["ad"], m.get("metin"))
                except DestekHatasi as e:
                    await ws.send_text(json_metni({"tip": "hata", "metin": str(e)}))
                    continue
                await ziyaretciye(sid, {"tip": "mesaj", "mesaj": mesaj})
                saticilara("mesaj", sohbet_id=sid, mesaj=mesaj, sohbet=await asyncio.to_thread(destek.sohbet, sid, False))
            elif m.get("tip") == "yaziyor" and time.time() - son_yaziyor > 2:
                son_yaziyor = time.time()
                saticilara("yaziyor", sohbet_id=sid)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        destek_soketleri.get(sid, set()).discard(ws)


# ---------------- panel: canlı destek (sadece satıcı) ----------------
@app.get("/api/panel/destek")
async def panel_destek_listesi(durum: str = ""):
    return {"sohbetler": await asyncio.to_thread(destek.listele, durum or None),
            "sayilar": await asyncio.to_thread(destek.sayilar)}


@app.get("/api/panel/destek/{sohbet_id}")
async def panel_destek_sohbeti(sohbet_id: int):
    await asyncio.to_thread(destek.okundu, sohbet_id)
    s = await asyncio.to_thread(destek.sohbet, sohbet_id)
    if s is None:
        return JSONResponse({"hata": "Sohbet bulunamadı."}, status_code=404)
    saticilara("okundu", sohbet_id=sohbet_id)   # diğer satıcıların okunmamış sayısı da düşsün
    return s


@app.post("/api/panel/destek/{sohbet_id}/mesaj")
async def panel_destek_mesaj(request: Request, sohbet_id: int):
    k = request.state.kullanici
    try:
        mesaj = await asyncio.to_thread(destek.mesaj_ekle, sohbet_id, "satici", k["ad"], (await request.json()).get("metin"), k["id"])
    except DestekHatasi as e:
        return kullanici_hatasi(e)
    await ziyaretciye(sohbet_id, {"tip": "mesaj", "mesaj": mesaj})
    saticilara("mesaj", sohbet_id=sohbet_id, mesaj=mesaj, sohbet=await asyncio.to_thread(destek.sohbet, sohbet_id, False))
    return {"mesaj": mesaj}


@app.post("/api/panel/destek/{sohbet_id}/yaziyor")
async def panel_destek_yaziyor(request: Request, sohbet_id: int):
    await ziyaretciye(sohbet_id, {"tip": "yaziyor", "ad": request.state.kullanici["ad"]})
    return {"tamam": True}


@app.post("/api/panel/destek/{sohbet_id}/kapat")
async def panel_destek_kapat(sohbet_id: int):
    mesaj = await asyncio.to_thread(destek.kapat, sohbet_id, "satici")
    if mesaj:
        await ziyaretciye(sohbet_id, {"tip": "kapandi", "neden": "satici", "mesaj": mesaj})
        saticilara("kapandi", sohbet_id=sohbet_id, mesaj=mesaj, sohbet=await asyncio.to_thread(destek.sohbet, sohbet_id, False))
    return {"tamam": True}


@app.delete("/api/panel/destek/{sohbet_id}")
async def panel_destek_sil(sohbet_id: int):
    try:
        await asyncio.to_thread(destek.sil, sohbet_id)
    except DestekHatasi as e:
        return kullanici_hatasi(e)
    saticilara("silindi", sohbet_id=sohbet_id)
    return {"tamam": True}


def yerel_adresler():
    adresler = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))   # paket gönderilmez; sadece ağ arayüzünün adresini öğrenir
            adresler.append(sk.getsockname()[0])
    except OSError:
        pass
    return adresler


def sunucuyu_calistir(port):
    """Otomatik yeniden başlatmada ayrı süreçte çalışan sunucu."""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def port_dolu_mu(port):
    """Sunucu zaten çalışıyor mu (VS Code açılınca ve baslat.bat ile iki kez başlatılırsa ikincisi çıksın)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.5)
        return sk.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("STUDYO_PORT", "8000"))
    if port_dolu_mu(port):
        print(f"\n  Tablo Stüdyosu Web zaten çalışıyor: http://localhost:{port}  (panel: /panel)")
        print("  Ayrıca başlatmaya gerek yok; dosyaları kaydettikçe sunucu ve sayfalar kendini yeniler.\n")
        raise SystemExit(0)
    print("\n  Tablo Stüdyosu Web çalışıyor:")
    print(f"    Mağaza (herkese açık): http://localhost:{port}")
    print(f"    Yönetim paneli:        http://localhost:{port}/panel")
    for ip in yerel_adresler():
        print(f"    Diğer bilgisayarlarda: http://{ip}:{port}  (panel: /panel)")
    print(f"    Satıcı: {hesaplar.kullanici_sayisi('satici')}   Müşteri: {hesaplar.kullanici_sayisi('musteri')}")
    print(f"    Veriler: {VERI}")
    # Proje dosyalarından biri (.py) kaydedilince sunucu kendini kapatıp yeniden açar; açık sayfalar kendiliğinden
    # yeniden bağlanır. HTML / JS / CSS için yeniden başlatma gerekmez (sayfayı yenilemek yeter).
    # Kapatmak için: set STUDYO_OTOMATIK=0
    otomatik = os.environ.get("STUDYO_OTOMATIK", "1") != "0"
    if otomatik:
        # uvicorn'un kendi reload'u Windows'ta eski süreci Ctrl+C sinyaliyle kapatmaya çalışır, sinyal ulaşmazsa
        # süresiz bekler. watchfiles süreci süre sınırıyla kapatır, takılmaz. Stüdyo durumu her değişiklikten
        # 1 sn sonra zaten diske yazıldığı için ani kapanışta bir şey kaybolmaz.
        from watchfiles import PythonFilter, run_process
        print("    Otomatik yeniden başlatma açık: .py dosyaları kaydedilince sunucu kendini yeniler.\n")
        run_process(KOK, target=sunucuyu_calistir, args=(port,), watch_filter=PythonFilter(),
                    sigint_timeout=3, sigkill_timeout=2,
                    callback=lambda degisen: print("  Değişiklik algılandı (" + ", ".join(
                        sorted({os.path.basename(y) for _, y in degisen})) + "), sunucu yeniden başlatılıyor…"))
    else:
        print()
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
