"""Mağaza: panelden eklenen ürünler (cam tablolar) ve ürünlerin kayıtlı odalardaki sunum görselleri.

Ürünler veri/magaza/urunler.db'de, resimleri veri/magaza/resimler/ klasöründe durur. Bir ürünün bir odadaki görseli
'Tablo Ekle' sekmesindeki çizimin aynısıdır (cekirdek.tabloyu_odaya_ekle, odanın kayıtlı yansıma ayarlarıyla) ve
veri/magaza/onbellek/ klasörüne yazılır. Önbellek anahtarında ürünün ve odanın güncelleme zamanı vardır: oda
'Oda Sunumları'nda düzenlenip kaydedilince o odadaki ürün görselleri bir sonraki istekte kendiliğinden yeniden çizilir.
"""
import glob
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime
from threading import Lock

import cv2
import numpy as np

import cekirdek as ck

GORSEL_BOYUTLARI = {"k": 480, "o": 960, "b": 1800}   # sunum görsellerinin uzun kenarı (küçük / orta / büyük)
RESIM_MAKS = 2600                                     # ürün resimleri en fazla bu boyutta saklanır
AD_UZUNLUGU = 120
ACIKLAMA_UZUNLUGU = 4000
GECICI_OMUR = 24 * 3600                               # kaydedilmeyen yüklemeler bir gün sonra silinir


class MagazaHatasi(Exception):
    """Kullanıcıya gösterilecek ürün hatası."""


def simdi():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def arama_metni(s):
    """Türkçe harflerle büyük / küçük harf duyarsız arama için sadeleştirilmiş metin."""
    s = (s or "").replace("I", "ı").replace("İ", "i").lower()
    return s.translate(str.maketrans("çğıöşü", "cgiosu"))


def kisa_ad(ad):
    """Adresteki okunur kısım: 'Kahve Fincanı Cam Tablo' -> 'kahve-fincani-cam-tablo'."""
    return re.sub(r"[^a-z0-9]+", "-", arama_metni(ad)).strip("-")[:60] or "urun"


def jpeg(resim, kalite=88):
    ok, buf = cv2.imencode(".jpg", resim, [cv2.IMWRITE_JPEG_QUALITY, kalite])
    if not ok:
        raise IOError("Resim kodlanamadı")
    return buf.tobytes()


def kucult(resim, maks):
    h, w = resim.shape[:2]
    o = maks / max(h, w)
    return cv2.resize(resim, None, fx=o, fy=o, interpolation=cv2.INTER_AREA) if o < 1 else resim


def dosyaya_yaz(yol, veri):
    """Yarım kalmış dosya okunmasın diye önce geçici adla yazıp sonra yerine koyar."""
    gecici = f"{yol}.{uuid.uuid4().hex[:6]}.tmp"
    with open(gecici, "wb") as f:
        f.write(veri)
    os.replace(gecici, yol)


class Bellek:
    """Küçük, iş parçacığı güvenli LRU önbellek."""

    def __init__(self, boyut):
        self.boyut, self.veri, self.kilit = boyut, OrderedDict(), Lock()

    def al(self, anahtar, uret):
        with self.kilit:
            if anahtar in self.veri:
                self.veri.move_to_end(anahtar)
                return self.veri[anahtar]
        deger = uret()
        with self.kilit:
            self.veri[anahtar] = deger
            while len(self.veri) > self.boyut:
                self.veri.popitem(last=False)
        return deger


class Magaza:
    def __init__(self, klasor):
        self.klasor = klasor
        self.resimler = os.path.join(klasor, "resimler")
        self.onbellek = os.path.join(klasor, "onbellek")
        self.gecici = os.path.join(klasor, "gecici")
        for k in (self.resimler, self.onbellek, self.gecici):
            os.makedirs(k, exist_ok=True)
        self.db = os.path.join(klasor, "urunler.db")
        with self.baglan() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS urunler (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    kullanici_id INTEGER NOT NULL,
                    ad          TEXT NOT NULL,
                    aciklama    TEXT NOT NULL DEFAULT '',
                    fiyat       REAL NOT NULL,
                    eski_fiyat  REAL,                 -- indirimden önceki fiyat (üstü çizili gösterilir)
                    tip         TEXT NOT NULL,        -- yatay / dikey / kare / yuvarlak
                    dosya       TEXT NOT NULL,        -- resimler/ klasöründeki ürün resmi
                    genislik    INTEGER NOT NULL,
                    yukseklik   INTEGER NOT NULL,
                    yayinda     INTEGER NOT NULL DEFAULT 1,
                    odalar      TEXT,                 -- JSON oda id listesi; NULL: o tipteki bütün odalar
                    kapak_oda   INTEGER,              -- vitrinde ilk görünen oda (NULL: listedeki ilk oda)
                    olusturma   TEXT NOT NULL,
                    guncelleme  TEXT NOT NULL
                )""")
        # Çizim kodu (cekirdek.py) değişince eski görseller kullanılmasın
        self.cizim_surumu = str(int(os.path.getmtime(ck.__file__)))
        self.oda_bellek = Bellek(16)      # (oda klasörü, oda id, güncelleme) -> (resim, köşeler, ışık analizi)
        self.eser_bellek = Bellek(12)     # ürün resmi dosyası -> resim
        self.gecici_bellek = Bellek(48)   # kaydedilmemiş ürünün oda önizlemeleri
        self.oda_listeleri = {}           # oda klasörü -> (veritabanı değişme zamanı, odalar)
        self.kilit = Lock()
        self.gecicileri_temizle()

    @contextmanager
    def baglan(self):
        """İşlem bitince kaydedip kapanan bağlantı (hata olursa geri alınır)."""
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # ÜRÜNLER
    # ------------------------------------------------------------------
    @staticmethod
    def _sozluk(satir):
        u = dict(satir)
        u["odalar"] = json.loads(u["odalar"]) if u["odalar"] else None
        u["yayinda"] = bool(u["yayinda"])
        u["kisa_ad"] = kisa_ad(u["ad"])
        return u

    def urun(self, urun_id):
        with self.baglan() as c:
            satir = c.execute("SELECT * FROM urunler WHERE id = ?", (urun_id,)).fetchone()
        return self._sozluk(satir) if satir else None

    def urunler(self, kullanici_id=None, yayinda=None, ara="", tip="", sirala="yeni"):
        kosul, deger = [], []
        if kullanici_id is not None:
            kosul.append("kullanici_id = ?")
            deger.append(kullanici_id)
        if yayinda is not None:
            kosul.append("yayinda = ?")
            deger.append(int(yayinda))
        if tip in ck.TABLO_TIPLERI:
            kosul.append("tip = ?")
            deger.append(tip)
        sorgu = "SELECT * FROM urunler" + (" WHERE " + " AND ".join(kosul) if kosul else "")
        with self.baglan() as c:
            liste = [self._sozluk(s) for s in c.execute(sorgu, deger).fetchall()]
        if ara.strip():
            kelimeler = arama_metni(ara).split()
            liste = [u for u in liste
                     if all(k in arama_metni(f"{u['ad']} {u['aciklama']} {ck.TABLO_TIPLERI[u['tip']]}") for k in kelimeler)]
        anahtar = {"ucuz": lambda u: (u["fiyat"], -u["id"]), "pahali": lambda u: (-u["fiyat"], -u["id"]),
                   "ad": lambda u: ck.oda_adi_sirasi(u["ad"])}.get(sirala, lambda u: -u["id"])
        return sorted(liste, key=anahtar)

    def gecici_kaydet(self, veri):
        """Panelde seçilen ürün resmi: kaydetmeden önce odalarda önizlenebilsin diye geçici olarak saklanır."""
        resim = cv2.imdecode(np.frombuffer(veri, np.uint8), cv2.IMREAD_COLOR)
        if resim is None:
            raise MagazaHatasi("Resim dosyası okunamadı.")
        resim = kucult(resim, RESIM_MAKS)
        anahtar = uuid.uuid4().hex
        dosyaya_yaz(os.path.join(self.gecici, f"{anahtar}.jpg"), jpeg(resim, 95))
        h, w = resim.shape[:2]
        return {"gecici": anahtar, "tip": ck.resim_tipi_tahmin(resim), "genislik": w, "yukseklik": h}

    def gecici_yol(self, anahtar):
        if not re.fullmatch(r"[0-9a-f]{32}", str(anahtar or "")):
            raise MagazaHatasi("Geçersiz resim anahtarı.")
        yol = os.path.join(self.gecici, f"{anahtar}.jpg")
        if not os.path.isfile(yol):
            raise MagazaHatasi("Yüklenen resim bulunamadı; lütfen resmi tekrar seçin.")
        return yol

    def gecicileri_temizle(self):
        sinir = time.time() - GECICI_OMUR
        for yol in glob.glob(os.path.join(self.gecici, "*")):
            try:
                if os.path.getmtime(yol) < sinir:
                    os.remove(yol)
            except OSError:
                pass

    @staticmethod
    def _alanlari_dogrula(a, yeni):
        ad = str(a.get("ad") or "").strip()
        if not ad:
            raise MagazaHatasi("Ürün adı boş olamaz.")
        if len(ad) > AD_UZUNLUGU:
            raise MagazaHatasi(f"Ürün adı en fazla {AD_UZUNLUGU} karakter olabilir.")
        try:
            fiyat = round(float(str(a.get("fiyat")).replace(",", ".")), 2)
        except (TypeError, ValueError):
            raise MagazaHatasi("Fiyat geçerli bir sayı olmalı.") from None
        if not 0 < fiyat < 10_000_000:
            raise MagazaHatasi("Fiyat sıfırdan büyük olmalı.")
        eski = a.get("eski_fiyat")
        if eski in (None, ""):
            eski = None
        else:
            try:
                eski = round(float(str(eski).replace(",", ".")), 2)
            except ValueError:
                raise MagazaHatasi("İndirimsiz fiyat geçerli bir sayı olmalı.") from None
            if eski <= fiyat:
                raise MagazaHatasi("İndirimsiz fiyat, satış fiyatından büyük olmalı (indirim yoksa boş bırakın).")
        tip = a.get("tip")
        if tip not in ck.TABLO_TIPLERI:
            raise MagazaHatasi("Tablo tipini seçin.")
        odalar = a.get("odalar")
        if odalar is not None:
            odalar = [int(i) for i in odalar]
        kapak = a.get("kapak_oda")
        alanlar = {"ad": ad, "aciklama": str(a.get("aciklama") or "").strip()[:ACIKLAMA_UZUNLUGU],
                   "fiyat": fiyat, "eski_fiyat": eski, "tip": tip, "yayinda": int(bool(a.get("yayinda", True))),
                   "odalar": json.dumps(odalar) if odalar is not None else None,
                   "kapak_oda": int(kapak) if kapak not in (None, "") else None}
        if yeni and not a.get("gecici"):
            raise MagazaHatasi("Ürün resmi seçin.")
        return alanlar

    def kaydet(self, kullanici_id, a, urun_id=None):
        """Yeni ürün ekler ya da kullanıcının ürününü günceller; ürünün id'sini döndürür."""
        alanlar = self._alanlari_dogrula(a, urun_id is None)
        eski = None
        if urun_id is not None:
            eski = self.urun(urun_id)
            if eski is None or eski["kullanici_id"] != kullanici_id:
                raise MagazaHatasi("Ürün bulunamadı.")
        tasinan = None   # (geçici yol, kalıcı yol): kayıt yazılamazsa resim geri taşınır
        if a.get("gecici"):
            kaynak = self.gecici_yol(a["gecici"])
            # cv2.imread Türkçe karakterli yolları (ör. "Yeni klasör") okuyamaz
            resim = ck.turkce_karakter_destekli_imread(kaynak)
            if resim is None:
                raise MagazaHatasi("Ürün resmi okunamadı; lütfen resmi tekrar seçin.")
            dosya = f"u_{uuid.uuid4().hex}.jpg"
            tasinan = (kaynak, os.path.join(self.resimler, dosya))
            os.replace(*tasinan)
            alanlar.update(dosya=dosya, genislik=resim.shape[1], yukseklik=resim.shape[0])
        zaman = simdi()
        try:
            with self.baglan() as c:
                if urun_id is None:
                    sutunlar = ["kullanici_id", *alanlar, "olusturma", "guncelleme"]
                    cur = c.execute(f"INSERT INTO urunler ({', '.join(sutunlar)}) VALUES ({', '.join('?' * len(sutunlar))})",
                                    [kullanici_id, *alanlar.values(), zaman, zaman])
                    urun_id = cur.lastrowid
                else:
                    c.execute(f"UPDATE urunler SET {', '.join(f'{k} = ?' for k in alanlar)}, guncelleme = ? WHERE id = ?",
                              [*alanlar.values(), zaman, urun_id])
        except Exception:
            if tasinan:
                os.replace(tasinan[1], tasinan[0])
            raise
        if eski and "dosya" in alanlar:
            self._dosya_sil(os.path.join(self.resimler, eski["dosya"]))
            self.onbellek_temizle(urun_id)
        return urun_id

    def yayin_degistir(self, kullanici_id, urun_id, yayinda):
        with self.baglan() as c:
            n = c.execute("UPDATE urunler SET yayinda = ? WHERE id = ? AND kullanici_id = ?",
                          (int(bool(yayinda)), urun_id, kullanici_id)).rowcount
        if not n:
            raise MagazaHatasi("Ürün bulunamadı.")

    def sil(self, kullanici_id, urun_id):
        u = self.urun(urun_id)
        if u is None or u["kullanici_id"] != kullanici_id:
            raise MagazaHatasi("Ürün bulunamadı.")
        with self.baglan() as c:
            c.execute("DELETE FROM urunler WHERE id = ?", (urun_id,))
        self._dosya_sil(os.path.join(self.resimler, u["dosya"]))
        self.onbellek_temizle(urun_id)
        return u

    def onbellek_temizle(self, urun_id):
        for yol in glob.glob(os.path.join(self.onbellek, f"u{urun_id}_*")):
            self._dosya_sil(yol)

    @staticmethod
    def _dosya_sil(yol):
        try:
            if os.path.isfile(yol):
                os.remove(yol)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # ODALAR (ürün sahibinin 'Oda Sunumları'nda kaydettiği odalar)
    # ------------------------------------------------------------------
    def odalari_getir(self, oda_klasoru):
        """Kayıtlı odalar (güncelleme zamanıyla); veritabanı değişmedikçe bellekten."""
        db = os.path.join(oda_klasoru, "odalar.db")
        if not os.path.isfile(db):
            return []
        zaman = os.path.getmtime(db)
        with self.kilit:
            kayit = self.oda_listeleri.get(oda_klasoru)
        if kayit and kayit[0] == zaman:
            return kayit[1]
        odalar = ck.kayitli_odalari_getir(oda_klasoru)
        conn = ck.veritabani_baglan(oda_klasoru)
        try:
            tarihler = dict(conn.execute("SELECT id, guncelleme FROM odalar").fetchall())
        finally:
            conn.close()
        for o in odalar:
            o["guncelleme"] = tarihler.get(o["id"], "")
        with self.kilit:
            self.oda_listeleri[oda_klasoru] = (zaman, odalar)
        return odalar

    @staticmethod
    def urun_odalari(urun, odalar, tip=None, secim="urun"):
        """Ürünün sunulduğu odalar (vitrin sırasıyla: kapak oda başta). secim="hepsi": seçilmeyenler de
        (panelde seçim yaparken)."""
        tip = tip or urun["tip"]
        uygun = [o for o in odalar if o["tip"] == tip]
        if secim != "hepsi" and urun.get("odalar") is not None:
            sira = {oda_id: i for i, oda_id in enumerate(urun["odalar"])}
            uygun = sorted((o for o in uygun if o["id"] in sira), key=lambda o: sira[o["id"]])
        kapak = urun.get("kapak_oda")
        return sorted(uygun, key=lambda o: o["id"] != kapak) if kapak is not None else uygun

    def _oda_yukle(self, oda_klasoru, oda):
        def uret():
            yol = os.path.join(oda_klasoru, os.path.basename(oda["dosya"]))
            resim = ck.turkce_karakter_destekli_imread(yol) if os.path.isfile(yol) else None
            if resim is None:
                raise FileNotFoundError(f"Oda resmi bulunamadı: {oda['dosya']}")
            h, w = resim.shape[:2]
            kose = oda["kose"].copy()
            if oda["genislik"] and oda["yukseklik"] and (w, h) != (oda["genislik"], oda["yukseklik"]):
                kose[:, 0] *= w / oda["genislik"]
                kose[:, 1] *= h / oda["yukseklik"]
            return resim, kose, ck.isik_kaynaklarini_bul(resim)
        return self.oda_bellek.al((oda_klasoru, oda["id"], oda["guncelleme"]), uret)

    def _eser_yukle(self, yol):
        def uret():
            resim = ck.turkce_karakter_destekli_imread(yol)
            if resim is None:
                raise FileNotFoundError("Ürün resmi okunamadı.")
            return resim
        return self.eser_bellek.al(yol, uret)

    def _ciz(self, oda_klasoru, oda, eser_yolu, boyut):
        resim, kose, analiz = self._oda_yukle(oda_klasoru, oda)
        uzun = GORSEL_BOYUTLARI[boyut]
        olcek = min(1.0, uzun / max(resim.shape[:2]))
        sonuc = ck.tabloyu_odaya_ekle(resim, kose, self._eser_yukle(eser_yolu), cercevesiz=oda["bos_duvar"],
                                      isik_analizi=analiz, yansima_ayari=oda["yansima"], tip=oda["tip"], olcek=olcek)
        return jpeg(kucult(sonuc, uzun), 88)

    def surum(self, urun, oda=None):
        """Görselin sürümü: ürün, oda ya da çizim kodu değişince değişir (adresteki ?v= ve önbellek dosya adı)."""
        metin = f"{urun['dosya']}|{urun['guncelleme']}|{self.cizim_surumu}"
        if oda is not None:
            metin += f"|{oda['id']}|{oda['guncelleme']}"
        return hashlib.sha1(metin.encode()).hexdigest()[:10]

    def sunum_gorseli(self, urun, oda_klasoru, oda, boyut):
        """Ürünün odadaki görseli (JPEG baytları); diskteki önbellekten ya da çizilip önbelleğe yazılarak."""
        on = f"u{urun['id']}_o{oda['id']}_{boyut}_"
        yol = os.path.join(self.onbellek, f"{on}{self.surum(urun, oda)}.jpg")
        if os.path.isfile(yol):
            with open(yol, "rb") as f:
                return f.read()
        veri = self._ciz(oda_klasoru, oda, os.path.join(self.resimler, urun["dosya"]), boyut)
        for eski in glob.glob(os.path.join(self.onbellek, f"{on}*.jpg")):   # oda / ürün değişmeden önceki görsel
            self._dosya_sil(eski)
        dosyaya_yaz(yol, veri)
        return veri

    def urun_resmi(self, urun, boyut):
        """Ürünün kendi resmi (odasız), istenen boyutta."""
        yol = os.path.join(self.onbellek, f"u{urun['id']}_resim_{boyut}_{self.surum(urun)}.jpg")
        if os.path.isfile(yol):
            with open(yol, "rb") as f:
                return f.read()
        veri = jpeg(kucult(self._eser_yukle(os.path.join(self.resimler, urun["dosya"])), GORSEL_BOYUTLARI[boyut]), 90)
        dosyaya_yaz(yol, veri)
        return veri

    def gecici_gorsel(self, anahtar, oda_klasoru, oda, boyut="o"):
        """Henüz kaydedilmemiş ürün resminin odadaki önizlemesi (sadece bellekte tutulur)."""
        yol = self.gecici_yol(anahtar)
        return self.gecici_bellek.al((anahtar, oda_klasoru, oda["id"], oda["guncelleme"], boyut),
                                     lambda: self._ciz(oda_klasoru, oda, yol, boyut))

    def eser_kopyala(self, kaynak):
        """'Tablo Ekle'ye yüklenmiş bir resmi ürün formuna geçici resim olarak aktarır."""
        with open(kaynak, "rb") as f:
            return self.gecici_kaydet(f.read())
