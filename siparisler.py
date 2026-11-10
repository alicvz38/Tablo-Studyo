"""Siparişler (veri/magaza/siparisler.db): müşterinin sepetinden oluşan sipariş, kalemleri ve durum geçmişi.

Fiyatlar sipariş anında sunucudaki ürün kaydından alınır (tarayıcıdan gelen fiyata güvenilmez); ürünün adı, fiyatı
ve görseli kaleme kopyalanır, ürün sonradan değişse ya da silinse de sipariş aynı kalır. Satıcılar durumu ilerletir
(onay, kargo takip no, teslim); her değişiklik geçmişe yazılır ve müşteri siparişinin aşamalarını görür.

Kartla ödemede sipariş önce "odeme" durumunda (ödeme bekleniyor) oluşur: listelerde ve ciroda görünmez. Ödeme
alınınca "alindi" olur (kartın markası ve son dört hanesi saklanır, kart bilgisi saklanmaz); alınamazsa silinir.
Banka sayfasında yarıda bırakılan ödemeler BEKLEYEN_OMUR sonra temizlenir.

İptal ve iade (müşterinin talebi, siparişin kargo akışından ayrı tutulur: talep, talep_durum):
  - "alindi": müşteri hemen iptal eder (musteri_iptal).
  - "hazirlaniyor": iptal talebi; satıcı onaylarsa sipariş iptal olur, reddederse devam eder.
  - "teslim": teslimden sonra IADE_SURESI_GUN içinde iade talebi; satıcı onaylar (ürünün nasıl gönderileceğini yazar),
    ürün geri gelince iadeyi tamamlar: sipariş "iade" olur ve cirodan düşer. Para iadesi (karta ya da IBAN'a)
    sunucuda yapılır.
"""
import re
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

DURUMLAR = {
    "alindi": "Sipariş alındı",
    "hazirlaniyor": "Hazırlanıyor",
    "kargoda": "Kargoya verildi",
    "teslim": "Teslim edildi",
    "iptal": "İptal edildi",
    "iade": "İade edildi",
}
SON_DURUMLAR = ("teslim", "iptal", "iade")   # satıcının durum listesinden artık değiştirilemeyenler
IADE_SURESI_GUN = 14
TALEP_TURLERI = {"iptal": "İptal talebi", "iade": "İade talebi"}
TALEP_DURUMLARI = {"bekliyor": "Yanıt bekleniyor", "onaylandi": "Onaylandı", "reddedildi": "Reddedildi",
                   "tamamlandi": "Tamamlandı", "geri_cekildi": "Geri çekildi"}
TALEP_NEDENLERI = {
    "iptal": ["Fikrimi değiştirdim", "Yanlış ürün / adet seçtim", "Daha uygun fiyat buldum", "Teslimat çok uzun sürecek",
              "Diğer"],
    "iade": ["Fikrimi değiştirdim", "Ürün hasarlı / kırık geldi", "Yanlış ürün gönderildi", "Ürün açıklamadaki gibi değil",
             "Diğer"],
}
AKIS = ["alindi", "hazirlaniyor", "kargoda", "teslim"]     # müşterinin gördüğü aşamalar
ODEME_SEKILLERI = {"kapida": "Kapıda ödeme", "havale": "Havale / EFT", "kart": "Kredi / banka kartı"}
ODEME_BEKLENIYOR = "odeme"   # kartla ödemede ödeme tamamlanana kadarki durum (DURUMLAR'da yok: satıcı seçemez)
BEKLEYEN_OMUR = 3600         # saniye
TESLIMAT_ALANLARI = {"ad": 60, "telefon": 25, "il": 40, "ilce": 40, "adres": 300, "not": 300}
EN_FAZLA_ADET = 10


class SiparisHatasi(Exception):
    """Kullanıcıya gösterilecek sipariş hatası."""


def simdi():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def siparis_no(siparis_id):
    return f"TS{100000 + siparis_id}"


class Siparisler:
    def __init__(self, yol):
        self.yol = yol
        with self.baglan() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS siparisler (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    musteri_id    INTEGER NOT NULL,
                    durum         TEXT NOT NULL,
                    toplam        REAL NOT NULL,
                    odeme         TEXT NOT NULL,
                    teslimat      TEXT NOT NULL,       -- JSON: ad, telefon, il, ilce, adres, not
                    kargo_firmasi TEXT NOT NULL DEFAULT '',
                    takip_no      TEXT NOT NULL DEFAULT '',
                    olusturma     TEXT NOT NULL,
                    guncelleme    TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS siparis_kalemleri (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    siparis_id  INTEGER NOT NULL REFERENCES siparisler(id) ON DELETE CASCADE,
                    urun_id     INTEGER NOT NULL,
                    ad          TEXT NOT NULL,
                    tip         TEXT NOT NULL,
                    fiyat       REAL NOT NULL,
                    adet        INTEGER NOT NULL,
                    gorsel      TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS siparis_gecmisi (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    siparis_id  INTEGER NOT NULL REFERENCES siparisler(id) ON DELETE CASCADE,
                    durum       TEXT NOT NULL,
                    aciklama    TEXT NOT NULL DEFAULT '',
                    kim         TEXT NOT NULL DEFAULT '',
                    zaman       TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS siparis_musteri ON siparisler(musteri_id);
            """)
            # Kartla ödeme sütunları (eski veritabanlarına eklenir)
            sutunlar = {r[1] for r in c.execute("PRAGMA table_info(siparisler)")}
            # İptal / iade talebi sütunları
            for ad in ("odeme_ref", "odeme_kart", "odeme_anahtar", "talep", "talep_durum", "talep_neden",
                       "talep_aciklama", "talep_iban", "talep_cevap", "talep_zaman"):
                if ad not in sutunlar:
                    c.execute(f"ALTER TABLE siparisler ADD COLUMN {ad} TEXT NOT NULL DEFAULT ''")
            c.execute("CREATE INDEX IF NOT EXISTS siparis_odeme_anahtar ON siparisler(odeme_anahtar)")

    @contextmanager
    def baglan(self):
        conn = sqlite3.connect(self.yol)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    @staticmethod
    def teslimati_dogrula(t):
        t = {k: " ".join(str((t or {}).get(k) or "").split()) if k != "adres" and k != "not"
             else str((t or {}).get(k) or "").strip() for k in TESLIMAT_ALANLARI}
        eksik = [ad for k, ad in (("ad", "ad soyad"), ("telefon", "telefon"), ("il", "il"), ("ilce", "ilçe"),
                                  ("adres", "adres")) if not t[k]]
        if eksik:
            raise SiparisHatasi("Teslimat bilgilerinde eksik var: " + ", ".join(eksik) + ".")
        for k, n in TESLIMAT_ALANLARI.items():
            if len(t[k]) > n:
                raise SiparisHatasi("Teslimat bilgilerinden biri çok uzun.")
        if sum(ch.isdigit() for ch in t["telefon"]) < 10:
            raise SiparisHatasi("Telefon numarasını eksiksiz yazın.")
        return t

    def olustur(self, musteri, kalemler, teslimat, odeme):
        """kalemler: [(urun, adet, gorsel_adresi)] — ürünler sunucudan okunmuş ve yayında olmalı."""
        if odeme not in ODEME_SEKILLERI:
            raise SiparisHatasi("Ödeme şeklini seçin.")
        if not kalemler:
            raise SiparisHatasi("Sepetiniz boş ya da sepetteki ürünler artık satışta değil.")
        teslimat = self.teslimati_dogrula(teslimat)
        toplam = round(sum(u["fiyat"] * adet for u, adet, _ in kalemler), 2)
        zaman = simdi()
        kartla = odeme == "kart"
        self.bekleyenleri_temizle()
        with self.baglan() as c:
            sid = c.execute("INSERT INTO siparisler (musteri_id, durum, toplam, odeme, teslimat, olusturma, guncelleme, "
                            "odeme_anahtar) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (musteri["id"], ODEME_BEKLENIYOR if kartla else "alindi", toplam, odeme,
                             json.dumps(teslimat, ensure_ascii=False), zaman, zaman,
                             secrets.token_urlsafe(18) if kartla else "")).lastrowid
            c.executemany("INSERT INTO siparis_kalemleri (siparis_id, urun_id, ad, tip, fiyat, adet, gorsel) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)",
                          [(sid, u["id"], u["ad"], u["tip"], u["fiyat"], adet, gorsel) for u, adet, gorsel in kalemler])
            if not kartla:   # kartla ödemede geçmiş, ödeme alınınca yazılır
                c.execute("INSERT INTO siparis_gecmisi (siparis_id, durum, aciklama, kim, zaman) VALUES (?, 'alindi', ?, ?, ?)",
                          (sid, f"{ODEME_SEKILLERI[odeme]} ile sipariş verildi.", musteri["ad"], zaman))
        return self.siparis(sid)

    # ---------------- kartla ödeme ----------------
    def odeme_anahtari(self, siparis_id):
        with self.baglan() as c:
            r = c.execute("SELECT odeme_anahtar FROM siparisler WHERE id = ?", (siparis_id,)).fetchone()
        return r[0] if r else ""

    def anahtarla_bul(self, anahtar):
        """Bankadan dönüşte siparişi bulur (dönüş isteğinde oturum çerezi gelmez)."""
        if not anahtar:
            return None
        with self.baglan() as c:
            r = c.execute("SELECT id FROM siparisler WHERE odeme_anahtar = ?", (anahtar,)).fetchone()
        return self.siparis(r[0]) if r else None

    def odeme_tamamlandi(self, siparis_id, odeme_ref, kart, kim):
        """Ödeme alındı: bekleyen sipariş 'alindi' olur. Daha önce tamamlanmışsa (çift dönüş) dokunmaz."""
        zaman = simdi()
        with self.baglan() as c:
            n = c.execute("UPDATE siparisler SET durum = 'alindi', odeme_ref = ?, odeme_kart = ?, olusturma = ?, "
                          "guncelleme = ? WHERE id = ? AND durum = ?",
                          (odeme_ref, kart, zaman, zaman, siparis_id, ODEME_BEKLENIYOR)).rowcount
            if n:
                c.execute("INSERT INTO siparis_gecmisi (siparis_id, durum, aciklama, kim, zaman) VALUES (?, 'alindi', ?, ?, ?)",
                          (siparis_id, f"Kartla ödendi ({kart}), sipariş verildi.", kim, zaman))
        return self.siparis(siparis_id), bool(n)

    def odeme_basarisiz(self, siparis_id):
        """Ödeme alınamadı: bekleyen sipariş silinir (sepet müşteride kalır, tekrar deneyebilir)."""
        with self.baglan() as c:
            if c.execute("DELETE FROM siparisler WHERE id = ? AND durum = ?", (siparis_id, ODEME_BEKLENIYOR)).rowcount:
                c.execute("DELETE FROM siparis_kalemleri WHERE siparis_id = ?", (siparis_id,))
                c.execute("DELETE FROM siparis_gecmisi WHERE siparis_id = ?", (siparis_id,))

    def teslim_alinanlar(self, musteri_id):
        """Müşterinin teslim aldığı (iade edilmemiş) ürünler: urun_id -> son teslim alınan siparişin id'si.
        Ürün değerlendirme hakkı buradan denetlenir."""
        with self.baglan() as c:
            return {r[0]: r[1] for r in c.execute(
                "SELECT k.urun_id, MAX(s.id) FROM siparis_kalemleri k JOIN siparisler s ON s.id = k.siparis_id "
                "WHERE s.musteri_id = ? AND s.durum = 'teslim' GROUP BY k.urun_id", (musteri_id,))}

    def bekleyenleri_temizle(self):
        """Banka sayfasında yarıda bırakılmış (BEKLEYEN_OMUR'dan eski) ödemeleri siler."""
        sinir = datetime.fromtimestamp(datetime.now().timestamp() - BEKLEYEN_OMUR).strftime("%Y-%m-%d %H:%M:%S")
        with self.baglan() as c:
            idler = [r[0] for r in c.execute("SELECT id FROM siparisler WHERE durum = ? AND olusturma < ?",
                                             (ODEME_BEKLENIYOR, sinir))]
            for sid in idler:
                c.execute("DELETE FROM siparis_kalemleri WHERE siparis_id = ?", (sid,))
                c.execute("DELETE FROM siparis_gecmisi WHERE siparis_id = ?", (sid,))
                c.execute("DELETE FROM siparisler WHERE id = ?", (sid,))

    def _doldur(self, c, satirlar):
        siparisler = []
        for s in satirlar:
            d = dict(s)
            d["no"] = siparis_no(d["id"])
            d["teslimat"] = json.loads(d["teslimat"])
            d["durum_adi"] = DURUMLAR.get(d["durum"], "Ödeme bekleniyor")
            d["odeme_adi"] = ODEME_SEKILLERI.get(d["odeme"], d["odeme"])
            if d.get("odeme_kart"):
                d["odeme_adi"] += f" · {d['odeme_kart']}"
            d.pop("odeme_anahtar", None)   # bankadan dönüşü doğrulayan gizli anahtar: tarayıcıya gitmez
            d["talep_adi"] = TALEP_TURLERI.get(d.get("talep") or "", "")
            d["talep_durum_adi"] = TALEP_DURUMLARI.get(d.get("talep_durum") or "", "")
            d["kalemler"] = [dict(k) for k in c.execute(
                "SELECT urun_id, ad, tip, fiyat, adet, gorsel FROM siparis_kalemleri WHERE siparis_id = ? ORDER BY id",
                (d["id"],))]
            d["adet"] = sum(k["adet"] for k in d["kalemler"])
            d["gecmis"] = [dict(g) for g in c.execute(
                "SELECT durum, aciklama, kim, zaman FROM siparis_gecmisi WHERE siparis_id = ? ORDER BY id", (d["id"],))]
            # İade hakkı: teslimden sonra IADE_SURESI_GUN gün
            teslim = next((g["zaman"] for g in d["gecmis"] if g["durum"] == "teslim"), None)
            d["iade_son"] = ""
            if teslim:
                son = datetime.strptime(teslim, "%Y-%m-%d %H:%M:%S") + timedelta(days=IADE_SURESI_GUN)
                d["iade_son"] = son.strftime("%Y-%m-%d %H:%M:%S")
            acik_talep = d.get("talep_durum") in ("bekliyor", "onaylandi")
            d["iptal_edilebilir"] = d["durum"] == "alindi" and not acik_talep
            d["iptal_talep_edilebilir"] = d["durum"] == "hazirlaniyor" and not acik_talep
            # Reddedilen ya da geri çekilen talepten sonra süre dolmadıysa yeniden talep edilebilir
            d["iade_talep_edilebilir"] = (d["durum"] == "teslim" and not acik_talep
                                          and bool(d["iade_son"]) and d["iade_son"] >= simdi())
            siparisler.append(d)
        return siparisler

    def siparis(self, siparis_id):
        with self.baglan() as c:
            liste = self._doldur(c, c.execute("SELECT * FROM siparisler WHERE id = ?", (siparis_id,)).fetchall())
        return liste[0] if liste else None

    def listele(self, musteri_id=None, durum=None):
        kosul, deger = ["durum != ?"], [ODEME_BEKLENIYOR]
        if musteri_id is not None:
            kosul.append("musteri_id = ?")
            deger.append(musteri_id)
        if durum in DURUMLAR:
            kosul.append("durum = ?")
            deger.append(durum)
        elif durum == "talepler":   # yanıt bekleyen ya da süren iptal / iade talepleri
            kosul.append("talep_durum IN ('bekliyor', 'onaylandi')")
        sorgu = "SELECT * FROM siparisler" + (" WHERE " + " AND ".join(kosul) if kosul else "") + " ORDER BY id DESC"
        with self.baglan() as c:
            return self._doldur(c, c.execute(sorgu, deger).fetchall())

    def sayilar(self):
        """Durumlara göre sipariş sayısı ve toplam ciro (iptaller hariç)."""
        with self.baglan() as c:
            sayi = dict(c.execute("SELECT durum, COUNT(*) FROM siparisler WHERE durum != ? GROUP BY durum",
                                  (ODEME_BEKLENIYOR,)).fetchall())
            ciro = c.execute("SELECT COALESCE(SUM(toplam), 0) FROM siparisler WHERE durum NOT IN ('iptal', 'iade', ?)",
                             (ODEME_BEKLENIYOR,)).fetchone()[0]
            talep = c.execute("SELECT COUNT(*) FROM siparisler WHERE talep_durum IN ('bekliyor', 'onaylandi')").fetchone()[0]
            bekleyen = c.execute("SELECT COUNT(*) FROM siparisler WHERE talep_durum = 'bekliyor'").fetchone()[0]
        return {"durumlar": {d: sayi.get(d, 0) for d in DURUMLAR}, "toplam": sum(sayi.values()), "ciro": ciro,
                "talepler": talep, "bekleyen_talep": bekleyen}

    def musteri_ozetleri(self):
        """Müşteri id -> (sipariş sayısı, toplam harcama, son sipariş)."""
        with self.baglan() as c:
            return {m: (n, t, son) for m, n, t, son in c.execute(
                "SELECT musteri_id, COUNT(*), COALESCE(SUM(CASE WHEN durum NOT IN ('iptal', 'iade') THEN toplam END), 0), "
                "MAX(olusturma) FROM siparisler WHERE durum != ? GROUP BY musteri_id", (ODEME_BEKLENIYOR,))}

    def durum_degistir(self, siparis_id, durum, kim, aciklama="", kargo_firmasi=None, takip_no=None):
        if durum not in DURUMLAR:
            raise SiparisHatasi("Geçersiz durum.")
        s = self.siparis(siparis_id)
        if s is None or s["durum"] == ODEME_BEKLENIYOR:
            raise SiparisHatasi("Sipariş bulunamadı.")
        if durum == "iade" and s["durum"] != "iade":
            raise SiparisHatasi("İade, müşterinin iade talebi üzerinden tamamlanır.")
        if s["durum"] in SON_DURUMLAR and durum != s["durum"]:
            durum_adi = s["durum_adi"].replace("İ", "i").replace("I", "ı").lower()   # Türkçe küçük harf
            raise SiparisHatasi(f"Sipariş {durum_adi}; durumu artık değiştirilemez.")
        kargo_firmasi = s["kargo_firmasi"] if kargo_firmasi is None else " ".join(str(kargo_firmasi).split())[:60]
        takip_no = s["takip_no"] if takip_no is None else " ".join(str(takip_no).split())[:60]
        if durum == "kargoda" and not (kargo_firmasi and takip_no):
            raise SiparisHatasi("Kargoya verirken kargo firmasını ve takip numarasını yazın.")
        aciklama = " ".join(str(aciklama or "").split())[:300]
        if durum == "kargoda" and s["durum"] != "kargoda":
            aciklama = (f"{kargo_firmasi}, takip no: {takip_no}. " + aciklama).strip()
        zaman = simdi()
        with self.baglan() as c:
            c.execute("UPDATE siparisler SET durum = ?, kargo_firmasi = ?, takip_no = ?, guncelleme = ? WHERE id = ?",
                      (durum, kargo_firmasi, takip_no, zaman, siparis_id))
            if durum != s["durum"] or aciklama:
                c.execute("INSERT INTO siparis_gecmisi (siparis_id, durum, aciklama, kim, zaman) VALUES (?, ?, ?, ?, ?)",
                          (siparis_id, durum, aciklama, kim, zaman))
            # Yanıt bekleyen iptal talebi varken satıcı siparişi kendisi iptal ettiyse ya da kargoya verdiyse talep kapanır
            if s.get("talep") == "iptal" and s.get("talep_durum") == "bekliyor" and durum != s["durum"]:
                iptal = durum == "iptal"
                c.execute("UPDATE siparisler SET talep_durum = ?, talep_cevap = ? WHERE id = ?",
                          ("tamamlandi" if iptal else "reddedildi",
                           "" if iptal else f"Sipariş {DURUMLAR[durum].lower()} aşamasına geçti.", siparis_id))
        return self.siparis(siparis_id)

    def _musterinin(self, siparis_id, musteri):
        s = self.siparis(siparis_id)
        if s is None or s["musteri_id"] != musteri["id"] or s["durum"] == ODEME_BEKLENIYOR:
            raise SiparisHatasi("Sipariş bulunamadı.")
        return s

    @staticmethod
    def _neden_dogrula(tur, neden, aciklama):
        neden = " ".join(str(neden or "").split())
        aciklama = " ".join(str(aciklama or "").split())[:500]
        if neden not in TALEP_NEDENLERI[tur]:
            raise SiparisHatasi("Lütfen bir sebep seçin.")
        if neden == "Diğer" and len(aciklama) < 5:
            raise SiparisHatasi("Sebebi kısaca açıklayın.")
        return neden, aciklama

    def musteri_iptal(self, siparis_id, musteri, neden="", aciklama=""):
        """Müşteri, siparişi hazırlanmaya başlamadan hemen iptal edebilir (sonrasında iptal talebi gönderir)."""
        s = self._musterinin(siparis_id, musteri)
        if s["durum"] != "alindi":
            raise SiparisHatasi("Bu sipariş hazırlanmaya başladı; iptal talebi gönderebilirsiniz.")
        metin = "Müşteri tarafından iptal edildi."
        if neden:
            neden, aciklama = self._neden_dogrula("iptal", neden, aciklama)
            metin = f"Müşteri tarafından iptal edildi: {neden}" + (f" — {aciklama}" if aciklama else "") + "."
        return self.durum_degistir(siparis_id, "iptal", musteri["ad"], metin)

    def talep_olustur(self, siparis_id, musteri, tur, neden, aciklama="", iban=""):
        """İptal talebi (hazırlanırken) ya da iade talebi (teslimden sonra IADE_SURESI_GUN içinde)."""
        if tur not in TALEP_TURLERI:
            raise SiparisHatasi("Geçersiz talep.")
        s = self._musterinin(siparis_id, musteri)
        if tur == "iptal" and not s["iptal_talep_edilebilir"]:
            raise SiparisHatasi("Bu sipariş için şu an iptal talebi gönderilemez."
                                + (" Sipariş kargoya verildi; teslim aldıktan sonra iade talebi oluşturabilirsiniz."
                                   if s["durum"] == "kargoda" else ""))
        if tur == "iade" and not s["iade_talep_edilebilir"]:
            if s["durum"] == "teslim" and s["iade_son"] and s["iade_son"] < simdi():
                raise SiparisHatasi(f"İade süresi ({IADE_SURESI_GUN} gün) doldu.")
            if s["durum"] in ("alindi", "hazirlaniyor", "kargoda"):
                raise SiparisHatasi("Siparişiniz henüz teslim edilmedi; teslim aldıktan sonra iade talebi oluşturabilirsiniz.")
            raise SiparisHatasi("Bu sipariş için şu an iade talebi gönderilemez.")
        neden, aciklama = self._neden_dogrula(tur, neden, aciklama)
        iban = re.sub(r"\s", "", str(iban or "")).upper()
        if tur == "iade" and s["odeme"] != "kart":
            # Kartla ödenmediyse para iadesi havaleyle yapılır
            if not re.fullmatch(r"TR\d{24}", iban):
                raise SiparisHatasi("Para iadesi için IBAN'ınızı eksiksiz yazın (TR ile başlayan 26 karakter).")
        else:
            iban = ""
        zaman = simdi()
        metin = f"{TALEP_TURLERI[tur]}: {neden}" + (f" — {aciklama}" if aciklama else "")
        with self.baglan() as c:
            c.execute("UPDATE siparisler SET talep = ?, talep_durum = 'bekliyor', talep_neden = ?, talep_aciklama = ?, "
                      "talep_iban = ?, talep_cevap = '', talep_zaman = ?, guncelleme = ? WHERE id = ?",
                      (tur, neden, aciklama, iban, zaman, zaman, siparis_id))
            c.execute("INSERT INTO siparis_gecmisi (siparis_id, durum, aciklama, kim, zaman) VALUES (?, ?, ?, ?, ?)",
                      (siparis_id, s["durum"], metin, musteri["ad"], zaman))
        return self.siparis(siparis_id)

    def talep_geri_cek(self, siparis_id, musteri):
        s = self._musterinin(siparis_id, musteri)
        if s["talep_durum"] != "bekliyor":
            raise SiparisHatasi("Yanıt bekleyen bir talebiniz yok.")
        self._talep_yaz(s, "geri_cekildi", "", musteri["ad"], f"{s['talep_adi']} müşteri tarafından geri çekildi.")
        return self.siparis(siparis_id)

    def _talep_yaz(self, s, talep_durum, cevap, kim, gecmis_metni, durum=None):
        zaman = simdi()
        with self.baglan() as c:
            c.execute("UPDATE siparisler SET talep_durum = ?, talep_cevap = ?, durum = ?, guncelleme = ? WHERE id = ?",
                      (talep_durum, cevap, durum or s["durum"], zaman, s["id"]))
            c.execute("INSERT INTO siparis_gecmisi (siparis_id, durum, aciklama, kim, zaman) VALUES (?, ?, ?, ?, ?)",
                      (s["id"], durum or s["durum"], gecmis_metni, kim, zaman))

    def talep_karar(self, siparis_id, karar, kim, notu=""):
        """Satıcının kararı: onayla / reddet / tamamla (iade: ürün geri geldi, para iade edildi).
        Para iadesi bu çağrıdan önce sunucuda yapılır; burada sadece durum ve geçmiş yazılır."""
        s = self.siparis(siparis_id)
        if s is None or not s.get("talep"):
            raise SiparisHatasi("Bu siparişte talep yok.")
        notu = " ".join(str(notu or "").split())[:500]
        tur = s["talep"]
        if karar == "reddet":
            if s["talep_durum"] != "bekliyor":
                raise SiparisHatasi("Bu talep yanıt beklemiyor.")
            if len(notu) < 5:
                raise SiparisHatasi("Reddetme sebebini müşterinin göreceği şekilde yazın.")
            self._talep_yaz(s, "reddedildi", notu, kim, f"{s['talep_adi']} reddedildi: {notu}")
        elif karar == "onayla":
            if s["talep_durum"] != "bekliyor":
                raise SiparisHatasi("Bu talep yanıt beklemiyor.")
            if tur == "iptal":
                self._talep_yaz(s, "tamamlandi", notu, kim, "İptal talebi onaylandı, sipariş iptal edildi."
                                + (f" {notu}" if notu else ""), durum="iptal")
            else:
                if len(notu) < 5:
                    raise SiparisHatasi("Müşteriye ürünü nasıl geri göndereceğini yazın (kargo firması, adres / kod).")
                self._talep_yaz(s, "onaylandi", notu, kim, f"İade talebi onaylandı. {notu}")
        elif karar == "tamamla":
            if tur != "iade" or s["talep_durum"] != "onaylandi":
                raise SiparisHatasi("Önce iade talebini onaylayın.")
            self._talep_yaz(s, "tamamlandi", s["talep_cevap"], kim, "Ürün geri alındı, iade tamamlandı."
                            + (f" {notu}" if notu else ""), durum="iade")
        else:
            raise SiparisHatasi("Geçersiz karar.")
        return self.siparis(siparis_id)
