"""Ürün değerlendirmeleri (veri/magaza/degerlendirme.db): yıldız (1-5) ve yorum.

Sadece ürünü satın alıp teslim almış müşteri değerlendirebilir (sunucu siparişlerden denetler; iade edilen
sipariş sayılmaz). Her müşteri bir ürünü bir kez değerlendirir, sonradan düzenleyip silebilir. Satıcı yoruma herkesin
gördüğü bir cevap yazabilir ve uygunsuz yorumu gizleyebilir (gizli yorum puan ortalamasına da girmez).
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

YORUM_EN_FAZLA = 1000
CEVAP_EN_FAZLA = 600


class DegerlendirmeHatasi(Exception):
    """Kullanıcıya gösterilecek hata."""


def simdi():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def gizli_ad(ad):
    """Herkese açık listede ad gizlenir: "Ayşe Yılmaz" -> "Ayşe Y."."""
    parca = (ad or "").split()
    if not parca:
        return "Müşteri"
    return parca[0] if len(parca) == 1 else f"{parca[0]} {parca[-1][0]}."


class Degerlendirmeler:
    def __init__(self, yol):
        self.yol = yol
        with self.baglan() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS degerlendirmeler (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    urun_id      INTEGER NOT NULL,
                    musteri_id   INTEGER NOT NULL,
                    musteri_adi  TEXT NOT NULL,
                    puan         INTEGER NOT NULL CHECK (puan BETWEEN 1 AND 5),
                    yorum        TEXT NOT NULL DEFAULT '',
                    gorunur      INTEGER NOT NULL DEFAULT 1,
                    satici_cevap TEXT NOT NULL DEFAULT '',
                    cevap_zaman  TEXT NOT NULL DEFAULT '',
                    olusturma    TEXT NOT NULL,
                    guncelleme   TEXT NOT NULL,
                    UNIQUE (urun_id, musteri_id)
                );
                CREATE INDEX IF NOT EXISTS degerlendirme_urun ON degerlendirmeler(urun_id);
            """)

    @contextmanager
    def baglan(self):
        conn = sqlite3.connect(self.yol, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def ozetler(self, urun_idler=None):
        """urun_id -> (ortalama, sayı); sadece görünür değerlendirmeler. urun_idler None ise hepsi."""
        with self.baglan() as c:
            satirlar = c.execute("SELECT urun_id, AVG(puan), COUNT(*) FROM degerlendirmeler WHERE gorunur = 1 "
                                 "GROUP BY urun_id").fetchall()
        sonuc = {r[0]: (round(r[1], 1), r[2]) for r in satirlar}
        if urun_idler is not None:
            sonuc = {u: sonuc[u] for u in urun_idler if u in sonuc}
        return sonuc

    def urun_icin(self, urun_id, musteri_id=None):
        """Ürün sayfası: ortalama, dağılım, görünür yorumlar (adlar gizli) ve (varsa) müşterinin kendi
        değerlendirmesi (gizlenmiş olsa da kendisi görür)."""
        with self.baglan() as c:
            satirlar = c.execute("SELECT * FROM degerlendirmeler WHERE urun_id = ? AND gorunur = 1 "
                                 "ORDER BY olusturma DESC", (urun_id,)).fetchall()
            benim = c.execute("SELECT * FROM degerlendirmeler WHERE urun_id = ? AND musteri_id = ?",
                              (urun_id, musteri_id)).fetchone() if musteri_id else None
        dagilim = {p: 0 for p in range(5, 0, -1)}
        for r in satirlar:
            dagilim[r["puan"]] += 1
        sayi = len(satirlar)
        return {
            "ortalama": round(sum(r["puan"] for r in satirlar) / sayi, 1) if sayi else 0,
            "sayi": sayi,
            "dagilim": dagilim,
            "liste": [{"id": r["id"], "ad": gizli_ad(r["musteri_adi"]), "puan": r["puan"], "yorum": r["yorum"],
                       "tarih": r["olusturma"], "duzenlendi": r["guncelleme"] != r["olusturma"],
                       "satici_cevap": r["satici_cevap"], "cevap_zaman": r["cevap_zaman"],
                       "benim": bool(musteri_id and r["musteri_id"] == musteri_id)} for r in satirlar],
            "benim": ({"puan": benim["puan"], "yorum": benim["yorum"], "gorunur": bool(benim["gorunur"]),
                       "satici_cevap": benim["satici_cevap"]} if benim else None),
        }

    def kaydet(self, urun_id, musteri, puan, yorum):
        """Değerlendirme ekler ya da müşterinin önceki değerlendirmesini günceller (hak denetimi sunucuda)."""
        try:
            puan = int(puan)
        except (TypeError, ValueError):
            puan = 0
        if not 1 <= puan <= 5:
            raise DegerlendirmeHatasi("Lütfen 1 ile 5 arasında yıldız verin.")
        yorum = "\n".join(" ".join(s.split()) for s in str(yorum or "").strip().splitlines()).strip()
        if len(yorum) > YORUM_EN_FAZLA:
            raise DegerlendirmeHatasi(f"Yorum en fazla {YORUM_EN_FAZLA} karakter olabilir.")
        zaman = simdi()
        with self.baglan() as c:
            onceki = c.execute("SELECT id FROM degerlendirmeler WHERE urun_id = ? AND musteri_id = ?",
                               (urun_id, musteri["id"])).fetchone()
            if onceki:
                c.execute("UPDATE degerlendirmeler SET puan = ?, yorum = ?, musteri_adi = ?, guncelleme = ? WHERE id = ?",
                          (puan, yorum, musteri["ad"], zaman, onceki["id"]))
                return onceki["id"], False
            yeni = c.execute("INSERT INTO degerlendirmeler (urun_id, musteri_id, musteri_adi, puan, yorum, olusturma, "
                             "guncelleme) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (urun_id, musteri["id"], musteri["ad"], puan, yorum, zaman, zaman)).lastrowid
            return yeni, True

    def sil(self, urun_id, musteri_id):
        with self.baglan() as c:
            if not c.execute("DELETE FROM degerlendirmeler WHERE urun_id = ? AND musteri_id = ?",
                             (urun_id, musteri_id)).rowcount:
                raise DegerlendirmeHatasi("Değerlendirme bulunamadı.")

    # ---------------- panel ----------------
    def listele(self, urun_idler):
        """Satıcının ürünlerinin bütün değerlendirmeleri (gizliler dahil, adlar açık), en yeni önce."""
        if not urun_idler:
            return []
        soru = ",".join("?" * len(urun_idler))
        with self.baglan() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM degerlendirmeler WHERE urun_id IN ({soru}) "
                                               "ORDER BY olusturma DESC", list(urun_idler))]

    def degerlendirme(self, did):
        with self.baglan() as c:
            r = c.execute("SELECT * FROM degerlendirmeler WHERE id = ?", (did,)).fetchone()
        return dict(r) if r else None

    def cevapla(self, did, cevap):
        cevap = " ".join(str(cevap or "").split())[:CEVAP_EN_FAZLA]
        with self.baglan() as c:
            c.execute("UPDATE degerlendirmeler SET satici_cevap = ?, cevap_zaman = ? WHERE id = ?",
                      (cevap, simdi() if cevap else "", did))
        return self.degerlendirme(did)

    def gorunurluk(self, did, gorunur):
        with self.baglan() as c:
            c.execute("UPDATE degerlendirmeler SET gorunur = ? WHERE id = ?", (1 if gorunur else 0, did))
        return self.degerlendirme(did)
