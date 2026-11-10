"""Canlı destek (veri/magaza/destek.db): mağazadaki ziyaretçi ile satıcılar arasındaki sohbetler.

Ziyaretçi (üye olsun olmasın) tarayıcısındaki gizli bir anahtarla (çerez) kendi sohbetine bağlanır. Sohbet ziyaretçi ya
da satıcı bitirince, ya da ZAMAN_ASIMI boyunca kimse yazmazsa kapanır; kapanınca ziyaretçi 1-5 puan ve isteğe bağlı
yorumla oylar. Satıcılar bütün sohbetleri panelden görür ve cevaplar.
"""
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

ZAMAN_ASIMI = 10 * 60          # saniye: bu kadar mesaj gelmezse sohbet kapanır
MESAJ_UZUNLUGU = 1000
KAPANIS = {
    "musteri": "Ziyaretçi sohbeti bitirdi.",
    "satici": "Sohbet destek ekibi tarafından sonlandırıldı.",
    "zaman_asimi": "10 dakikadır mesaj gelmediği için sohbet kapatıldı.",
}


class DestekHatasi(Exception):
    """Kullanıcıya gösterilecek destek hatası."""


def simdi():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Destek:
    def __init__(self, yol):
        self.yol = yol
        with self.baglan() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS sohbetler (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    anahtar       TEXT NOT NULL UNIQUE,     -- ziyaretçinin çerezindeki gizli anahtar
                    musteri_id    INTEGER,                  -- üye ise
                    ad            TEXT NOT NULL,
                    eposta        TEXT NOT NULL DEFAULT '',
                    sayfa         TEXT NOT NULL DEFAULT '', -- sohbeti başlattığı sayfa
                    durum         TEXT NOT NULL DEFAULT 'acik',
                    kapanis       TEXT,                     -- musteri / satici / zaman_asimi
                    satici_id     INTEGER,                  -- ilk cevap veren satıcı
                    satici_ad     TEXT NOT NULL DEFAULT '',
                    satici_okudu  INTEGER NOT NULL DEFAULT 0,  -- satıcıların okuduğu son mesaj id
                    puan          INTEGER,
                    yorum         TEXT NOT NULL DEFAULT '',
                    olusturma     TEXT NOT NULL,
                    son_mesaj     TEXT NOT NULL,            -- son insan mesajı (zaman aşımı buna göre)
                    kapanma       TEXT
                );
                CREATE TABLE IF NOT EXISTS destek_mesajlari (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    sohbet_id  INTEGER NOT NULL REFERENCES sohbetler(id) ON DELETE CASCADE,
                    gonderen   TEXT NOT NULL,     -- musteri / satici / sistem
                    ad         TEXT NOT NULL DEFAULT '',
                    metin      TEXT NOT NULL,
                    zaman      TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS destek_mesaj_sohbet ON destek_mesajlari(sohbet_id);
            """)

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
    def _metin(metin):
        metin = str(metin or "").strip()
        if not metin:
            raise DestekHatasi("Mesaj boş olamaz.")
        if len(metin) > MESAJ_UZUNLUGU:
            raise DestekHatasi(f"Mesaj en fazla {MESAJ_UZUNLUGU} karakter olabilir.")
        return metin

    def _ozet(self, c, s, mesajlar=False):
        d = dict(s)
        d.pop("anahtar", None)
        son = c.execute("SELECT gonderen, metin, zaman FROM destek_mesajlari WHERE sohbet_id = ? AND gonderen != 'sistem' "
                        "ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
        d["son"] = dict(son) if son else None
        d["okunmamis"] = c.execute("SELECT COUNT(*) FROM destek_mesajlari WHERE sohbet_id = ? AND gonderen = 'musteri' "
                                   "AND id > ?", (d["id"], d["satici_okudu"])).fetchone()[0]
        if mesajlar:
            d["mesajlar"] = [dict(m) for m in c.execute(
                "SELECT id, gonderen, ad, metin, zaman FROM destek_mesajlari WHERE sohbet_id = ? ORDER BY id", (d["id"],))]
        return d

    def sohbet(self, sohbet_id, mesajlar=True):
        with self.baglan() as c:
            s = c.execute("SELECT * FROM sohbetler WHERE id = ?", (sohbet_id,)).fetchone()
            return self._ozet(c, s, mesajlar) if s else None

    def anahtarla(self, anahtar):
        """Ziyaretçinin çerezindeki anahtara ait sohbet (açık ya da en son kapanan)."""
        if not anahtar:
            return None
        with self.baglan() as c:
            s = c.execute("SELECT * FROM sohbetler WHERE anahtar = ?", (anahtar,)).fetchone()
            return self._ozet(c, s, True) if s else None

    def baslat(self, ad, eposta, metin, musteri=None, sayfa=""):
        ad = " ".join(str(ad or "").split())[:60] or (musteri["ad"] if musteri else "")
        if not ad:
            raise DestekHatasi("Adınızı yazın.")
        metin = self._metin(metin)
        anahtar = secrets.token_urlsafe(24)
        zaman = simdi()
        with self.baglan() as c:
            sid = c.execute("INSERT INTO sohbetler (anahtar, musteri_id, ad, eposta, sayfa, olusturma, son_mesaj) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (anahtar, musteri["id"] if musteri else None, ad, str(eposta or "").strip()[:100],
                             str(sayfa or "")[:200], zaman, zaman)).lastrowid
            c.execute("INSERT INTO destek_mesajlari (sohbet_id, gonderen, ad, metin, zaman) VALUES (?, 'musteri', ?, ?, ?)",
                      (sid, ad, metin, zaman))
            c.execute("INSERT INTO destek_mesajlari (sohbet_id, gonderen, ad, metin, zaman) VALUES (?, 'sistem', '', ?, ?)",
                      (sid, "Mesajınız alındı. Destek ekibimiz en kısa sürede size yazacak.", zaman))
        return anahtar, self.sohbet(sid)

    def mesaj_ekle(self, sohbet_id, gonderen, ad, metin, satici_id=None):
        """Mesajı ekler; sohbet kapalıysa hata. Satıcının ilk cevabında sohbet ona atanır."""
        metin = self._metin(metin)
        zaman = simdi()
        with self.baglan() as c:
            s = c.execute("SELECT durum, satici_id FROM sohbetler WHERE id = ?", (sohbet_id,)).fetchone()
            if s is None:
                raise DestekHatasi("Sohbet bulunamadı.")
            if s["durum"] != "acik":
                raise DestekHatasi("Bu sohbet kapandı.")
            mid = c.execute("INSERT INTO destek_mesajlari (sohbet_id, gonderen, ad, metin, zaman) VALUES (?, ?, ?, ?, ?)",
                            (sohbet_id, gonderen, ad, metin, zaman)).lastrowid
            c.execute("UPDATE sohbetler SET son_mesaj = ? WHERE id = ?", (zaman, sohbet_id))
            if gonderen == "satici":
                # Satıcı yazdıysa önceki mesajları da görmüştür
                c.execute("UPDATE sohbetler SET satici_okudu = ? WHERE id = ?", (mid, sohbet_id))
                if s["satici_id"] is None:
                    c.execute("UPDATE sohbetler SET satici_id = ?, satici_ad = ? WHERE id = ?", (satici_id, ad, sohbet_id))
        return {"id": mid, "gonderen": gonderen, "ad": ad, "metin": metin, "zaman": zaman}

    def okundu(self, sohbet_id):
        with self.baglan() as c:
            c.execute("UPDATE sohbetler SET satici_okudu = COALESCE((SELECT MAX(id) FROM destek_mesajlari "
                      "WHERE sohbet_id = ?), 0) WHERE id = ?", (sohbet_id, sohbet_id))

    def kapat(self, sohbet_id, neden):
        """Sohbeti kapatır, kapanış nedenini sistem mesajı olarak ekler. Zaten kapalıysa None."""
        zaman = simdi()
        with self.baglan() as c:
            n = c.execute("UPDATE sohbetler SET durum = 'kapali', kapanis = ?, kapanma = ? WHERE id = ? AND durum = 'acik'",
                          (neden, zaman, sohbet_id)).rowcount
            if not n:
                return None
            mid = c.execute("INSERT INTO destek_mesajlari (sohbet_id, gonderen, ad, metin, zaman) VALUES (?, 'sistem', '', ?, ?)",
                            (sohbet_id, KAPANIS[neden], zaman)).lastrowid
        return {"id": mid, "gonderen": "sistem", "ad": "", "metin": KAPANIS[neden], "zaman": zaman}

    def sil(self, sohbet_id):
        """Kapanmış sohbeti mesajlarıyla birlikte siler (açık sohbet silinmez, önce kapatılmalı)."""
        with self.baglan() as c:
            s = c.execute("SELECT durum FROM sohbetler WHERE id = ?", (sohbet_id,)).fetchone()
            if s is None:
                raise DestekHatasi("Sohbet bulunamadı.")
            if s["durum"] != "kapali":
                raise DestekHatasi("Açık sohbet silinemez; önce sohbeti kapatın.")
            c.execute("DELETE FROM destek_mesajlari WHERE sohbet_id = ?", (sohbet_id,))
            c.execute("DELETE FROM sohbetler WHERE id = ?", (sohbet_id,))

    def oyla(self, anahtar, puan, yorum):
        try:
            puan = int(puan)
        except (TypeError, ValueError):
            puan = 0
        if not 1 <= puan <= 5:
            raise DestekHatasi("1 ile 5 arasında bir puan seçin.")
        with self.baglan() as c:
            s = c.execute("SELECT id, durum, puan FROM sohbetler WHERE anahtar = ?", (anahtar,)).fetchone()
            if s is None:
                raise DestekHatasi("Sohbet bulunamadı.")
            if s["durum"] != "kapali":
                raise DestekHatasi("Sohbet bitmeden oylanamaz.")
            if s["puan"] is not None:
                raise DestekHatasi("Bu sohbeti zaten oyladınız.")
            c.execute("UPDATE sohbetler SET puan = ?, yorum = ? WHERE id = ?", (puan, str(yorum or "").strip()[:500], s["id"]))
        return self.sohbet(s["id"])

    def listele(self, durum=None, sinir=200):
        kosul = "WHERE durum = ?" if durum in ("acik", "kapali") else ""
        with self.baglan() as c:
            satirlar = c.execute(f"SELECT * FROM sohbetler {kosul} ORDER BY son_mesaj DESC LIMIT ?",
                                 ((durum, sinir) if kosul else (sinir,))).fetchall()
            return [self._ozet(c, s) for s in satirlar]

    def sayilar(self):
        with self.baglan() as c:
            acik = c.execute("SELECT COUNT(*) FROM sohbetler WHERE durum = 'acik'").fetchone()[0]
            okunmamis = c.execute(
                "SELECT COUNT(*) FROM destek_mesajlari m JOIN sohbetler s ON s.id = m.sohbet_id "
                "WHERE s.durum = 'acik' AND m.gonderen = 'musteri' AND m.id > s.satici_okudu").fetchone()[0]
            puan = c.execute("SELECT AVG(puan), COUNT(puan) FROM sohbetler WHERE puan IS NOT NULL").fetchone()
        return {"acik": acik, "okunmamis": okunmamis, "ortalama_puan": round(puan[0], 1) if puan[0] else None,
                "oy_sayisi": puan[1]}

    def zaman_asimlari(self):
        """ZAMAN_ASIMI'dır mesaj gelmeyen açık sohbetleri kapatır: [(sohbet id, sistem mesajı)]."""
        sinir = (datetime.now() - timedelta(seconds=ZAMAN_ASIMI)).strftime("%Y-%m-%d %H:%M:%S")
        with self.baglan() as c:
            idler = [r[0] for r in c.execute("SELECT id FROM sohbetler WHERE durum = 'acik' AND son_mesaj <= ?", (sinir,))]
        kapanan = []
        for sid in idler:
            m = self.kapat(sid, "zaman_asimi")
            if m:
                kapanan.append((sid, m))
        return kapanan
