"""Kullanıcı hesapları ve oturumlar (veri/hesaplar.db).

İki rol vardır:
  satici   panele girer (odalar, ürünler, siparişler). Yeni satıcıyı sadece bir satıcı panelden ekler;
           hiç hesap yokken açılan ilk hesap satıcıdır.
  musteri  mağazadan üye olur, sipariş verir ve siparişlerini takip eder.
Herkes kullanıcı adı ya da e-postasıyla giriş yapar; ikisi de sonradan değiştirilebilir (müşteride e-posta zorunlu).

Şifreler tuzlanmış PBKDF2-SHA256 özeti olarak, oturum anahtarları da SHA-256 özeti olarak saklanır;
veritabanı ele geçse bile şifreler ve açık oturumlar doğrudan kullanılamaz.
"""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from datetime import datetime

YINELEME = 240_000
OTURUM_SURESI = 30 * 24 * 3600   # saniye
KULLANICI_ADI_KALIBI = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
EPOSTA_KALIBI = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
EN_KISA_SIFRE = 6
ROLLER = {"satici": "Satıcı", "musteri": "Müşteri"}


class HesapHatasi(Exception):
    """Kullanıcıya gösterilecek kayıt / giriş hatası."""


def sifre_ozeti(sifre, tuz=None):
    tuz = tuz or secrets.token_hex(16)
    ozet = hashlib.pbkdf2_hmac("sha256", sifre.encode(), tuz.encode(), YINELEME).hex()
    return f"pbkdf2_sha256${YINELEME}${tuz}${ozet}"


def sifre_dogru(sifre, kayitli):
    try:
        _, yineleme, tuz, ozet = kayitli.split("$")
    except ValueError:
        return False
    hesap = hashlib.pbkdf2_hmac("sha256", sifre.encode(), tuz.encode(), int(yineleme)).hex()
    return hmac.compare_digest(hesap, ozet)


def anahtar_ozeti(anahtar):
    return hashlib.sha256(anahtar.encode()).hexdigest()


# Olmayan kullanıcıda da aynı süre harcansın (kullanıcı adı var mı yok mu süreden anlaşılmasın)
_SAHTE_OZET = sifre_ozeti("sahte-sifre")


class Hesaplar:
    def __init__(self, yol):
        self.yol = yol
        with self._baglan() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kullanicilar (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    kullanici_adi  TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    ad             TEXT NOT NULL,
                    sifre          TEXT NOT NULL,
                    kayit_tarihi   TEXT NOT NULL,
                    son_giris      TEXT
                );
                CREATE TABLE IF NOT EXISTS oturumlar (
                    anahtar        TEXT PRIMARY KEY,    -- oturum anahtarının SHA-256 özeti
                    kullanici_id   INTEGER NOT NULL REFERENCES kullanicilar(id) ON DELETE CASCADE,
                    bitis          REAL NOT NULL
                );
            """)
            # Üyelik sisteminden önceki hesaplar satıcıdır (panelin sahibi)
            mevcut = {s[1] for s in conn.execute("PRAGMA table_info(kullanicilar)")}
            for sutun, tanim in (("rol", "TEXT NOT NULL DEFAULT 'satici'"), ("aktif", "INTEGER NOT NULL DEFAULT 1"),
                                 ("telefon", "TEXT NOT NULL DEFAULT ''"),
                                 ("adres", "TEXT")):   # JSON: son kullanılan teslimat adresi
                if sutun not in mevcut:
                    conn.execute(f"ALTER TABLE kullanicilar ADD COLUMN {sutun} {tanim}")
            # E-posta ayrı alan: herkes kullanıcı adı ya da e-postayla giriş yapar
            if "eposta" not in mevcut:
                conn.execute("ALTER TABLE kullanicilar ADD COLUMN eposta TEXT")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS kullanicilar_eposta ON kullanicilar(eposta COLLATE NOCASE)")
            # Önceki sürümde müşterinin kullanıcı adı e-postasıydı: e-posta kendi alanına taşınır, kullanıcı adı
            # e-postanın @ öncesinden türetilir (kullanıcı sonra değiştirebilir)
            for kid, eski in conn.execute("SELECT id, kullanici_adi FROM kullanicilar WHERE kullanici_adi LIKE '%@%' "
                                          "AND eposta IS NULL").fetchall():
                conn.execute("UPDATE kullanicilar SET eposta = ?, kullanici_adi = ? WHERE id = ?",
                             (eski.lower(), self._bos_kullanici_adi(conn, eski.split("@")[0], kid), kid))

    @staticmethod
    def _bos_kullanici_adi(conn, taban, haric_id=None):
        """Tabandan kalıba uyan, alınmamış bir kullanıcı adı: ayse.yilmaz, ayse.yilmaz2, ..."""
        taban = re.sub(r"[^A-Za-z0-9._-]", "", taban.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")))[:26]
        taban = taban if len(taban) >= 3 else (taban + "uye")[:26]
        ad, n = taban, 1
        while conn.execute("SELECT 1 FROM kullanicilar WHERE kullanici_adi = ? AND id != ?",
                           (ad, haric_id or -1)).fetchone():
            n += 1
            ad = f"{taban}{n}"
        return ad

    def _baglan(self):
        conn = sqlite3.connect(self.yol)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _kullanici(satir):
        if not satir:
            return None
        return {"id": satir["id"], "kullanici_adi": satir["kullanici_adi"], "eposta": satir["eposta"] or "",
                "ad": satir["ad"], "rol": satir["rol"], "aktif": bool(satir["aktif"]), "telefon": satir["telefon"],
                "adres": json.loads(satir["adres"]) if satir["adres"] else None,
                "kayit_tarihi": satir["kayit_tarihi"], "son_giris": satir["son_giris"]}

    def kullanici_sayisi(self, rol=None):
        with self._baglan() as conn:
            if rol:
                return conn.execute("SELECT COUNT(*) FROM kullanicilar WHERE rol = ?", (rol,)).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM kullanicilar").fetchone()[0]

    @staticmethod
    def _ad_ve_sifre(ad, sifre):
        ad = " ".join(ad.split())
        if not 1 <= len(ad) <= 50:
            raise HesapHatasi("Ad soyad 1-50 karakter olmalı.")
        if len(sifre) < EN_KISA_SIFRE:
            raise HesapHatasi(f"Şifre en az {EN_KISA_SIFRE} karakter olmalı.")
        return ad

    @staticmethod
    def _kullanici_adi_dogrula(kullanici_adi):
        kullanici_adi = str(kullanici_adi or "").strip()
        if not KULLANICI_ADI_KALIBI.match(kullanici_adi):
            raise HesapHatasi("Kullanıcı adı 3-30 karakter olmalı; sadece harf (Türkçe karakter olmadan), "
                              "rakam, nokta, tire ve alt çizgi kullanılabilir.")
        return kullanici_adi

    @staticmethod
    def _eposta_dogrula(eposta, zorunlu=True):
        eposta = str(eposta or "").strip().lower()
        if not eposta and not zorunlu:
            return None
        if len(eposta) > 100 or not EPOSTA_KALIBI.match(eposta):
            raise HesapHatasi("Geçerli bir e-posta adresi yazın.")
        return eposta

    @staticmethod
    def _cakisma(hata):
        """Benzersizlik hatasının hangi alandan olduğu (kullanıcı adı / e-posta)."""
        if "eposta" in str(hata):
            return HesapHatasi("Bu e-posta adresi başka bir hesapta kayıtlı.")
        return HesapHatasi("Bu kullanıcı adı alınmış, başka bir ad deneyin.")

    def _ekle(self, kullanici_adi, eposta, ad, sifre, rol):
        simdi = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._baglan() as conn:
                cur = conn.execute("INSERT INTO kullanicilar (kullanici_adi, eposta, ad, sifre, kayit_tarihi, rol) "
                                   "VALUES (?, ?, ?, ?, ?, ?)", (kullanici_adi, eposta, ad, sifre_ozeti(sifre), simdi, rol))
                return self.kullanici(cur.lastrowid, conn)
        except sqlite3.IntegrityError as e:
            raise self._cakisma(e) from None

    def kayit(self, kullanici_adi, ad, sifre, rol="satici", eposta=None):
        """Satıcı hesabı (e-posta isteğe bağlı). Panelden satıcı ekleme ve ilk hesap için."""
        return self._ekle(self._kullanici_adi_dogrula(kullanici_adi), self._eposta_dogrula(eposta, zorunlu=False),
                          self._ad_ve_sifre(ad, sifre), sifre, rol)

    def musteri_kayit(self, kullanici_adi, eposta, ad, sifre):
        """Mağazadan üyelik: kullanıcı adı ve e-posta ikisi de zorunlu, ikisiyle de giriş yapılır."""
        return self._ekle(self._kullanici_adi_dogrula(kullanici_adi), self._eposta_dogrula(eposta),
                          self._ad_ve_sifre(ad, sifre), sifre, "musteri")

    def kullanici(self, kullanici_id, conn=None):
        if conn is None:
            with self._baglan() as conn:
                return self.kullanici(kullanici_id, conn)
        return self._kullanici(conn.execute("SELECT * FROM kullanicilar WHERE id = ?", (kullanici_id,)).fetchone())

    def listele(self, rol):
        with self._baglan() as conn:
            return [self._kullanici(s) for s in conn.execute(
                "SELECT * FROM kullanicilar WHERE rol = ? ORDER BY id", (rol,)).fetchall()]

    def aktiflik(self, kullanici_id, aktif):
        """Hesabı kapatır / açar; kapatılan hesabın açık oturumları da kapanır."""
        with self._baglan() as conn:
            conn.execute("UPDATE kullanicilar SET aktif = ? WHERE id = ?", (int(bool(aktif)), kullanici_id))
            if not aktif:
                conn.execute("DELETE FROM oturumlar WHERE kullanici_id = ?", (kullanici_id,))

    def bilgileri_guncelle(self, kullanici_id, ad=None, telefon=None, adres=None, kullanici_adi=None, eposta=None):
        """Verilen alanları değiştirir (None olanlar aynı kalır). Müşteride e-posta zorunlu, satıcıda boş olabilir."""
        alanlar = {}
        if ad is not None:
            alanlar["ad"] = self._ad_ve_sifre(ad, "x" * EN_KISA_SIFRE)
        if kullanici_adi is not None:
            alanlar["kullanici_adi"] = self._kullanici_adi_dogrula(kullanici_adi)
        if eposta is not None:
            mevcut = self.kullanici(kullanici_id)
            alanlar["eposta"] = self._eposta_dogrula(eposta, zorunlu=bool(mevcut and mevcut["rol"] == "musteri"))
        if telefon is not None:
            telefon = " ".join(str(telefon).split())
            if len(telefon) > 25:
                raise HesapHatasi("Telefon numarası çok uzun.")
            alanlar["telefon"] = telefon
        if adres is not None:
            alanlar["adres"] = json.dumps(adres, ensure_ascii=False)
        if alanlar:
            try:
                with self._baglan() as conn:
                    conn.execute(f"UPDATE kullanicilar SET {', '.join(f'{k} = ?' for k in alanlar)} WHERE id = ?",
                                 [*alanlar.values(), kullanici_id])
            except sqlite3.IntegrityError as e:
                raise self._cakisma(e) from None
        return self.kullanici(kullanici_id)

    def giris(self, tanim, sifre):
        """Kullanıcı adı ya da e-posta ve şifre doğruysa kullanıcıyı, değilse None döndürür.
        (Kullanıcı adında @ olamaz, e-postada olmak zorunda: ikisi karışmaz.) Kapatılmış hesap giriş yapamaz."""
        tanim = tanim.strip()
        with self._baglan() as conn:
            satir = conn.execute("SELECT * FROM kullanicilar WHERE kullanici_adi = ? OR eposta = ?",
                                 (tanim, tanim.lower())).fetchone()
            if not sifre_dogru(sifre, satir["sifre"] if satir else _SAHTE_OZET) or satir is None or not satir["aktif"]:
                return None
            conn.execute("UPDATE kullanicilar SET son_giris = ? WHERE id = ?",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), satir["id"]))
            return self._kullanici(satir)

    def sifre_degistir(self, kullanici_id, eski, yeni):
        if len(yeni) < EN_KISA_SIFRE:
            raise HesapHatasi(f"Yeni şifre en az {EN_KISA_SIFRE} karakter olmalı.")
        with self._baglan() as conn:
            satir = conn.execute("SELECT sifre FROM kullanicilar WHERE id = ?", (kullanici_id,)).fetchone()
            if satir is None or not sifre_dogru(eski, satir["sifre"]):
                raise HesapHatasi("Mevcut şifre yanlış.")
            conn.execute("UPDATE kullanicilar SET sifre = ? WHERE id = ?", (sifre_ozeti(yeni), kullanici_id))

    def oturum_ac(self, kullanici_id):
        anahtar = secrets.token_urlsafe(32)
        with self._baglan() as conn:
            conn.execute("DELETE FROM oturumlar WHERE bitis < ?", (time.time(),))
            conn.execute("INSERT INTO oturumlar (anahtar, kullanici_id, bitis) VALUES (?, ?, ?)",
                         (anahtar_ozeti(anahtar), kullanici_id, time.time() + OTURUM_SURESI))
        return anahtar

    def oturum_kullanici(self, anahtar):
        if not anahtar:
            return None
        with self._baglan() as conn:
            satir = conn.execute(
                "SELECT k.* FROM oturumlar o JOIN kullanicilar k ON k.id = o.kullanici_id "
                "WHERE o.anahtar = ? AND o.bitis > ? AND k.aktif = 1", (anahtar_ozeti(anahtar), time.time())).fetchone()
            return self._kullanici(satir)

    def oturum_kapat(self, anahtar):
        if anahtar:
            with self._baglan() as conn:
                conn.execute("DELETE FROM oturumlar WHERE anahtar = ?", (anahtar_ozeti(anahtar),))

    def diger_oturumlari_kapat(self, kullanici_id, anahtar):
        """Şifre değişince bu cihaz dışındaki bütün oturumlar kapanır."""
        with self._baglan() as conn:
            conn.execute("DELETE FROM oturumlar WHERE kullanici_id = ? AND anahtar != ?",
                         (kullanici_id, anahtar_ozeti(anahtar)))
