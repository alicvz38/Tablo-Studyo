"""Üyelerin sepeti ve favorileri (veri/magaza/sepet.db): hesaba bağlıdır, her üye sadece kendininkini görür ve
başka cihazdan girince de aynısını bulur. Giriş yapmamış ziyaretçinin sepeti tarayıcısında kalır; giriş yapınca
üyenin sepetine eklenir (magaza.js).

Sadece ürün kimliği ve adet saklanır; ad, fiyat ve görsel her okunuşta güncel üründen gelir. Tarayıcı her
değişiklikte listenin tamamını gönderir (sürüm numarasıyla: art arda gelen istekler sırasız ulaşırsa eskisi yeniyi
ezmez).
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

EN_FAZLA_KALEM = 50     # sepette / favorilerde en fazla bu kadar ürün
EN_FAZLA_ADET = 10


class Sepetler:
    def __init__(self, yol):
        self.yol = yol
        with self.baglan() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS sepet (
                    kullanici_id INTEGER NOT NULL,
                    urun_id      INTEGER NOT NULL,
                    adet         INTEGER NOT NULL,
                    sira         INTEGER NOT NULL,
                    PRIMARY KEY (kullanici_id, urun_id)
                );
                CREATE TABLE IF NOT EXISTS favori (
                    kullanici_id INTEGER NOT NULL,
                    urun_id      INTEGER NOT NULL,
                    sira         INTEGER NOT NULL,
                    PRIMARY KEY (kullanici_id, urun_id)
                );
                CREATE TABLE IF NOT EXISTS surum (
                    kullanici_id INTEGER NOT NULL,
                    tur          TEXT NOT NULL,       -- 'sepet' | 'favori'
                    surum        REAL NOT NULL,
                    guncelleme   TEXT NOT NULL,
                    PRIMARY KEY (kullanici_id, tur)
                );
            """)

    @contextmanager
    def baglan(self):
        conn = sqlite3.connect(self.yol, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def oku(self, kullanici_id):
        """(sepet: [(urun_id, adet)], favoriler: [urun_id]) eklenme sırasıyla."""
        with self.baglan() as c:
            sepet = c.execute("SELECT urun_id, adet FROM sepet WHERE kullanici_id = ? ORDER BY sira",
                              (kullanici_id,)).fetchall()
            favori = [r[0] for r in c.execute("SELECT urun_id FROM favori WHERE kullanici_id = ? ORDER BY sira",
                                              (kullanici_id,))]
        return sepet, favori

    def _surum_gecerli(self, c, kullanici_id, tur, surum):
        """Gelen liste kayıtlı olandan yeniyse sürümü yazar ve True döner (eski istek yok sayılır)."""
        if surum is None:
            return True
        r = c.execute("SELECT surum FROM surum WHERE kullanici_id = ? AND tur = ?", (kullanici_id, tur)).fetchone()
        if r and float(surum) < r[0]:
            return False
        c.execute("INSERT INTO surum (kullanici_id, tur, surum, guncelleme) VALUES (?, ?, ?, ?) "
                  "ON CONFLICT(kullanici_id, tur) DO UPDATE SET surum = excluded.surum, guncelleme = excluded.guncelleme",
                  (kullanici_id, tur, float(surum), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        return True

    def sepet_yaz(self, kullanici_id, kalemler, surum=None):
        """kalemler: [(urun_id, adet)] — sepetin tamamı (sıra korunur). Eski sürümse dokunmaz."""
        temiz, gorulen = [], set()
        for uid, adet in kalemler:
            if uid in gorulen:
                continue
            gorulen.add(uid)
            temiz.append((uid, max(1, min(EN_FAZLA_ADET, adet))))
        with self.baglan() as c:
            if not self._surum_gecerli(c, kullanici_id, "sepet", surum):
                return False
            c.execute("DELETE FROM sepet WHERE kullanici_id = ?", (kullanici_id,))
            c.executemany("INSERT INTO sepet (kullanici_id, urun_id, adet, sira) VALUES (?, ?, ?, ?)",
                          [(kullanici_id, uid, adet, i) for i, (uid, adet) in enumerate(temiz[:EN_FAZLA_KALEM])])
        return True

    def favori_yaz(self, kullanici_id, urun_idler, surum=None):
        temiz = list(dict.fromkeys(urun_idler))[:EN_FAZLA_KALEM]
        with self.baglan() as c:
            if not self._surum_gecerli(c, kullanici_id, "favori", surum):
                return False
            c.execute("DELETE FROM favori WHERE kullanici_id = ?", (kullanici_id,))
            c.executemany("INSERT INTO favori (kullanici_id, urun_id, sira) VALUES (?, ?, ?)",
                          [(kullanici_id, uid, i) for i, uid in enumerate(temiz)])
        return True

    def sepetten_cikar(self, kullanici_id, urun_idler):
        """Sipariş verilen ürünler sepetten çıkar (tarayıcı sipariş anında başka sayfaya geçse de)."""
        with self.baglan() as c:
            c.executemany("DELETE FROM sepet WHERE kullanici_id = ? AND urun_id = ?",
                          [(kullanici_id, uid) for uid in urun_idler])
