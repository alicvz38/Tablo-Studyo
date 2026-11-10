"""Tablo Stüdyosu çekirdeği: masaüstü tablo_studyo.py'deki görüntü işleme ve veritabanı fonksiyonları
(arayüzsüz). Web sunucusu (sunucu.py) bu fonksiyonları kullanır; sonuçlar masaüstü programla birebir aynıdır.

Veriler STUDYO_VERI ortam değişkenindeki klasörde (varsayılan: bu dosyanın yanındaki veri/) tutulur:
  veri/odalar/odalar.db + oda resimleri
"""
import glob
import os
import re
import json
import shutil
import sqlite3
from datetime import datetime

import cv2
import numpy as np

VERI_KLASORU = os.path.abspath(os.environ.get("STUDYO_VERI") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "veri"))
# --- VERİTABANI ---
# Oda resimleri bu klasöre kopyalanır, koordinatlar aynı klasördeki SQLite dosyasında tutulur.
# Web sürümünde her kullanıcının kendi oda klasörü vardır (oda_klasoru parametresi).
ODA_KLASORU = os.path.join(VERI_KLASORU, "odalar")

def veritabani_baglan(oda_klasoru=None):
    oda_klasoru = oda_klasoru or ODA_KLASORU
    os.makedirs(oda_klasoru, exist_ok=True)
    conn = sqlite3.connect(os.path.join(oda_klasoru, "odalar.db"))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS odalar (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ad              TEXT NOT NULL,
            dosya           TEXT NOT NULL,      -- odalar/ klasöründeki resim dosyası
            genislik        INTEGER NOT NULL,   -- resmin piksel boyutu
            yukseklik       INTEGER NOT NULL,
            sol_ust_x       REAL, sol_ust_y  REAL,
            sag_ust_x       REAL, sag_ust_y  REAL,
            sag_alt_x       REAL, sag_alt_y  REAL,
            sol_alt_x       REAL, sol_alt_y  REAL,
            bos_duvar       INTEGER NOT NULL DEFAULT 0,  -- 1: tablo boş duvara otomatik yerleştirildi
            kayit_tarihi    TEXT NOT NULL,
            guncelleme      TEXT NOT NULL
        )
    """)
    # Eski veritabanlarına yansıma sütunlarını ekle (mevcut kayıtlar korunur)
    mevcut = {satir[1] for satir in conn.execute("PRAGMA table_info(odalar)")}
    for sutun, tip in YANSIMA_SUTUNLARI.items():
        if sutun not in mevcut:
            conn.execute(f"ALTER TABLE odalar ADD COLUMN {sutun} {tip}")
    # Temperli camın önceki sürümlerinde (2026-10-28) kayıtlara yazılan eski varsayılanlar elle değiştirilmemişse
    # yenileriyle değiştirilir
    if "cam_golge" in mevcut:
        for eski in ((5, 60, 30, 20, 45), (9, 75, 45, 35, 60), (7, 70, 30, 25, 60)):
            kosul = " AND ".join(f"{k} = {v}" for (k, *_), v in zip(TEMPERLI_KONTROLLERI, eski))
            conn.execute("UPDATE odalar SET " + ", ".join(f"{k} = {v}" for k, _, _, _, v in TEMPERLI_KONTROLLERI)
                         + " WHERE " + kosul)
    if "tablo_tipi" not in mevcut:
        conn.execute("ALTER TABLE odalar ADD COLUMN tablo_tipi TEXT")  # boşsa koordinattan tahmin edilir
    conn.commit()
    return conn

KOORDINAT_SUTUNLARI = ["sol_ust_x", "sol_ust_y", "sag_ust_x", "sag_ust_y",
                       "sag_alt_x", "sag_alt_y", "sol_alt_x", "sol_alt_y"]

# --- TABLO TİPİ: odadaki tablo yerinin şekli; resimler sadece aynı tipteki odalara uygulanır ---
TABLO_TIPLERI = {"yatay": "Yatay", "dikey": "Dikey", "kare": "Kare", "yuvarlak": "Yuvarlak"}
TABLO_TIPI_SIMGE = {"yatay": "▭", "dikey": "▯", "kare": "□", "yuvarlak": "◯"}

# --- Oda adlarını alfabetik sıralama: Türkçe harf sırası (ç ğ ı ö ş ü doğru yerde), büyük / küçük harf
# farkı yok, sayılar sayı olarak (oda-2, oda-10'dan önce gelir) ---
_TURKCE_ALFABE = "abcçdefgğhıijklmnoöprsştuüvyz"
_HARF_SIRASI = {h: i for i, h in enumerate(_TURKCE_ALFABE)}

def oda_adi_sirasi(ad):
    """sorted(..., key=...) için oda adı anahtarı."""
    ad = (ad or "").replace("I", "ı").replace("İ", "i").lower()
    anahtar = []
    for parca in re.split(r"(\d+)", ad):
        if parca.isdigit():
            anahtar.append((1, int(parca), ""))
        elif parca:
            # Alfabede olmayan karakterler (boşluk, tire, x, w, q ...) harflerden sonra, kendi kod sırasıyla
            anahtar.append((2, 0, tuple(_HARF_SIRASI.get(c, 100 + ord(c)) for c in parca)))
    return anahtar

def alan_orani(pts):
    """Sıralı 4 köşeden tablo alanının yaklaşık genişlik / yükseklik oranı."""
    genislik = (np.linalg.norm(pts[1] - pts[0]) + np.linalg.norm(pts[2] - pts[3])) / 2
    yukseklik = (np.linalg.norm(pts[3] - pts[0]) + np.linalg.norm(pts[2] - pts[1])) / 2
    return genislik / max(yukseklik, 1e-6)

def oran_tipi(oran, tolerans=1.1):
    """En-boy oranından tip: yaklaşık 1 ise kare, değilse yatay / dikey (yuvarlak elle seçilir)."""
    if oran > tolerans:
        return "yatay"
    if oran < 1 / tolerans:
        return "dikey"
    return "kare"

def tablo_tipi_tahmin(kose):
    return oran_tipi(alan_orani(kose_noktalarini_sirala(np.float32(kose))))

def tablo_maskesi(w, h, tip):
    """Tablo düzleminde (w x h) görünür alan maskesi (0-1): yuvarlakta kenarları yumuşak elips, diğerlerinde tamamı."""
    if tip != "yuvarlak":
        return np.ones((h, w), np.float32)
    maske = np.zeros((h, w), np.uint8)
    cv2.ellipse(maske, (int(round(w / 2 * 16)), int(round(h / 2 * 16))),
                (int(w / 2 * 16), int(h / 2 * 16)), 0, 0, 360, 255, -1, cv2.LINE_AA, 4)
    return maske.astype(np.float32) / 255.0

def tablo_hatti(kose, tip):
    """Ekranda çizilecek tablo sınırı: dörtgen ya da perspektife uygun elips noktaları."""
    pts = kose_noktalarini_sirala(np.float32(kose))
    if tip != "yuvarlak":
        return pts.astype(np.int32)
    M = cv2.getPerspectiveTransform(np.float32([[0, 0], [1, 0], [1, 1], [0, 1]]), pts)
    t = np.linspace(0, 2 * np.pi, 120, dtype=np.float32)
    birim = np.stack([0.5 + 0.5 * np.cos(t), 0.5 + 0.5 * np.sin(t)], axis=1)[None]
    return cv2.perspectiveTransform(birim, M)[0].astype(np.int32)

# --- CAM TABLO YANSIMASI: ayarlar ---
# Yansıma ayna gibidir (Referans Yansımalar klasöründeki fotoğraflara göre): camda odanın kendisi, en belirgin olarak
# odadaki pencere, ayna gibi ters görünür. Kullanıcı açar / kapar, gücü ve tarafı değiştirir, başka bir çeşit dener
# ya da aşağıdaki elle ayarları kullanır.
YANSIMA_TARAFLARI = {"oto": "Otomatik", "sol": "Sol", "sag": "Sağ", "ust": "Üst"}   # üst: tavandan / yukarıdan ışık
YANSIMA_GUC_ARALIGI = (0, 200)   # %; 100 = otomatik güç
YANSIMA_VARSAYILAN = {"acik": True, "guc": 100, "taraf": "oto", "cesit": 0}

# Yansımanın elle ayarları: otomatik yansımanın üstüne uygulanır (varsayılanlar = otomatik). Kayıt sütunu "cam_" + anahtar.
# (anahtar, ekrandaki etiket, en küçük, en büyük, varsayılan)
YANSIMA_KONTROLLERI = [
    ("kaydir_x",     "Konum Yatay %",                     -100, 100,   0),   # tablo genişliğine oranla, + sağa
    ("kaydir_y",     "Konum Dikey %",                     -100, 100,   0),   # tablo yüksekliğine oranla, + aşağı
    ("boyut",        "Boyut %",                             50, 200, 100),   # yansıyan odanın görünen boyu
    ("dondur_x",     "X Ekseninde Döndür °",              -180, 180,   0),   # yatay ekseninde (üst / alt öne yatar)
    ("dondur_y",     "Y Ekseninde Döndür °",              -180, 180,   0),   # pencere düzlemi dikey ekseninde
    ("dondur_z",     "Z Ekseninde Döndür °",              -180, 180,   0),   # cam düzleminde (saat yönü +)
    ("kontrast",     "Yansıma Kontrastı %",                  0, 100, 100),   # büyüdükçe sadece açık yerler yansır
    ("bulaniklik",   "Bulanıklık %",                         0, 100,   0),
    ("sicaklik",     "Renk Sıcaklığı (- soğuk / + sıcak)", -100, 100,   0),
    ("asagi_sonme",  "Aşağı Doğru Sönme %",                  0, 100,   0),
]
YANSIMA_VARSAYILAN.update({k: v for k, _, _, _, v in YANSIMA_KONTROLLERI})

# Temperli cam görünümü (sadece çerçevesiz tablolarda): camın kalınlığı (yan yüzü), cilalı kenar, parlak yüzey,
# cam tonu ve duvara gölge. (anahtar, ekrandaki etiket, en küçük, en büyük, varsayılan)
TEMPERLI_KONTROLLERI = [
    ("cam_kalinlik",  "Cam Kalınlığı",          0,  20,  7),   # ‰ (tablonun kısa kenarına göre)
    ("cam_kenar",     "Kenar Parlaklığı %",     0, 100, 100),
    ("cam_parlaklik", "Yüzey Parlaklığı %",     0, 100, 0),    # tablonun rengini değiştirmesin diye kapalı
    ("cam_ton",       "Cam Tonu %",             0, 100, 0),    # temperli camın hafif yeşil-mavi tonu (kapalı)
    ("cam_golge",     "Duvar Gölgesi %",        0, 100, 100),  # cam pano duvardan önde durur
]
YANSIMA_VARSAYILAN["temperli"] = True
YANSIMA_VARSAYILAN.update({k: v for k, _, _, _, v in TEMPERLI_KONTROLLERI})

# Odadaki pencerelerin bölme (sütun / sıra) sayısı aralığı
IZGARA_ARALIGI = (1, 8)

def yansima_ayari_duzelt(ayar):
    """Eksik / bozuk yansıma ayarını varsayılanlarla tamamlar."""
    a = {**YANSIMA_VARSAYILAN, **{k: v for k, v in (ayar or {}).items() if k in YANSIMA_VARSAYILAN and v is not None}}
    a["acik"] = bool(a["acik"])
    a["guc"] = int(np.clip(int(a["guc"]), *YANSIMA_GUC_ARALIGI))
    a["taraf"] = a["taraf"] if a["taraf"] in YANSIMA_TARAFLARI else "oto"
    a["cesit"] = int(a["cesit"]) % 1000
    a["temperli"] = bool(a["temperli"])
    for k, _, alt, ust, _ in YANSIMA_KONTROLLERI + TEMPERLI_KONTROLLERI:
        a[k] = int(np.clip(int(a[k]), alt, ust))
    return a

# Kayıt sütunları (eski yansima_* sütunları eski veritabanlarında durur ama kullanılmaz)
YANSIMA_SUTUNLARI = {
    "cam_acik": "INTEGER DEFAULT 1",
    "cam_gucu": "INTEGER DEFAULT 100",
    "cam_taraf": "TEXT DEFAULT 'oto'",
    "cam_cesit": "INTEGER DEFAULT 0",
    "cam_temperli": "INTEGER DEFAULT 1",
    **{"cam_" + k: f"INTEGER DEFAULT {v}" for k, _, _, _, v in YANSIMA_KONTROLLERI},
    **{k: f"INTEGER DEFAULT {v}" for k, _, _, _, v in TEMPERLI_KONTROLLERI},
    "isik_verisi": "TEXT",
}

def turkce_karakter_destekli_imread(dosya_yolu):
    try:
        img_array = np.fromfile(dosya_yolu, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"Dosya okuma hatası: {e}")
        return None

def kose_noktalarini_sirala(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)] # Sol Üst
    rect[2] = pts[np.argmax(s)] # Sağ Alt
    
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)] # Sağ Üst
    rect[3] = pts[np.argmax(diff)] # Sol Alt
    return rect

# ----------------------------------------------------------------------
# ÇERÇEVE İÇİ (TABLO ALANI) ALGILAMA
# ----------------------------------------------------------------------
def _ic_acilar_uygun(q, alt=55, ust=125):
    """Perspektifte bile dikdörtgenin iç açıları 90°'den çok sapmaz."""
    for i in range(4):
        a, b, c = q[i - 1], q[i], q[(i + 1) % 4]
        v1, v2 = a - b, c - b
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        if not (alt <= np.degrees(np.arccos(np.clip(cos, -1, 1))) <= ust):
            return False
    return True

def _aday_dortgenler(gray):
    """Farklı kenar hassasiyetlerinde tüm dışbükey dörtgen adaylarını toplar
    (köşeleri hafif yuvarlanmış şekiller için en küçük dikdörtgen yaklaşımı da denenir)."""
    h, w = gray.shape
    alan_min, alan_max = 0.02 * h * w, 0.75 * h * w
    adaylar = []
    # Kontrastı yerel olarak güçlendirilmiş görüntü: tuvale yakın renkli (gümüş, beyaz) çerçevelerin iç kenarı için
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    for kaynak, alt_esik, ust_esik in ((gray, 20, 60), (gray, 40, 120), (clahe, 30, 150)):
        edges = cv2.Canny(kaynak, alt_esik, ust_esik)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            alan = cv2.contourArea(c)
            if not (alan_min < alan < alan_max):
                continue
            peri = cv2.arcLength(c, True)
            secenekler = [cv2.approxPolyDP(c, eps * peri, True) for eps in (0.015, 0.03)]
            rect = cv2.minAreaRect(c)
            if rect[1][0] * rect[1][1] > 0 and alan / (rect[1][0] * rect[1][1]) > 0.92:
                secenekler.append(cv2.boxPoints(rect).reshape(-1, 1, 2))
            for approx in secenekler:
                if len(approx) != 4 or not cv2.isContourConvex(approx.astype(np.int32)):
                    continue
                q = kose_noktalarini_sirala(approx.reshape(4, 2).astype(np.float32))
                if _ic_acilar_uygun(q):
                    adaylar.append(q)
                    break

    # Neredeyse aynı adayları tekilleştir
    tekil = []
    for q in adaylar:
        boy = np.linalg.norm(q[0] - q[2])
        if all(np.abs(q - t).max() > 0.01 * boy for t in tekil):
            tekil.append(q)
    return tekil

def _aday_puani(gray, grad, q):
    """Bir dörtgenin 'çerçeve içi' olma olasılığını puanlar."""
    h, w = gray.shape
    alan = cv2.contourArea(q)
    merkez = q.mean(axis=0)
    genislik = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    yukseklik = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    oran = genislik / max(yukseklik, 1e-6)
    if not (0.2 < oran < 5):
        return 0.0
    # Tablolar duvarda asılıdır: merkezi resmin alt dörtte birinde olamaz (sehpa, halı, kanepe)
    if merkez[1] > 0.75 * h:
        return 0.0

    ic = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(ic, q.astype(np.int32), 255)
    k = max(3, int(0.015 * min(genislik, yukseklik)))
    ic_dar = cv2.erode(ic, np.ones((k, k), np.uint8))
    if cv2.countNonZero(ic_dar) < 100:
        return 0.0
    _, std = cv2.meanStdDev(gray, mask=ic_dar)
    duzluk = float(np.exp(-std[0][0] / 14.0))   # boş tuval / düz renk -> 1'e yakın

    # Kenar gücü: dörtgen sınırı boyunca gradyan
    sinir = np.zeros((h, w), np.uint8)
    cv2.polylines(sinir, [q.astype(np.int32)], True, 255, 3)
    kenar = cv2.mean(grad, mask=sinir)[0]
    kenar_puani = float(min(kenar / 40.0, 1.0))

    # Çerçeve bandı: sınırın hemen dışındaki şerit içeriden belirgin farklı mı?
    bant_k = max(5, int(0.03 * min(genislik, yukseklik)))
    dis = cv2.dilate(ic, np.ones((bant_k, bant_k), np.uint8))
    bant = cv2.subtract(dis, ic)
    ic_ort = cv2.mean(gray, mask=ic_dar)[0]
    bant_ort = cv2.mean(gray, mask=bant)[0]
    bant_puani = float(min(abs(ic_ort - bant_ort) / 30.0, 1.0))

    boyut_puani = np.sqrt(alan / (h * w))
    konum_puani = 1.0 if merkez[1] < 0.6 * h else 0.6
    return boyut_puani * (0.2 + duzluk) * (0.2 + kenar_puani) * (0.3 + bant_puani) * konum_puani

def _dogru_uzerindekiler(noktalar, tolerans=1.5, deneme=80):
    """Kenar noktalarının çoğunluğunun üzerinde olduğu tek doğru (RANSAC). Noktalar iki ayrı kenara
    (ör. çerçevenin dış kenarı ve tuval kenarı) bölünmüşse ikisinin arasından çapraz bir doğru geçmesin."""
    n = len(noktalar)
    if n < 6:
        return noktalar
    rng = np.random.default_rng(0)
    en_iyi = None
    for _ in range(deneme):
        i, j = rng.choice(n, 2, replace=False)
        d = noktalar[j] - noktalar[i]
        uz = np.linalg.norm(d)
        if uz < 1e-6:
            continue
        nrm = np.float32([-d[1], d[0]]) / uz
        icerde = np.abs((noktalar - noktalar[i]) @ nrm) < tolerans
        if en_iyi is None or icerde.sum() > en_iyi.sum():
            en_iyi = icerde
    if en_iyi is None or en_iyi.sum() < max(6, 0.4 * n):
        return noktalar
    return noktalar[en_iyi]

def _kenarlari_incelt(grad_x, grad_y, q):
    """Her kenar boyunca, kenara dik yönde en güçlü gradyanı arayıp çizgi oturtur;
    köşeleri bu çizgilerin kesişiminden (piksel altı hassasiyetle) hesaplar."""
    h, w = grad_x.shape
    kisa = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
    # İçeri az, dışarı daha çok ara: kaba dörtgen tuvaldeki iç gölgede kalmış olabilir
    r_ic = max(4, int(0.01 * kisa))
    r_dis = max(6, int(0.11 * kisa))   # kaba dörtgenin bir köşesi kesik / içeride kalmış olabilir
    offsetler = np.arange(-r_ic, r_dis + 1, dtype=np.float32)
    merkez = q.mean(axis=0)
    cizgiler = []

    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        yon = (b - a) / (np.linalg.norm(b - a) + 1e-9)
        n = np.float32([-yon[1], yon[0]])
        if np.dot(n, (a + b) / 2 - merkez) < 0:
            n = -n  # normal dışarı baksın

        noktalar, isaretler = [], []
        for t in np.linspace(0.08, 0.92, 60):
            p = a + t * (b - a)
            ornek = p[None, :] + offsetler[:, None] * n[None, :]
            xs = np.clip(ornek[:, 0], 0, w - 1).astype(np.float32).reshape(1, -1)
            ys = np.clip(ornek[:, 1], 0, h - 1).astype(np.float32).reshape(1, -1)
            gx = cv2.remap(grad_x, xs, ys, cv2.INTER_LINEAR)[0]
            gy = cv2.remap(grad_y, xs, ys, cv2.INTER_LINEAR)[0]
            gn = gx * n[0] + gy * n[1]   # dışarı doğru parlaklık değişimi
            mutlak = np.abs(gn)
            if mutlak.max() < 8:
                continue
            # İçeriden dışarı ilerlerken ilk güçlü tepe = tuval/çerçeve sınırı
            # (iç gölge zayıf kalır, çerçevenin dış kenarı ise daha dışarıdadır)
            guclu = mutlak >= 0.5 * mutlak.max()
            j = int(np.argmax(guclu))
            while j + 1 < len(mutlak) and mutlak[j + 1] >= mutlak[j]:
                j += 1
            # Parabolik tepe ile piksel altı konum
            s = offsetler[j]
            if 0 < j < len(gn) - 1:
                y0, y1, y2 = np.abs(gn[j - 1]), np.abs(gn[j]), np.abs(gn[j + 1])
                payda = y0 - 2 * y1 + y2
                if abs(payda) > 1e-6:
                    s += 0.5 * (y0 - y2) / payda
            noktalar.append(p + s * n)
            isaretler.append(np.sign(gn[j]))

        if len(noktalar) < 10:
            return q  # güvenilir değil, kaba dörtgeni koru
        noktalar = np.float32(noktalar)
        # Çoğunluğun işaretiyle aynı olanları al (çerçevenin dış kenarına atlamayı engeller)
        isaretler = np.array(isaretler)
        cogunluk = 1.0 if (isaretler > 0).sum() >= (isaretler < 0).sum() else -1.0
        noktalar = noktalar[isaretler == cogunluk]
        if len(noktalar) < 8:
            return q
        noktalar = _dogru_uzerindekiler(noktalar)
        vx, vy, x0, y0 = cv2.fitLine(noktalar, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        cizgiler.append((np.float32([x0, y0]), np.float32([vx, vy])))

    yeni = []
    for i in range(4):
        (p1, d1), (p2, d2) = cizgiler[i - 1], cizgiler[i]
        A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], dtype=np.float64)
        if abs(np.linalg.det(A)) < 1e-6:
            return q
        t = np.linalg.solve(A, (p2 - p1).astype(np.float64))[0]
        yeni.append(p1 + t * d1)
    yeni = np.float32(yeni)
    # İnceltme kaba dörtgenden çok uzaklaştıysa (yanlış kenara atladıysa) kabul etme
    if np.abs(yeni - q).max() > 2 * r_dis:
        return q
    return yeni

def _cerceve_bandini_atla(gray, q):
    """Kenar çerçevenin dışında kaldıysa (açılı tablolarda çerçevenin yan yüzü de görünür) kenarı tuvalin
    başladığı yere kadar içeri alır. Çerçeve kenar boyunca düz / tek renkli bir banttır, tuval ise dokuludur:
    her kenardan içeri doğru dokunun tuval seviyesine çıktığı ilk mesafe bulunur.
    Kenar zaten tuvaldeyse (bant yoksa) ya da tuval de düzse (karar verilemez) kenar yerinde kalır."""
    h, w = gray.shape
    kisa = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
    derin = max(8, int(0.07 * kisa))            # en fazla bu kadar içeri girilir
    ic_bolge = max(6, int(0.03 * kisa))          # tuval dokusu bu derinlikte ölçülür
    offsetler = np.arange(0, derin + ic_bolge, dtype=np.float32)
    merkez = q.mean(axis=0)
    cizgiler, degisti = [], False

    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        yon = (b - a) / (np.linalg.norm(b - a) + 1e-9)
        n = np.float32([-yon[1], yon[0]])
        if np.dot(n, merkez - (a + b) / 2) < 0:
            n = -n  # normal içeri baksın
        ts = np.linspace(0.1, 0.9, 120, dtype=np.float32)
        noktalar = a[None, :] + ts[:, None] * (b - a)[None, :]               # kenar boyunca
        ornek = noktalar[None, :, :] + offsetler[:, None, None] * n[None, None, :]
        xs = np.clip(ornek[..., 0], 0, w - 1).astype(np.float32)
        ys = np.clip(ornek[..., 1], 0, h - 1).astype(np.float32)
        deger = cv2.remap(gray, xs, ys, cv2.INTER_LINEAR)                      # (mesafe, kenar boyu)
        doku = np.abs(np.diff(deger, axis=1)).mean(axis=1)                    # kenar boyunca değişim
        doku = np.convolve(doku, np.ones(3) / 3, mode="same")
        tuval = float(np.median(doku[derin:]))

        kayma = 0.0
        if tuval >= 3.0:
            basla = 2                                   # kenarın hemen üstü (geçiş pikselleri) sayılmaz
            dokulu = np.nonzero(doku[basla:derin] >= 0.5 * tuval)[0]
            son = basla + (int(dokulu[0]) if len(dokulu) else derin - basla)
            bant = doku[basla:son]
            if son - basla >= 3 and son < derin and len(bant) and float(np.median(bant)) < 0.35 * tuval:
                # Gerçek çerçevede bant -> tuval geçişi kenar boyunca aynı mesafede düz bir çizgidir.
                # Resmin kendi düz fonu (ör. siyah fonlu çerçevesiz tablo) ise düzensiz biter: o zaman kenar kalır.
                adim = np.abs(np.diff(deger[basla:derin + 2], axis=0))           # (mesafe, kenar boyu)
                yerler = basla + np.argmax(adim, axis=0)
                orta = float(np.median(yerler))
                # Test odalarında: çerçevelerde tutarlılık 0.45-1.0, siyah fonlu çerçevesiz camda 0.05-0.13
                if abs(orta - son) <= 8 and np.mean(np.abs(yerler - orta) <= 2) >= 0.35:
                    kayma = orta + 0.5
                    degisti = True
        cizgiler.append((a + kayma * n, yon))

    if not degisti:
        return q
    yeni = []
    for i in range(4):
        (p1, d1), (p2, d2) = cizgiler[i - 1], cizgiler[i]
        A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], dtype=np.float64)
        if abs(np.linalg.det(A)) < 1e-6:
            return q
        t = np.linalg.solve(A, (p2 - p1).astype(np.float64))[0]
        yeni.append(p1 + t * d1)
    yeni = kose_noktalarini_sirala(np.float32(yeni))
    return yeni if _ic_acilar_uygun(yeni) else q

def _ic_bolge_buyut(grad, q):
    """Dörtgenin ortasından başlayıp yumuşak (gradyanı düşük) bağlı bölgeyi büyütür.
    Boş tuvaldeki ışık geçişleri yumuşaktır, çerçeve kenarı keskindir -> bölge çerçevenin iç kenarında durur.
    Dönüş: iç dörtgen, q zaten iç alansa q, içerik düz değilse None."""
    h, w = grad.shape[:2]
    alan_maske = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(alan_maske, q.astype(np.int32), 255)
    alan = cv2.countNonZero(alan_maske)

    mx, my = q.mean(axis=0).astype(int)
    r = max(3, int(0.08 * np.sqrt(alan)))
    yama = grad[max(0, my - r):my + r, max(0, mx - r):mx + r]
    esik = max(10.0, 3.0 * float(np.median(yama)))

    benzer = ((grad < esik) * 255).astype(np.uint8)
    benzer = cv2.bitwise_and(benzer, alan_maske)
    sayi, etiket = cv2.connectedComponents(benzer)
    if sayi < 2 or etiket[my, mx] == 0:
        return None
    bolge = ((etiket == etiket[my, mx]) * 255).astype(np.uint8)
    bolge = cv2.morphologyEx(bolge, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    oran = cv2.countNonZero(bolge) / alan
    if oran >= 0.985:
        return q
    if oran < 0.45:
        return None  # içi düz renk değil (resimli tablo vb.)

    contours, _ = cv2.findContours(bolge, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    c = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.04):
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            ic = kose_noktalarini_sirala(approx.reshape(4, 2).astype(np.float32))
            return ic if _ic_acilar_uygun(ic) else None
    rect = cv2.minAreaRect(hull)
    return kose_noktalarini_sirala(cv2.boxPoints(rect).astype(np.float32))

def _cizgi_kumeleri(gray):
    """Uzun düz kenar çizgileri: aynı doğru üzerindeki parçalar birleştirilir.
    Dönüş: (yatay, dikey) listeleri; her öğe (nokta, yön, toplam uzunluk)."""
    h, w = gray.shape
    kisa = min(h, w)
    edges = cv2.Canny(gray, 30, 90)
    parcalar = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=60, minLineLength=int(0.08 * kisa),
                               maxLineGap=int(0.01 * kisa) + 3)
    if parcalar is None:
        return [], []
    kosegen = np.hypot(h, w)
    kumeler = {"yatay": [], "dikey": []}
    for x1, y1, x2, y2 in sorted(parcalar.reshape(-1, 4).astype(np.float64),
                                 key=lambda p: -np.hypot(p[2] - p[0], p[3] - p[1])):
        dx, dy = x2 - x1, y2 - y1
        uz = np.hypot(dx, dy)
        if abs(dy) < 0.45 * abs(dx):
            tur = "yatay"
        elif abs(dx) < 0.25 * abs(dy):
            tur = "dikey"
        else:
            continue
        teta = np.arctan2(dy, dx) % np.pi
        nrm = np.float64([-np.sin(teta), np.cos(teta)])
        rho = nrm @ [x1, y1]
        for k in kumeler[tur]:
            fark = abs(teta - k["teta"])
            if min(fark, np.pi - fark) < np.deg2rad(2.0) and abs(nrm @ k["nokta"] - rho) < 0.006 * kosegen:
                k["uclar"] += [(x1, y1), (x2, y2)]
                k["uzunluk"] += uz
                break
        else:
            kumeler[tur].append({"teta": teta, "nokta": np.float64([x1, y1]), "uclar": [(x1, y1), (x2, y2)],
                                 "uzunluk": uz})

    def dogru(k):
        vx, vy, x0, y0 = cv2.fitLine(np.float32(k["uclar"]), cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        return np.float32([x0, y0]), np.float32([vx, vy]), k["uzunluk"]

    # Mobilya, halı ya da resmin kendi deseni çok sayıda çizgi verir: tablonun kenarı listede kalsın diye 28'e kadar
    sec = lambda ks: [dogru(k) for k in sorted(ks, key=lambda k: -k["uzunluk"])[:28]]
    return sec(kumeler["yatay"]), sec(kumeler["dikey"])

def _kesisim(c1, c2):
    (p1, d1, _), (p2, d2, _) = c1, c2
    A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], dtype=np.float64)
    if abs(np.linalg.det(A)) < 1e-6:
        return None
    t = np.linalg.solve(A, (p2 - p1).astype(np.float64))[0]
    return p1 + t * d1

def _kenar_destegi(grad, a, b, n=48):
    """a-b kenarı boyunca (kenara dik ±2 px içinde) belirgin gradyan bulunan noktaların oranı.
    Resmin dışında kalan noktalar sayılmaz; kenarın çoğu dışarıdaysa 0."""
    h, w = grad.shape
    yon = (b - a) / (np.linalg.norm(b - a) + 1e-9)
    nrm = np.float32([-yon[1], yon[0]])
    t = np.linspace(0.05, 0.95, n, dtype=np.float32)[:, None]
    p = a[None, :] + t * (b - a)[None, :]
    icerde = (p[:, 0] >= 2) & (p[:, 0] <= w - 3) & (p[:, 1] >= 2) & (p[:, 1] <= h - 3)
    if icerde.mean() < 0.3:
        return 0.0
    en_iyi = np.zeros(n, np.float32)
    for s in (-2, -1, 0, 1, 2):
        q = p + s * nrm
        xs = np.clip(q[:, 0], 0, w - 1).astype(np.float32).reshape(1, -1)
        ys = np.clip(q[:, 1], 0, h - 1).astype(np.float32).reshape(1, -1)
        en_iyi = np.maximum(en_iyi, cv2.remap(grad, xs, ys, cv2.INTER_LINEAR)[0])
    return float((en_iyi[icerde] > 40).mean())

def _isaretli_kenar(gx, gy, a, b, n):
    """a-b kenarı boyunca (kenara dik ±2 px içinde) kenara dik yöndeki işaretli gradyan (en güçlüsü) ve
    resmin içinde kalan noktalar."""
    h, w = gx.shape
    yon = (b - a) / (np.linalg.norm(b - a) + 1e-9)
    nrm = np.float32([-yon[1], yon[0]])
    t = np.linspace(0.05, 0.95, n, dtype=np.float32)[:, None]
    p = a[None, :] + t * (b - a)[None, :]
    icerde = (p[:, 0] >= 2) & (p[:, 0] <= w - 3) & (p[:, 1] >= 2) & (p[:, 1] <= h - 3)
    en_iyi = np.zeros(n, np.float32)
    for s in (-2, -1, 0, 1, 2):
        q = p + s * nrm
        xs = np.clip(q[:, 0], 0, w - 1).astype(np.float32).reshape(1, -1)
        ys = np.clip(q[:, 1], 0, h - 1).astype(np.float32).reshape(1, -1)
        g = cv2.remap(gx, xs, ys, cv2.INTER_LINEAR)[0] * nrm[0] + cv2.remap(gy, xs, ys, cv2.INTER_LINEAR)[0] * nrm[1]
        en_iyi = np.where(np.abs(g) > np.abs(en_iyi), g, en_iyi)
    return en_iyi, icerde

def _kenar_koseden_tasiyor(gx, gy, a, b):
    """Tablonun kenarı köşede biter; tavan, pencere kasası, duvar köşesi gibi çizgiler köşeden öteye uzanır.
    a-b kenarının iki ucundan dışarı doğru uzantısında AYNI kenar (aynı yönde, benzer güçte parlaklık geçişi)
    devam ediyorsa True. Yere yaslanan camın alt kenarının hizasındaki duvar-zemin çizgisi ya da dokulu duvar
    (rastgele yönlü, zayıf geçişler) kenarın devamı sayılmaz."""
    ana, icerde = _isaretli_kenar(gx, gy, a, b, 48)
    if not icerde.any():
        return False
    isaret = np.sign(np.median(ana[icerde]))
    # Resim kenara değdiği yerde geçiş zayıflar / yön değiştirir: güç, baskın yöndeki güçlü kısımdan
    guc = float(np.percentile(ana[icerde] * isaret, 75))
    if isaret == 0 or guc < 1e-3:
        return False
    uzanti = 0.15 * (b - a)
    # Köşeye en yakın çeyrek atlanır: köşedeki bulanıklık / temas gölgesi uzantı sayılmasın
    for u0, u1 in ((a - uzanti, a - 0.25 * uzanti), (b + 0.25 * uzanti, b + uzanti)):
        devam, ic = _isaretli_kenar(gx, gy, u0, u1, 16)
        if ic.mean() >= 0.3 and np.mean((devam[ic] * isaret) >= 0.5 * guc) > 0.6:
            return True
    return False

def _ici_disindan_farkli(gray, q, lab=None):
    """Dörtgenin içi hemen dışındaki şeritten belirgin farklı mı (duvardaki iki çizgi arası boş duvar tablo
    değildir). Gri ton aynı olsa da renk farkı (sarı duvarda mor tablo; lab: renkli resmin LAB hali) yeterlidir.
    Hız için sadece dörtgenin çevresindeki kutuda hesaplanır."""
    h, w = gray.shape
    bant_k = max(5, int(0.04 * min(h, w)))
    x0, y0 = np.maximum(np.floor(q.min(axis=0)).astype(int) - bant_k, 0)
    x1, y1 = np.minimum(np.ceil(q.max(axis=0)).astype(int) + bant_k + 1, [w, h])
    ic = np.zeros((y1 - y0, x1 - x0), np.uint8)
    cv2.fillConvexPoly(ic, (q - [x0, y0]).astype(np.int32), 255)
    dis = cv2.subtract(cv2.dilate(ic, np.ones((bant_k, bant_k), np.uint8)), ic)
    g = gray[y0:y1, x0:x1]
    ic_ort, ic_std = cv2.meanStdDev(g, mask=ic)
    dis_ort, dis_std = cv2.meanStdDev(g, mask=dis)
    if abs(ic_ort[0][0] - dis_ort[0][0]) >= 20 or ic_std[0][0] >= 2.0 * dis_std[0][0] + 8:
        return True
    if lab is not None:
        l = lab[y0:y1, x0:x1]
        fark = np.float32(cv2.mean(l, mask=ic)[:3]) - np.float32(cv2.mean(l, mask=dis)[:3])
        return float(np.linalg.norm(fark[1:])) >= 15   # a / b (renk) kanallarında fark
    return False

def _kenar_tutarliligi(gx, gy, a, b):
    """a-b kenarı boyunca belirgin ve AYNI yönde (hep koyudan açığa ya da hep açıktan koyuya) parlaklık geçişi olan
    noktaların oranı. Tablonun kenarı boydan boya aynı yönde geçiştir; kanepe, halı, bitki içinden geçen bir çizgi
    ise yön değiştirir. Resmin dışında kalan noktalar sayılmaz; kenarın çoğu dışarıdaysa (0, 0).
    Dönüş: (oran, uç oranı). Uç oranı iki uçtaki (yedişer nokta) oranların küçüğüdür: tablonun kenarı köşeyi geçip
    boş duvara / pencereye uzatılmışsa düşüktür."""
    g, icerde = _isaretli_kenar(gx, gy, a, b, 48)
    if icerde.mean() < 0.3:
        return 0.0, 0.0
    isaret = 1.0 if np.sum(g[icerde] > 40) >= np.sum(g[icerde] < -40) else -1.0
    var = (g * isaret > 40)
    uclar = [var[uc][icerde[uc]].mean() for uc in (slice(0, 7), slice(-7, None)) if icerde[uc].any()]
    return float(var[icerde].mean()), float(min(uclar)) if uclar else 0.0

def _cizgi_dortgeni(gray, grad, renkli=None):
    """Çerçevesiz (cam / tuval) tablolar için: kontur kapanmasa da (desenli resim, resim kenarına değen köşe)
    dört kenarı uzun düz çizgilerden kurar. İki dikey + iki yatay çizginin oluşturduğu, dört kenarı da
    boydan boya kenarla desteklenen ve içi dışından belirgin farklı olan en büyük dörtgen seçilir."""
    h, w = gray.shape
    yataylar, dikeyler = _cizgi_kumeleri(gray)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    pay = 0.01 * max(h, w)
    sinir = 0.02 * max(h, w)

    # Soldan sağa / yukarıdan aşağı sıralı: i < j ise i soldaki (üstteki) çizgi
    def orta(c, eksen):
        p, v, _ = c
        if eksen == 0:
            return p[0] + (h / 2 - p[1]) / (v[1] if abs(v[1]) > 1e-6 else 1e-6) * v[0]
        return p[1] + (w / 2 - p[0]) / (v[0] if abs(v[0]) > 1e-6 else 1e-6) * v[1]
    dikeyler = sorted(dikeyler, key=lambda c: orta(c, 0))
    yataylar = sorted(yataylar, key=lambda c: orta(c, 1))
    # Bütün yatay-dikey kesişimleri bir kez (dörtgenler bunlardan kurulur)
    K = [[_kesisim(y, d) for d in dikeyler] for y in yataylar]

    lab = cv2.cvtColor(renkli, cv2.COLOR_BGR2LAB).astype(np.float32) if renkli is not None else None
    # Aynı kenar (aynı çizginin aynı iki çizgi arasındaki parçası) birçok dörtgende geçer: bir kez hesaplanır
    destek_bellek, tasma_bellek = {}, {}

    def kenar_destegi(anahtar, a, b):
        if anahtar not in destek_bellek:
            destek_bellek[anahtar] = _kenar_tutarliligi(gx, gy, a, b)
        return destek_bellek[anahtar]

    def kenar_tasiyor(anahtar, a, b):
        if anahtar not in tasma_bellek:
            tasma_bellek[anahtar] = _kenar_koseden_tasiyor(gx, gy, a, b)
        return tasma_bellek[anahtar]

    adaylar = []
    for i in range(len(dikeyler)):
        for j in range(i + 1, len(dikeyler)):
            for k in range(len(yataylar)):
                for m in range(k + 1, len(yataylar)):
                    koseler = (K[k][i], K[k][j], K[m][j], K[m][i])
                    if any(p is None for p in koseler):
                        continue
                    q = np.float32(koseler)
                    if (q[:, 0].min() < -pay or q[:, 1].min() < -pay or q[:, 0].max() > w + pay
                            or q[:, 1].max() > h + pay):
                        continue
                    gen = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3]))
                    yuk = min(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1]))
                    if gen < 0.15 * w or yuk < 0.15 * h or q.mean(axis=0)[1] > 0.7 * h:
                        continue
                    alan = cv2.contourArea(q)
                    if not (0.04 * h * w < alan < 0.75 * h * w) or not _ic_acilar_uygun(q, 70, 110):
                        continue
                    # Resmin kenarı boyunca uzanan kenar (iki köşesi de aynı sınırda) tablo kenarı değildir;
                    # tek köşenin sınıra değmesi olur (kadraja sığmayan tablo)
                    yakin = np.stack([q[:, 0] < sinir, q[:, 1] < sinir, q[:, 0] > w - sinir, q[:, 1] > h - sinir], 1)
                    if any((yakin[s] & yakin[(s + 1) % 4]).any() for s in range(4)):
                        continue
                    # Kenar anahtarları (kenarın çizgisi, uçlarındaki iki çizgi), köşe sırasıyla: üst, sağ, alt, sol
                    kenarlar = [("y", k, i, j), ("d", j, k, m), ("y", m, j, i), ("d", i, m, k)]
                    destek, uclar = [], []
                    for s in range(4):
                        oran, uc = kenar_destegi(kenarlar[s], q[s], q[(s + 1) % 4])
                        destek.append(oran)
                        uclar.append(uc)
                        if oran < 0.7:
                            break
                    if min(destek) < 0.7:
                        continue
                    if any(kenar_tasiyor(kenarlar[s], q[s], q[(s + 1) % 4]) for s in range(4)):
                        continue
                    # Kenarları köşelere ulaşmayan (köşeyi geçip duvara / pencereye uzatılmış) dörtgen geride kalır
                    kose_puani = 0.25 + 0.75 * float(np.mean(uclar))
                    adaylar.append((min(destek) * kose_puani * np.sqrt(alan / (h * w)), q))
    # En iyi puanlıdan başlayarak içi dışından farklı olan ilk dörtgen
    for _, q in sorted(adaylar, key=lambda a: -a[0]):
        if _ici_disindan_farkli(gray, q, lab):
            return _dis_kenara_genislet(gray, q)
    return None

def _dis_kenara_genislet(gray, q):
    """Çerçevesiz camda içerideki bir çizgi (camdaki pencere yansıması, resmin kendi çizgisi) kenar sanılmış
    olabilir: her kenardan dışarı doğru, dışında düz duvar başlayan son belirgin kenara (camın kenarı) taşır."""
    h, w = gray.shape
    kisa = min(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0]))
    derin = max(6, int(0.07 * kisa))
    duvar = max(4, int(0.02 * kisa))
    offsetler = np.arange(0, derin + duvar + 1, dtype=np.float32)
    merkez = q.mean(axis=0)
    cizgiler = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        yon = (b - a) / (np.linalg.norm(b - a) + 1e-9)
        n = np.float32([-yon[1], yon[0]])
        if np.dot(n, (a + b) / 2 - merkez) < 0:
            n = -n  # normal dışarı baksın
        ts = np.linspace(0.1, 0.9, 80, dtype=np.float32)
        noktalar = a[None, :] + ts[:, None] * (b - a)[None, :]
        ornek = noktalar[None, :, :] + offsetler[:, None, None] * n[None, None, :]
        xs = np.clip(ornek[..., 0], 0, w - 1).astype(np.float32)
        ys = np.clip(ornek[..., 1], 0, h - 1).astype(np.float32)
        deger = cv2.remap(gray.astype(np.float32), xs, ys, cv2.INTER_LINEAR)
        adim = np.abs(np.diff(deger.mean(axis=1)))                    # kenara dik ortalama değişim
        doku = np.abs(np.diff(deger, axis=1)).mean(axis=1)            # kenar boyunca değişim
        kayma = 0.0
        esik = 0.4 * adim[:3].max() if adim[:3].max() > 0 else 0
        for s in range(derin - 1, 2, -1):                             # dıştan içe: son belirgin kenar
            if adim[s] >= max(esik, 3.0) and adim[s] >= adim[max(0, s - 2):s + 3].max():
                dis_doku = doku[s + 2:s + 2 + duvar]
                if len(dis_doku) and dis_doku.mean() < 0.5 * doku[:s].mean() + 0.5:
                    kayma = s + 0.5
                break
        cizgiler.append((a + kayma * n, yon))
    yeni = []
    for i in range(4):
        (p1, d1), (p2, d2) = cizgiler[i - 1], cizgiler[i]
        A = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]], dtype=np.float64)
        if abs(np.linalg.det(A)) < 1e-6:
            return q
        t = np.linalg.solve(A, (p2 - p1).astype(np.float64))[0]
        yeni.append(p1 + t * d1)
    yeni = kose_noktalarini_sirala(np.float32(yeni))
    return yeni if _ic_acilar_uygun(yeni, 70, 110) else q

def tablo_alanini_bul(img):
    """Odadaki çerçevenin iç alanını (tablonun konacağı yeri) bulur. Bulamazsa None döner."""
    H, W = img.shape[:2]
    olcek = min(1.0, 1200.0 / max(H, W))
    kucuk = cv2.resize(img, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA) if olcek < 1 else img
    gray = cv2.GaussianBlur(cv2.cvtColor(kucuk, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    grad = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3),
                         cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))

    adaylar = _aday_dortgenler(gray)
    puanli = sorted(((_aday_puani(gray, grad, q), q) for q in adaylar), key=lambda x: x[0], reverse=True)
    # Kontur kapanmadıysa (çerçevesiz, desenli tablo) ya da en iyi konturun bir kenarı hiçbir kenara oturmuyorsa
    # (yere yaslanan camın konturu zemindeki yansımayla birleşmiş): uzun düz kenar çizgilerinden dörtgen kur.
    # Camdaki pencere yansıması bir kenarı biraz zayıflatabilir (~0.4): o zaman kontur korunur.
    zayif = not puanli or puanli[0][0] < 0.05 or min(
        _kenar_destegi(grad, puanli[0][1][s], puanli[0][1][(s + 1) % 4]) for s in range(4)) < 0.25
    if zayif:
        q = _cizgi_dortgeni(gray, grad, kucuk)
        if q is not None:
            puanli = [(1.0, q)]
        elif not puanli or puanli[0][0] < 0.05:
            return None
    en_iyi_puan, en_iyi = puanli[0]

    # Seçilen dörtgenin içinde, ona oturan daha iç bir dörtgen (çerçevenin iç kenarı) varsa onu al
    while True:
        kontur = en_iyi.reshape(-1, 1, 2)
        alan = cv2.contourArea(en_iyi)
        ic_adaylar = [(p, q) for p, q in puanli
                      if p > 0.5 * en_iyi_puan
                      and 0.6 < cv2.contourArea(q) / alan < 0.985
                      and all(cv2.pointPolygonTest(kontur, (float(x), float(y)), False) >= 0 for x, y in q)
                      and np.linalg.norm(q.mean(axis=0) - en_iyi.mean(axis=0)) < 0.05 * np.sqrt(alan)]
        if not ic_adaylar:
            break
        en_iyi_puan, en_iyi = max(ic_adaylar, key=lambda x: cv2.contourArea(x[1]))

    # Kenar konturu çerçevenin iç kenarını kapalı bulamadıysa: tuval renginden bölge büyüterek bul
    ic = _ic_bolge_buyut(grad, en_iyi)
    if ic is not None:
        en_iyi = ic

    # Tam çözünürlükte kenar inceltme
    gray_tam = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    gx = cv2.Sobel(gray_tam, cv2.CV_32F, 1, 0, ksize=3) / 4.0
    gy = cv2.Sobel(gray_tam, cv2.CV_32F, 0, 1, ksize=3) / 4.0
    q = _kenarlari_incelt(gx, gy, en_iyi / olcek)
    # Tablonun iç kısmı alınır: kenar çerçevenin üstünde / dışında kaldıysa tuvale kadar içeri al
    return _cerceve_bandini_atla(gray_tam, q)

# ----------------------------------------------------------------------
# YUVARLAK TABLO ALANI ALGILAMA
# ----------------------------------------------------------------------
def _elips_noktalari(elips, n=180):
    """Elips (cv2.fitEllipse biçiminde) üzerindeki n nokta ve bu noktalardaki dışa bakan birim normaller."""
    (cx, cy), (w, h), aci = elips
    a, b = max(w / 2, 1e-3), max(h / 2, 1e-3)
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    c, s = np.cos(np.deg2rad(aci)), np.sin(np.deg2rad(aci))
    x, y = a * np.cos(t), b * np.sin(t)
    pts = np.stack([cx + x * c - y * s, cy + x * s + y * c], axis=1)
    nx, ny = np.cos(t) / a, np.sin(t) / b
    nrm = np.stack([nx * c - ny * s, nx * s + ny * c], axis=1)
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    return pts.astype(np.float32), nrm.astype(np.float32)

def _elips_uzakligi(elips, pts):
    """Noktaların elipse yaklaşık uzaklığı (piksel): normalize yarıçap farkı x kısa yarı eksen."""
    (cx, cy), (w, h), aci = elips
    a, b = max(w / 2, 1e-3), max(h / 2, 1e-3)
    c, s = np.cos(np.deg2rad(aci)), np.sin(np.deg2rad(aci))
    dx, dy = pts[:, 0] - cx, pts[:, 1] - cy
    x, y = dx * c + dy * s, -dx * s + dy * c
    return np.abs(np.sqrt((x / a) ** 2 + (y / b) ** 2) - 1) * min(a, b)

def _saglam_elips(pts, tolerans, deneme=150, rng=None):
    """Noktalara aykırı noktalara dayanıklı (RANSAC) elips oturtur: tablonun önüne giren vazo, abajur, koltuk
    gibi nesnelerin kontur parçaları elipsi bozmasın. Dönüş: (elips, uyan nokta oranı) ya da (None, 0)."""
    pts = np.float32(pts).reshape(-1, 2)
    if len(pts) < 12:
        return None, 0.0
    rng = rng or np.random.default_rng(0)
    en_iyi, en_iyi_uyan = None, None
    for i in range(deneme):
        if i % 2:
            # Konturun ardışık bir yayından örnek: kontur tablonun yanında koltuk / bitki de dolaşıyorsa,
            # tamamen rastgele 6 noktanın hepsinin tablo kenarına düşmesi çok nadirdir
            uzunluk = int(len(pts) * rng.uniform(0.05, 0.35))
            bas = rng.integers(len(pts))
            ornek = pts[(bas + np.linspace(0, uzunluk, 6).astype(int)) % len(pts)]
        else:
            ornek = pts[rng.choice(len(pts), 6, replace=False)]
        try:
            e = cv2.fitEllipse(ornek)
        except cv2.error:
            continue
        if not (np.isfinite(e[1]).all() and min(e[1]) > 4):
            continue
        uyan = _elips_uzakligi(e, pts) < tolerans
        if en_iyi_uyan is None or uyan.sum() > en_iyi_uyan.sum():
            en_iyi, en_iyi_uyan = e, uyan
    if en_iyi is None or en_iyi_uyan.sum() < 12:
        return None, 0.0
    # Uyan noktalarla son oturtma (iki tur: oturdukça uyan noktalar da netleşir)
    for _ in range(2):
        e = cv2.fitEllipse(pts[en_iyi_uyan])
        uyan = _elips_uzakligi(e, pts) < tolerans
        if uyan.sum() < 12:
            break
        en_iyi, en_iyi_uyan = e, uyan
    return en_iyi, float(en_iyi_uyan.mean())

def _normal_gradyan(gx, gy, pts, nrm, offsetler):
    """Her nokta için normal boyunca (offsetler kadar) örneklenen, normal yöndeki parlaklık değişimi (n x k)."""
    ornek = pts[:, None, :] + offsetler[None, :, None] * nrm[:, None, :]
    xs = np.ascontiguousarray(ornek[..., 0], dtype=np.float32)
    ys = np.ascontiguousarray(ornek[..., 1], dtype=np.float32)
    gxs = cv2.remap(gx, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    gys = cv2.remap(gy, xs, ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return gxs * nrm[:, None, 0] + gys * nrm[:, None, 1]

def _elips_destegi(gx, gy, elips, kenar_esik=10.0, duz_esik=6.0):
    """Elipsin çevresinin ne kadarı gerçek bir tablo kenarı (0-1): elips üzerinde keskin bir kenar olmalı ve
    hemen dışı (duvar / çerçeve) düz olmalı. Desenli bir tablonun içine düşen elipsler dışı desenli olduğu için,
    tablonun dışına taşan elipsler de kenarı tutmadığı için düşük puan alır."""
    (_, _), (w, h), _ = elips
    pts, nrm = _elips_noktalari(elips, 180)
    r = (w + h) / 4
    tol = max(2.0, 0.008 * r)
    kenar = np.abs(_normal_gradyan(gx, gy, pts, nrm, np.linspace(-tol, tol, 7, dtype=np.float32))).max(axis=1)
    dis_off = np.linspace(2.5 * tol, 2.5 * tol + max(4.0, 0.05 * r), 6, dtype=np.float32)
    dis = np.abs(_normal_gradyan(gx, gy, pts, nrm, dis_off)).mean(axis=1)
    H, W = gx.shape
    icerde = (pts[:, 0] >= 0) & (pts[:, 0] < W) & (pts[:, 1] >= 0) & (pts[:, 1] < H)
    return float(np.mean((kenar > kenar_esik) & (dis < duz_esik) & icerde))

def _golgeyi_atla(k, kenar, doku, parlak, b, p, en_genis):
    """k: bir normal boyunca seçilen dış kenarın sırası. Bu kenarın hemen içinde düz ve duvardan koyu bir şerit,
    onun da içinde başka güçlü bir kenar varsa, k cam panonun duvara düşen gölgesinin sınırıdır: içteki
    (tablonun kendi) kenarın sırasını döndürür. Değilse k'yı aynen döndürür."""
    esik = max(8.0, 0.5 * kenar[k])   # tablonun kenarı, gölgenin yumuşak sınırından zayıf olmaz
    for k2 in range(k - 3, max(k - en_genis, 1) - 1, -1):
        if kenar[k2] > esik and kenar[k2] >= kenar[k2 - 1] and kenar[k2] >= kenar[k2 + 1]:
            # Şerit: gölgenin dış sınırındaki yumuşak geçiş (b kadar) hariç, iç kenara kadar
            serit = slice(k2 + 2, k - b + 1)
            if serit.stop - serit.start < 2:
                return k
            dis_parlak = parlak[k + b: k + b + p].mean()
            if doku[serit].mean() < 5.0 and parlak[serit].mean() < dis_parlak - 4.0:
                return k2
            return k
    return k

def _elipsi_kenara_oturt(gx, gy, e, aralik, n=360, gray=None):
    """Elipsi tablonun gerçek kenarına yapıştırır ve aykırı noktalara dayanıklı yeniden oturtur.
    Her normal boyunca ±aralik içinde, içi desenli (tablo) dışı düz (duvar ya da cam panonun duvara düşen
    gölgesi) olan en güçlü kenar aranır: gölgenin dış sınırında iki taraf da düz, tablonun içindeki desen
    çizgilerinde iki taraf da desenli olduğu için bunlar seçilmez. Kenar bulunamazsa ya da elips çok kayarsa
    e'yi aynen döndürür."""
    r = (e[1][0] + e[1][1]) / 4
    pts, nrm = _elips_noktalari(e, n)
    p = int(max(3, round(0.012 * r)))          # içi / dışı ölçülen şerit genişliği
    b = p + 2                                  # kenar ile şerit arası boşluk: gölgenin yumuşak (birkaç piksele
                                               # yayılan) sınırı kendi geçişini "desen" saymasın
    a = int(np.ceil(aralik))
    off = np.arange(-a - p - b, a + p + b + 1, dtype=np.float32)
    gt = _normal_gradyan(gx, gy, pts, nrm, off)    # normal yöndeki parlaklık değişimi
    gd = _normal_gradyan(gy, -gx, pts, nrm, off)   # teğet yöndeki (gradyanın 90° dönmüşü)
    gn = np.abs(gt)
    # Doku: yön fark etmeksizin gradyan büyüklüğü; şerit ortalamaları kümülatif toplamla
    doku = np.sqrt(gt ** 2 + gd ** 2)
    birikimli = np.concatenate([np.zeros((n, 1), np.float32), np.cumsum(doku, axis=1)], axis=1)
    orta = np.arange(p + b, len(off) - p - b)
    ic_doku = (birikimli[:, orta - b + 1] - birikimli[:, orta - b + 1 - p]) / p
    dis_doku = (birikimli[:, orta + b + p] - birikimli[:, orta + b]) / p
    kenar = gn[:, orta]
    uygun = ((kenar > 8) & (kenar > 0.35 * kenar.max(axis=1, keepdims=True))
             & (ic_doku > np.maximum(7.0, 2.0 * dis_doku)) & (np.abs(off[orta]) <= aralik)[None, :])
    # Uygun kenarlardan en dıştaki: tablonun içinde de düz renkli geniş bölgeler olabilir, asıl kenar en dıştadır.
    # Seçilen kenar, en dış uygun noktadan başlayan yerel tepe (aynı kenarın birkaç pikseline yayılmışsa en güçlüsü)
    skor = np.where(uygun, kenar, 0)
    en_dis = len(orta) - 1 - np.argmax(uygun[:, ::-1], axis=1)
    j_yerel = en_dis.copy()
    for adim in range(1, 4):   # tepeye doğru içeri birkaç piksel kay
        aday = np.clip(en_dis - adim, 0, len(orta) - 1)
        daha_iyi = kenar[np.arange(n), aday] > kenar[np.arange(n), j_yerel]
        j_yerel = np.where(daha_iyi & uygun[np.arange(n), aday], aday, j_yerel)
    j = orta[j_yerel]
    if gray is not None:
        # Dar gölgeler: iç şerit gölgeyi aşıp tabloya ulaştığı için gölge sınırı da "içi desenli" görünebilir
        ornek = pts[:, None, :] + off[None, :, None] * nrm[:, None, :]
        parlak = cv2.remap(gray, np.ascontiguousarray(ornek[..., 0], dtype=np.float32),
                           np.ascontiguousarray(ornek[..., 1], dtype=np.float32), cv2.INTER_LINEAR)
        en_genis = int(0.1 * r)
        for i in np.flatnonzero(uygun.any(axis=1)):
            j[i] = _golgeyi_atla(j[i], gn[i], doku[i], parlak[i], b, p, en_genis)
    kayma = off[j].copy()
    y0, y1, y2 = gn[np.arange(n), j - 1], gn[np.arange(n), j], gn[np.arange(n), j + 1]
    payda = y0 - 2 * y1 + y2
    gecerli = np.abs(payda) > 1e-6
    kayma += np.clip(np.where(gecerli, 0.5 * (y0 - y2) / np.where(gecerli, payda, 1), 0), -1, 1)
    guclu = uygun.any(axis=1) & (y1 > 8)
    if guclu.sum() < n // 6:
        return e
    # Yinelemeli oturtma: önce bütün kenar noktalarıyla, sonra kalıntısı büyük (tablonun önündeki nesne,
    # desen çizgisi) noktalar atılarak. Dar toleranslı RANSAC burada uygun değil: elips gölgeyi kapsıyorsa
    # doğru kenar noktaları eski elipse göre "aykırı" görünür ve elips hiç kıpırdamaz.
    kenar_pts = (pts + kayma[:, None] * nrm)[guclu].astype(np.float32)
    secili = np.ones(len(kenar_pts), bool)
    yeni = e
    for _ in range(4):
        if secili.sum() < 12:
            return e
        yeni = cv2.fitEllipse(kenar_pts[secili])
        kalinti = _elips_uzakligi(yeni, kenar_pts)
        mad = np.median(np.abs(kalinti[secili] - np.median(kalinti[secili]))) + 1e-6
        secili = kalinti < max(1.5, 0.004 * r, 3.0 * 1.4826 * mad)
    if secili.mean() < 0.5 or _elips_uzakligi(e, _elips_noktalari(yeni, 36)[0]).max() > 1.5 * aralik:
        return e
    return yeni

def _elips_adaylari(gray, grad):
    """Doku maskesinden (duz duvar üstündeki desenli tablo) ve kenar konturlarından elips adayları."""
    h, w = gray.shape
    alan_min, alan_max = 0.015 * h * w, 0.6 * h * w
    konturlar = []
    # 1) Doku: desenli bölgeler düz duvardan ayrılır (yuvarlak tablonun kendisi)
    doku = cv2.GaussianBlur(grad, (0, 0), 3)
    k = max(3, int(0.012 * max(h, w))) | 1
    k_ac = max(5, int(0.03 * max(h, w))) | 1
    for esik in (5.0, 9.0):
        maske = ((doku > esik) * 255).astype(np.uint8)
        maske = cv2.morphologyEx(maske, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        # Açılmış hali: tabloyu önündeki bitki dalları / abajur kablosu gibi ince bağlarla koltuğa bağlanmaktan ayırır
        acik = cv2.morphologyEx(maske, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_ac, k_ac)))
        for m in (maske, acik):
            c, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            konturlar += c
    # 2) Kenarlar: yuvarlak çerçeveler ve düz renkli yuvarlak tuvaller
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    for kaynak, alt_esik, ust_esik in ((gray, 20, 60), (gray, 40, 120), (clahe, 30, 150)):
        kenar = cv2.dilate(cv2.Canny(kaynak, alt_esik, ust_esik), np.ones((3, 3), np.uint8))
        c, _ = cv2.findContours(kenar, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        konturlar += c

    adaylar = []
    rng = np.random.default_rng(0)
    for c in konturlar:
        if not alan_min < cv2.contourArea(c) < 1.6 * alan_max or len(c) < 40:
            continue
        pts = c.reshape(-1, 2).astype(np.float32)
        if len(pts) > 1500:
            pts = pts[np.linspace(0, len(pts) - 1, 1500).astype(int)]
        # Uyan oran düşük olabilir (tablo koltuğa / bitkiye değiyorsa kontur onları da dolaşır); asıl eleme
        # sonraki kenar desteği puanında yapılır
        e, uyan_orani = _saglam_elips(pts, tolerans=max(2.0, 0.006 * max(h, w)), deneme=250, rng=rng)
        if e is None or uyan_orani < 0.2:
            continue
        (cx, cy), (ew, eh), _ = e
        if not alan_min < np.pi * ew * eh / 4 < alan_max or max(ew, eh) / min(ew, eh) > 2.2:
            continue
        adaylar.append(e)

    # Neredeyse aynı adayları tekilleştir
    tekil = []
    for e in adaylar:
        boy = (e[1][0] + e[1][1]) / 2
        if all(np.hypot(e[0][0] - t[0][0], e[0][1] - t[0][1]) > 0.02 * boy
               or abs(e[1][0] - t[1][0]) + abs(e[1][1] - t[1][1]) > 0.04 * boy for t in tekil):
            tekil.append(e)
    return tekil

def _elips_puani(gray, gx, gy, e):
    """Bir elipsin 'duvardaki yuvarlak tablo' olma olasılığı ve kenar desteği."""
    h, w = gray.shape
    (cx, cy), (ew, eh), _ = e
    pts, _ = _elips_noktalari(e, 72)
    pay = 0.01 * max(h, w)
    if (pts[:, 0].min() < -pay or pts[:, 1].min() < -pay or pts[:, 0].max() > w + pay or pts[:, 1].max() > h + pay
            or cy > 0.75 * h):   # resmin dışına taşan ya da yerdeki (sehpa, halı) yuvarlaklar değil
        return 0.0, 0.0
    destek = _elips_destegi(gx, gy, e)
    boyut = np.sqrt(np.pi * ew * eh / 4 / (h * w))
    konum = 1.0 if cy < 0.6 * h else 0.6
    return destek ** 2 * boyut * konum, destek

def elips_dortgeni(elips):
    """Elipsi, tablo tipi 'yuvarlak' iken programın çizdiği daireyle birebir örtüşen 4 köşeye çevirir.
    Sol ve sağ kenarlar dik (tablolar duvarda dik asılır), üst ve alt kenarlar elipsin eşlenik çapı yönünde
    (duvar perspektifindeki gibi eğik). Bu paralelkenarın içine çizilen daire tam olarak bu elips olur."""
    (cx, cy), (w, h), aci = elips
    a, b = w / 2, h / 2
    R = np.array([[np.cos(np.deg2rad(aci)), -np.sin(np.deg2rad(aci))],
                  [np.sin(np.deg2rad(aci)), np.cos(np.deg2rad(aci))]])
    A = R @ np.diag([1 / a ** 2, 1 / b ** 2]) @ R.T      # elips: x^T A x = 1
    q = np.array([0.0, 1 / np.sqrt(A[1, 1])])            # dik yarı çap
    p = np.array([A[1, 1], -A[0, 1]])                    # q'nun eşleniği: p^T A q = 0
    p /= np.sqrt(p @ A @ p)
    if p[0] < 0:
        p = -p
    c = np.array([cx, cy])
    return np.float32([c - p - q, c + p - q, c + p + q, c - p + q])

def yuvarlak_alanini_bul(img):
    """Odadaki yuvarlak tablonun (ya da yuvarlak çerçevenin iç alanının) elipsini bulur.
    Dönüş: (4 köşe, elips) ya da None."""
    H, W = img.shape[:2]
    olcek = min(1.0, 1000.0 / max(H, W))
    kucuk = cv2.resize(img, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA) if olcek < 1 else img
    gray = cv2.GaussianBlur(cv2.cvtColor(kucuk, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) / 4.0
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3) / 4.0
    grad = cv2.magnitude(gx, gy)

    adaylar = [_elipsi_kenara_oturt(gx, gy, e, max(4.0, 0.1 * (e[1][0] + e[1][1]) / 4), n=180, gray=gray)
               for e in _elips_adaylari(gray.astype(np.uint8), grad)]
    puanli = sorted(((*_elips_puani(gray, gx, gy, e), e) for e in adaylar), key=lambda x: x[0], reverse=True)
    puanli = [(p, d, e) for p, d, e in puanli if d >= 0.55]
    if not puanli:
        return None
    _, destek, en_iyi = puanli[0]

    # Yuvarlak çerçeve: içinde ona oturan, içi düz (boş tuval) daha küçük bir elips varsa onu al
    alan = np.pi * en_iyi[1][0] * en_iyi[1][1] / 4
    for p, d, e in puanli[1:]:
        oran = np.pi * e[1][0] * e[1][1] / 4 / alan
        if (0.5 < oran < 0.95 and d >= 0.6
                and np.hypot(e[0][0] - en_iyi[0][0], e[0][1] - en_iyi[0][1]) < 0.05 * np.sqrt(alan)):
            maske = np.zeros(gray.shape, np.uint8)
            cv2.ellipse(maske, (e[0], (e[1][0] * 0.9, e[1][1] * 0.9), e[2]), 255, -1)
            if cv2.meanStdDev(gray, mask=maske)[1][0][0] < 14:
                en_iyi, destek = e, d
                break

    # Tam çözünürlükte inceltme: her normal boyunca en güçlü kenar, aykırı noktalara dayanıklı oturtma
    gray_tam = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
    gx = cv2.Sobel(gray_tam, cv2.CV_32F, 1, 0, ksize=3) / 4.0
    gy = cv2.Sobel(gray_tam, cv2.CV_32F, 0, 1, ksize=3) / 4.0
    e = ((en_iyi[0][0] / olcek, en_iyi[0][1] / olcek), (en_iyi[1][0] / olcek, en_iyi[1][1] / olcek), en_iyi[2])
    # Geniş arama: cam panonun duvara düşen gölgesi yarıçapın ~%10'u kadar olabilir. Her turda kenar noktaları
    # yeni elipse göre yeniden aranır; elips yerine oturana kadar (en fazla 3 tur)
    for _ in range(3):
        onceki = e
        e = _elipsi_kenara_oturt(gx, gy, e, max(6.0, 0.1 * (e[1][0] + e[1][1]) / 4), gray=gray_tam)
        if _elips_uzakligi(onceki, _elips_noktalari(e, 36)[0]).max() < 1.0:
            break
    e = _elipsi_kenara_oturt(gx, gy, e, max(3.0, 0.01 * (e[1][0] + e[1][1]) / 4), gray=gray_tam)   # ince ayar
    return elips_dortgeni(e), e

def tablo_yerini_bul(img):
    """Odadaki tablo yerini bulur: dikdörtgen çerçevenin iç alanı ya da yuvarlak tablo.
    Dönüş: (4 köşe, tip) — tip "yuvarlak" ya da None (köşelerin oranından tahmin edilir); bulunamazsa (None, None)."""
    dortgen = tablo_alanini_bul(img)
    yuvarlak = yuvarlak_alanini_bul(img)
    if yuvarlak is None:
        return dortgen, None
    kose, elips = yuvarlak
    if dortgen is None:
        return kose, "yuvarlak"

    q = kose_noktalarini_sirala(np.float32(dortgen))
    kontur = q.reshape(-1, 1, 2)
    elips_pts, _ = _elips_noktalari(elips, 72)
    pay = 0.01 * max(img.shape[:2])
    if all(cv2.pointPolygonTest(kontur, (float(x), float(y)), True) > -pay for x, y in elips_pts):
        # Yuvarlak, dörtgenin içinde: yuvarlağın hemen çevresi düzse (duvar, panel, çerçeve) duvardaki yuvarlak
        # tablodur; desenliyse yuvarlak bir motifi olan dikdörtgen tablodur
        gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0).astype(np.float32)
        grad = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)) / 4
        (cx, cy), (w, h), aci = elips
        serit = np.zeros(gray.shape, np.uint8)
        cv2.ellipse(serit, ((cx, cy), (w * 1.3, h * 1.3), aci), 255, -1)
        cv2.ellipse(serit, ((cx, cy), (w * 1.06, h * 1.06), aci), 0, -1)
        dortgen_maske = np.zeros(gray.shape, np.uint8)
        cv2.fillConvexPoly(dortgen_maske, q.astype(np.int32), 255)
        serit = cv2.bitwise_and(serit, dortgen_maske)
        if cv2.countNonZero(serit) < 100 or np.median(grad[serit > 0]) < 3.0:
            return kose, "yuvarlak"
        return dortgen, None
    if all(cv2.pointPolygonTest(elips_pts.reshape(-1, 1, 2), (float(x), float(y)), False) >= 0 for x, y in q):
        return kose, "yuvarlak"   # dörtgen yuvarlak tablonun içinde (camdaki pencere yansıması, desen)
    # Ayrı yerlerde: büyük olan duvardaki asıl tablodur
    return (kose, "yuvarlak") if np.pi * elips[1][0] * elips[1][1] / 4 > cv2.contourArea(q) else (dortgen, None)

def bos_duvar_alani(img):
    """Duvarda tablo asılabilecek en geniş boş (desensiz) alanın merkezini ve yarıçapını bulur."""
    h, w = img.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    edges = cv2.Canny(gray, 30, 90)
    k = max(3, int(max(h, w) * 0.015))
    edges = cv2.dilate(edges, np.ones((k, k), np.uint8))

    bos = cv2.bitwise_not(edges)
    bos[0, :] = bos[-1, :] = 0
    bos[:, 0] = bos[:, -1] = 0
    mesafe = cv2.distanceTransform(bos, cv2.DIST_L2, 5)

    # Tablolar genelde göz hizasında ve duvarın ortasına yakın asılır
    xs = np.arange(w, dtype=np.float32)
    yatay_agirlik = 1.0 - 0.4 * np.abs(xs - w / 2) / (w / 2)
    skor = mesafe * yatay_agirlik[None, :]
    skor[:int(h * 0.2), :] = 0
    skor[int(h * 0.55):, :] = 0

    cy, cx = np.unravel_index(np.argmax(skor), skor.shape)
    return float(cx), float(cy), float(mesafe[cy, cx])

def duvar_egim_modeli(img, cx, tablo_araligi=None):
    """Duvarın yatay çizgilerinden (tavan, süpürgelik, mobilya) perspektif modelini çıkarır.
    x = cx dikeyinde, y yüksekliğindeki bir yatay duvar çizgisinin eğimi: egim = alfa * y + beta.
    Çizgiler bir kaçış noktasında birleştiği için bu ilişki doğrusaldır.
    tablo_araligi (y_ust, y_alt): verilirse model ancak tablonun hem üstünde hem altında çizgi varsa
    kullanılır; tek taraftaki çizgiler (koltuk, raf) karşıdan görülen duvara yanlış eğim veriyordu."""
    h, w = img.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60,
                            minLineLength=int(0.08 * min(h, w)), maxLineGap=10)
    if lines is None:
        return 0.0, 0.0

    ornekler = []  # (y_cx, egim, uzunluk)
    for x1, y1, x2, y2 in lines.reshape(-1, 4).astype(np.float64):
        if x1 == x2:
            continue
        egim = (y2 - y1) / (x2 - x1)
        if abs(egim) > 1.0:  # dikeye yakın çizgiler perspektif bilgisi taşımaz
            continue
        pay = 0.15 * w
        if not (min(x1, x2) - pay <= cx <= max(x1, x2) + pay):
            continue
        y_cx = y1 + egim * (cx - x1)
        if 0 <= y_cx <= h:
            ornekler.append((y_cx, egim, np.hypot(x2 - x1, y2 - y1)))

    if not ornekler:
        return 0.0, 0.0
    ornekler = np.array(ornekler, dtype=np.float64)
    ys, egimler, uz = ornekler[:, 0], ornekler[:, 1], ornekler[:, 2]
    if len(ornekler) < 3:
        if tablo_araligi is not None:
            return 0.0, 0.0   # bir iki çizgi duvar açısı için yeterli değil
        return 0.0, float(np.average(egimler, weights=uz))

    # RANSAC: alakasız mobilya/zemin çizgilerini ele
    rng = np.random.default_rng(0)
    en_iyi, en_iyi_skor = None, -1
    for _ in range(200):
        i, j = rng.choice(len(ornekler), 2, replace=False)
        if abs(ys[i] - ys[j]) < 1e-3:
            alfa, beta = 0.0, (egimler[i] + egimler[j]) / 2
        else:
            alfa = (egimler[j] - egimler[i]) / (ys[j] - ys[i])
            beta = egimler[i] - alfa * ys[i]
        icerde = np.abs(alfa * ys + beta - egimler) < 0.03
        skor = uz[icerde].sum()
        if skor > en_iyi_skor:
            en_iyi_skor, en_iyi = skor, icerde

    ys, egimler, uz = ys[en_iyi], egimler[en_iyi], uz[en_iyi]
    if tablo_araligi is not None and not (ys.min() < tablo_araligi[0] and ys.max() > tablo_araligi[1]):
        return 0.0, 0.0
    if len(ys) >= 2 and np.ptp(ys) > 0.05 * h:
        alfa, beta = np.polyfit(ys, egimler, 1, w=uz)
    else:
        alfa, beta = 0.0, float(np.average(egimler, weights=uz))
    return float(alfa), float(beta)

def bos_duvar_dikdortgeni(img, oran, doluluk=0.85):
    """Duvardaki engellerin (lamba kablosu, yatak başlığı, koltuk, duvar köşesi, niş) arasında kalan,
    istenen en-boy oranındaki en büyük boş dikdörtgeni bulur ve onun 'doluluk' kadarını kullanır
    (test odalarında elle seçilen alanlar boş alanın ~%80-90'ı). Dönüş: (cx, cy, yarım genişlik, yarım yükseklik)."""
    h, w = img.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (0, 0), 1.5)
    edges = cv2.Canny(gray, 25, 70)
    k = max(3, int(0.01 * max(h, w)))
    engel = cv2.dilate(edges, np.ones((k, k), np.uint8))
    # Küçük parazit (duvar dokusu) engel sayılmasın: sadece belirgin uzunluktaki kenar parçaları
    sayi, etiket, istat, _ = cv2.connectedComponentsWithStats(engel)
    kucuk_parca = istat[:, cv2.CC_STAT_AREA] < 0.0015 * h * w
    kucuk_parca[0] = False
    engel[kucuk_parca[etiket]] = 0

    # Kaba ızgarada ara (hız için); integral görüntü ile her konum O(1)
    g = min(1.0, 240.0 / max(h, w))
    eng = cv2.resize(engel, (max(2, int(w * g)), max(2, int(h * g))), interpolation=cv2.INTER_AREA) > 0
    gh, gw = eng.shape
    integ = np.pad(eng.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))

    def uygun_konumlar(yuk):
        gen = int(round(yuk * oran))
        if yuk >= gh or gen >= gw or yuk < 2 or gen < 2:
            return None, gen
        toplam = integ[yuk:, gen:] - integ[:-yuk, gen:] - integ[yuk:, :-gen] + integ[:-yuk, :-gen]
        ys, xs = np.nonzero(toplam == 0)
        merkez_y = ys + yuk / 2
        tut = (merkez_y > 0.1 * gh) & (merkez_y < 0.65 * gh)   # tablolar göz hizası civarında asılır
        return (xs[tut], ys[tut]) if tut.any() else None, gen

    alt, ust = 2, gh
    en_iyi = None
    while alt <= ust:                                         # en büyük sığan yükseklik (ikili arama)
        orta = (alt + ust) // 2
        konum, gen = uygun_konumlar(orta)
        if konum is not None:
            en_iyi, alt = (orta, gen, konum), orta + 1
        else:
            ust = orta - 1
    if en_iyi is None:
        return None
    yuk, gen, (xs, ys) = en_iyi
    # Birden çok konum sığıyorsa boş alanın ortasına (yatayda biraz da fotoğrafın ortasına) en yakını
    hedef = np.array([0.5 * xs.mean() + 0.5 * (gw - gen) / 2, ys.mean()])
    j = int(np.argmin((xs - hedef[0]) ** 2 + (ys - hedef[1]) ** 2))
    cx, cy = (xs[j] + gen / 2) / g, (ys[j] + yuk / 2) / g
    return float(cx), float(cy), doluluk * gen / g / 2, doluluk * yuk / g / 2

def duvara_tablo_yerlestir(img, oran):
    """Tablosuz odada, boş duvar alanına duvar perspektifine uygun bir dörtgen yerleştirir.
    oran = tablo genişlik / yükseklik."""
    H, W = img.shape[:2]
    olcek = min(1.0, 800.0 / max(H, W))
    kucuk = cv2.resize(img, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA) if olcek < 1 else img
    h, w = kucuk.shape[:2]

    alan = bos_duvar_dikdortgeni(kucuk, oran)
    if alan is not None:
        cx, cy, a, b = alan
    else:
        cx, cy, r = bos_duvar_alani(kucuk)
        # Yarım yükseklik (b) ve yarım genişlik (a): boş alana sığacak, makul boyutta
        b = 1.05 * r / np.sqrt(1 + oran ** 2)
        b = float(np.clip(b, 0.12 * h, 0.28 * h))
        a = oran * b
        if a > 0.42 * w:
            a = 0.42 * w
            b = a / oran
    cx = float(np.clip(cx, a + 2, w - a - 2))
    cy = float(np.clip(cy, b + 2, h - b - 2))

    alfa, beta = duvar_egim_modeli(kucuk, cx, (cy - b, cy + b))
    # Kaçış noktası tablonun içine düşüyorsa model güvenilmez
    if abs(alfa) > 1e-9 and abs(1.0 / alfa) < a * 1.2:
        alfa = 0.0

    egim_ust = alfa * (cy - b) + beta
    egim_alt = alfa * (cy + b) + beta
    x1, x2 = cx - a, cx + a

    pts = np.float32([
        [x1, (cy - b) + egim_ust * (x1 - cx)],  # Sol Üst
        [x2, (cy - b) + egim_ust * (x2 - cx)],  # Sağ Üst
        [x2, (cy + b) + egim_alt * (x2 - cx)],  # Sağ Alt
        [x1, (cy + b) + egim_alt * (x1 - cx)],  # Sol Alt
    ]) / olcek

    pts[:, 0] = np.clip(pts[:, 0], 0, W - 1)
    pts[:, 1] = np.clip(pts[:, 1], 0, H - 1)
    return pts

# ----------------------------------------------------------------------
# CAM YANSIMASI
# ----------------------------------------------------------------------
def cita_konumlari(profil):
    """Pencere içindeki parlaklık profilinde koyu çıta (kayıt) vadilerinin ortaları (0-1)."""
    n = len(profil)
    if n < 12:
        return []
    # Yumuşatma ve kenar kırpması 8 bölmeli pencerenin ince çıtalarını yutmayacak kadar küçük
    k = max(1, int(n * 0.012))
    profil = np.convolve(profil, np.ones(k) / k, mode="same")
    kenar = int(n * 0.04)
    p = profil[kenar:n - kenar]
    if len(p) == 0 or np.median(p) < 0.2:
        return []
    vadi = np.concatenate([[False], p < 0.55 * np.median(p), [False]])
    # Her False -> True ... True -> False aralığı bir çıta
    degisim = np.flatnonzero(vadi[1:] != vadi[:-1])
    return [(kenar + (a + b - 1) / 2) / n for a, b in zip(degisim[::2], degisim[1::2])]

def bolme_say(profil):
    """Profildeki çıtalardan bölme sayısı (IZGARA_ARALIGI içinde)."""
    return int(np.clip(len(cita_konumlari(profil)) + 1, *IZGARA_ARALIGI))

def alt_cam_orani(profil):
    """Yukarıdan aşağı satır profilinde en alt camın üstteki camların ortalamasına oranı (yatay çıta yoksa None)."""
    sinirlar = [0.0, *cita_konumlari(profil), 1.0]
    boylar = np.diff(sinirlar)
    if len(boylar) < 2 or boylar[:-1].mean() <= 0:
        return None
    return float(boylar[-1] / boylar[:-1].mean())

def pencere_dokusu(img, quad, pay=0.04, maks=1200):
    """Odadaki pencerenin fotoğraftaki görüntüsü: perspektifi düzeltilmiş (yan duvardaki yamuk pencere dik
    dikdörtgene açılır), kasası da görünsün diye biraz geniş kesilmiş BGR resim (uzun kenarı en fazla maks)."""
    q = kose_noktalarini_sirala(np.float32(quad))
    merkez = q.mean(axis=0)
    q = merkez + (q - merkez) * (1 + pay)
    w = float(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))
    h = float(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])))
    o = min(1.0, maks / max(w, h, 1.0))
    dw, dh = max(8, int(round(w * o))), max(8, int(round(h * o)))
    M = cv2.getPerspectiveTransform(np.float32(q), np.float32([[0, 0], [dw, 0], [dw, dh], [0, dh]]))
    return cv2.warpPerspective(img, M, (dw, dh), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)

def kasali_pencereleri_bul(img):
    """Odanın sağ / sol yanındaki boydan pencereleri (çoğu kadrajda kesik) kasa çizgilerinden bulur: iki uzun dikey
    çizgi (ya da çizgi ve resmin kenarı) arasında kalan, duvardan belirgin farklı (manzara, ağaç, gece ışıkları ya da
    parlak gökyüzü) bölge. Düz duvar, tablo kadar dokulu ya da parlak olmadığı için elenir.
    Dönüş: isik_kaynaklarini_bul'daki gibi pencere sözlükleri (tam boy koordinatlarda, "kenar": True)."""
    H, W = img.shape[:2]
    olcek = min(1.0, 800.0 / max(H, W))
    kucuk = cv2.resize(img, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA) if olcek < 1 else img
    h, w = kucuk.shape[:2]
    gray = cv2.cvtColor(kucuk, cv2.COLOR_BGR2GRAY).astype(np.float32)
    # Detay yoğunluğu: belirgin kenar (gradyan > 40) olan piksellerin oranı. Test odalarında pencere (manzara,
    # ağaç, gece ışıkları, çıtalar) 0.25-0.8; düz duvar, sütun ve pilastır 0.01-0.18.
    detay = (cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)) > 40)
    # Gece manzarası: koyu, doygun mavi (şehir gökyüzü)
    hsv = cv2.cvtColor(kucuk, cv2.COLOR_BGR2HSV)
    gece = (hsv[..., 0] > 95) & (hsv[..., 0] < 135) & (hsv[..., 1] > 50) & (hsv[..., 2] < 140)

    # Uzun dikey çizgiler (resmin yüksekliğinin en az %30'u, dikeyden en fazla ~6°)
    kenarlar = cv2.Canny(cv2.GaussianBlur(gray.astype(np.uint8), (3, 3), 0), 40, 120)
    parcalar = cv2.HoughLinesP(kenarlar, 1, np.pi / 180, threshold=int(0.15 * h), minLineLength=int(0.3 * h),
                               maxLineGap=int(0.02 * h))
    cizgiler = []   # (x, üst, alt)
    for x1, y1, x2, y2 in (parcalar.reshape(-1, 4) if parcalar is not None else []):
        if abs(x2 - x1) <= 0.1 * abs(y2 - y1):
            x = (x1 + x2) / 2
            ust_y, alt_y = min(y1, y2), max(y1, y2)
            for c in cizgiler:   # aynı çizginin parçaları birleşir
                if abs(c[0] - x) < 0.01 * w:
                    c[1], c[2] = min(c[1], ust_y), max(c[2], alt_y)
                    break
            else:
                cizgiler.append([x, ust_y, alt_y])
    xs = sorted([[0.0, None, None]] + cizgiler + [[w - 1.0, None, None]], key=lambda c: c[0])

    adaylar = []
    for i in range(len(xs) - 1):
        for j in range(i + 1, len(xs)):
            a, b = xs[i], xs[j]
            gen = b[0] - a[0]
            if gen < 0.03 * w:
                continue
            if gen > 0.35 * w:
                break
            # Kadrajın kenarına dayanan pencere (resmin ortasındaki dokulu bölge çoğunlukla tablo / mobilyadır)
            if a[1] is not None and b[1] is not None:
                continue
            uclar = [c for c in (a, b) if c[1] is not None]
            if not uclar:
                continue
            y0 = min(c[1] for c in uclar)
            y1 = min(max(c[2] for c in uclar), 0.85 * h)
            if y0 > 0.4 * h or y1 - y0 < 0.4 * h:
                continue
            x0, x1 = int(a[0] + 0.01 * w), int(b[0] - 0.01 * w)
            if x1 - x0 < 3:
                continue
            bolge = (slice(int(y0), int(y1)), slice(x0, x1))
            yogunluk = float(detay[bolge].mean())
            yanik = float((gray[bolge] > 245).mean())        # gökyüzü: fotoğrafta beyaza kaçmış cam
            # Detaylı olması yetmez (heykel, lamba, ahşap çıta, pervaz da detaylıdır): gündüz penceresinde gökyüzü
            # beyaza kaçar, gece penceresinde koyu mavi şehir görünür. Test odalarında gerçek pencerelerde
            # yanık %9-48 ya da gece %27-34; yanlış adaylarda yanık %0, gece en fazla %2.
            gece_orani = float(gece[bolge].mean())
            if yogunluk > 0.22 and (yanik > 0.08 or gece_orani > 0.15) or yanik > 0.2:
                puan = gen * (y1 - y0) * (yogunluk + yanik)
                adaylar.append((puan, a[0], y0, b[0], y1, float(gray[bolge].mean()), yogunluk, yanik))

    # Örtüşmeyenler seçilir (en yüksek puandan)
    secilen = []
    for puan, x0, y0, x1, y1, parlak, yogunluk, yanik in sorted(adaylar, reverse=True):
        if all(x1 <= s[0] or x0 >= s[2] for s in secilen):
            secilen.append((x0, y0, x1, y1, parlak, yogunluk, yanik))
    # Pencere sütunu: yatay kasa çıtaları ve dışarıdaki manzaranın yatay kenarları vardır (test odalarında sütunların
    # %8-50'si); düz duvarda yoktur (%0-5). Pencerenin yanındaki duvar şeridi camda yansıyınca perde gibi
    # göründüğü için kesilen bölgeden atılır.
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)) / 4
    yatay_kenar = (gy > 12).astype(np.float32)
    pencereler = []
    for x0, y0, x1, y1, parlak, yogunluk, yanik in secilen[:2]:
        sutun = yatay_kenar[int(y0):int(y1), int(x0):int(x1)].mean(axis=0)
        sutun = cv2.blur(sutun.reshape(1, -1), (max(3, int(0.01 * w)), 1)).ravel()
        sag_kenarda = x1 >= w - 2
        icten = sutun if sag_kenarda else sutun[::-1]      # odaya bakan uçtan başla
        kirp = 0
        while kirp < len(icten) - 1 and icten[kirp] < 0.06:
            kirp += 1
        pay = int(0.01 * w)   # pencere kasası görünsün
        if sag_kenarda:
            x0 = max(x0, x0 + kirp - pay)
        else:
            x1 = min(x1, x1 - kirp + pay)
        if x1 - x0 < 0.02 * w:
            continue   # geriye pencere kalmadı (düz duvar şeridiydi)
        quad = np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        duz = gray[int(y0):int(y1), int(x0):int(x1)] / 255.0
        maske = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(maske, quad.astype(np.int32), 255)
        tam = quad / olcek
        pencereler.append({
            "quad": tam, "nx": bolme_say(duz.mean(axis=0)), "ny": bolme_say(duz.mean(axis=1)),
            "alt_oran": None, "renk": np.array(cv2.mean(kucuk, mask=maske)[:3]),
            "parlaklik": parlak / 255.0, "alan": float((x1 - x0) * (y1 - y0)) / olcek ** 2, "egim": 0.0,
            "oran": float((x1 - x0) / max(y1 - y0, 1)), "kenar": True, "yogunluk": yogunluk, "yanik": yanik,
            "doku": pencere_dokusu(img, tam, pay=0.0),
            "sira": pencere_sira_sayisi(pencere_dokusu(img, tam, pay=0.0)),   # ölçülebildiyse cam sıra sayısı
            # Kalan duvar sütunları (kasa arası duvar vb.) zayıf yansısın
            "doku_maske": pencere_dokusu(cv2.resize(
                (np.clip(cv2.blur(yatay_kenar, (max(3, int(0.01 * w)), int(0.3 * h))) / 0.06, 0.25, 1) * 255
                 ).astype(np.uint8), (W, H)), tam, pay=0.0),
        })
    return pencereler

def perde_bul(img):
    """Odada perde görünüyor mu: açık renkli, renksiz kumaş; yatayda ince kıvrımlar, dikeyde neredeyse hiç değişim
    yok ve tavandan yere kadar sürer. Kıvrım kontrastı yumuşaktır (pilastır, kapı / pencere kasası keskin kenarlıdır).
    Test odalarında tül perdeli iki oda (0030, 0040) bulunur, diğer 26 odada perde bulunmaz.
    Dönüş: {"x0", "x1" (tam boy piksel), "renk" (BGR)} ya da None."""
    H, W = img.shape[:2]
    olcek = min(1.0, 800.0 / max(H, W))
    k = cv2.resize(img, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA) if olcek < 1 else img
    h, w = k.shape[:2]
    g = cv2.cvtColor(k, cv2.COLOR_BGR2GRAY).astype(np.float32)
    doygunluk = cv2.cvtColor(k, cv2.COLOR_BGR2HSV)[..., 1]
    yatay = np.abs(g - cv2.GaussianBlur(g, (0, 0), sigmaX=2.0, sigmaY=0.01))
    dikey = np.abs(g - cv2.GaussianBlur(g, (0, 0), sigmaX=0.01, sigmaY=4.0))
    yatay = cv2.GaussianBlur(yatay, (0, 0), sigmaX=1.5, sigmaY=6)
    dikey = cv2.GaussianBlur(dikey, (0, 0), sigmaX=1.5, sigmaY=6)
    kumas = (g > 175) & (doygunluk < 40) & (yatay > 0.7) & (yatay < 7) & (dikey < 0.45 * yatay)
    ys = slice(int(0.08 * h), int(0.88 * h))
    sutun = cv2.blur(kumas[ys].mean(axis=0).reshape(1, -1).astype(np.float32), (max(3, int(0.02 * w)), 1)).ravel()

    # Kumaş sütunlarının en geniş kesintisiz bölgesi
    en, bas = (0, 0, 0), None
    for i, v in enumerate(list(sutun > 0.45) + [False]):
        if v and bas is None:
            bas = i
        elif not v and bas is not None:
            en = max(en, (i - bas, bas, i))
            bas = None
    genislik, x0, x1 = en
    if genislik < 0.03 * w:
        return None
    profil = g[ys, x0:x1].mean(axis=0)
    kivrim = profil - cv2.GaussianBlur(profil.reshape(1, -1), (0, 0), 3).ravel()
    if float(np.abs(kivrim).max()) > 30:
        return None   # keskin kenarlı: pilastır / kasa
    bolge = kumas[ys, x0:x1]
    renk = k[ys, x0:x1][bolge].mean(axis=0) if bolge.any() else np.float32([235, 235, 235])
    return {"x0": x0 / olcek, "x1": x1 / olcek, "renk": np.float32(renk)}

def tavan_pencerelerini_bul(img):
    """Tavandaki ışıklıklar (tavan penceresi): fotoğrafın üst kısmında, üst kenara değen ya da çok yakın, çevresindeki
    tavan / duvardan belirgin parlak ve gökyüzü gibi hafif mavi (ya da tamamen beyaz) bölge; camları ayıran çıtalar
    kapatılıp tek bölge sayılır. Örnek: galeri fotoğrafı, tavanda boydan boya ışıklık (cam L 237-247, b -3..-6;
    tavan L ~190, b +6; duvar L 209-228, b +2..+3). Tüm genişliği kaplayan parlak bant (aşırı pozlanmış beyaz tavan)
    sayılmaz. Dönüş: pencere sözlükleri (tam boy koordinatlar, "tavan": True)."""
    H, W = img.shape[:2]
    olcek = min(1.0, 800.0 / max(H, W))
    k = cv2.resize(img, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA) if olcek < 1 else img
    h, w = k.shape[:2]
    lab = cv2.cvtColor(k, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, b = lab[..., 0], lab[..., 2] - 128
    doy = cv2.cvtColor(k, cv2.COLOR_BGR2HSV)[..., 1]
    ust = int(0.45 * h)
    maske = (((L >= 240) | ((L >= 222) & (b <= -2))) & (doy < 60)).astype(np.uint8) * 255
    maske[ust:] = 0
    kap = max(3, int(0.02 * max(h, w))) | 1
    maske = cv2.morphologyEx(maske, cv2.MORPH_CLOSE, np.ones((kap, kap), np.uint8))
    maske = cv2.morphologyEx(maske, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    sayi, etiket, istat, _ = cv2.connectedComponentsWithStats(maske)
    sonuc = []
    for i in range(1, sayi):
        x, y, bw, bh, alan = istat[i]
        if alan < 0.01 * h * w or y > 0.03 * h or bw > 0.9 * w or bh > 0.35 * h or bw < 2 * bh * 0.5:
            continue
        bolge = etiket == i
        # Çevresindeki tavan / duvardan belirgin parlak olmalı (beyaz duvarın parlak yeri pencere sayılmasın)
        cevre = cv2.dilate(bolge.astype(np.uint8), np.ones((2 * kap + 1, 2 * kap + 1), np.uint8)).astype(bool) & ~bolge
        if not cevre.any() or float(L[bolge].mean()) - float(np.median(L[cevre])) < 12:
            continue
        quad = np.float32([[x, y], [x + bw, y], [x + bw, y + bh], [x, y + bh]]) / olcek
        sonuc.append({"quad": quad, "nx": 1, "ny": 1, "alan": float(alan) / olcek ** 2, "oran": float(bw / max(1, bh)),
                      "renk": np.float32(cv2.mean(k, mask=bolge.astype(np.uint8))[:3]),
                      "parlaklik": float(L[bolge].mean()) / 255.0, "egim": 0.0, "tavan": True})
    sonuc.sort(key=lambda p: p["alan"], reverse=True)
    return sonuc

def isik_kaynaklarini_bul(img):
    """Odadaki pencereleri (şekil, açı, bölme sayısı, renk) ve en güçlü ışık kaynağını bulur."""
    H, W = img.shape[:2]
    olcek = min(1.0, 800.0 / max(H, W))
    kucuk = cv2.resize(img, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA) if olcek < 1 else img
    h, w = kucuk.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(kucuk, cv2.COLOR_BGR2GRAY), (3, 3), 0)

    esik = max(200, np.percentile(gray, 97))
    parlak = ((gray >= esik) * 255).astype(np.uint8)
    # Çıtalarla bölünmüş camları tek pencere olarak birleştir
    k = max(5, int(0.025 * max(h, w)))
    birlesik = cv2.morphologyEx(parlak, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(birlesik, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    pencereler = []
    for c in contours:
        alan = cv2.contourArea(c)
        if alan < 0.004 * h * w or alan > 0.45 * h * w:
            continue
        rect = cv2.minAreaRect(c)
        rw, rh = rect[1]
        x, y, bw, bh = cv2.boundingRect(c)
        kenar = False
        # Yan duvardaki boydan pencere yandan bakınca dar görünür: en-boy sınırı 5
        if rw * rh == 0 or alan / (rw * rh) < 0.6 or max(rw, rh) / min(rw, rh) > 5:
            continue  # dikdörtgen olmayan parlak alanlar (lamba vb.) pencere sayılmaz
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) != 4 and alan / (rw * rh) < 0.85:
            continue  # yuvarlak / düzensiz parlak alan (avize, abajur) -> pencere değil
        quad = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(rect)
        quad = kose_noktalarini_sirala(np.float32(quad))

        maske = np.zeros((h, w), np.uint8)
        cv2.drawContours(maske, [c], -1, 255, -1)
        if y > 0.8 * h:
            continue  # tamamen resmin altında: parlak zemindeki yansıma, pencere değil
        cam = (cv2.bitwise_and(parlak, maske)[y:y + bh, x:x + bw] / 255.0)
        if cam.sum() / max(1, cv2.countNonZero(maske[y:y + bh, x:x + bw])) < 0.3:
            continue  # içi parlak değil, sadece kenarı parlak: beyaz çerçeve / kasa (pencerenin camları dolu parlar)

        # Yan duvardaki pencere yamuk görünür: yakın kenarı uzak kenarından uzundur.
        # egim > 0: sol kenar uzun (sol duvarda, odaya sağa bakıyor); egim < 0: sağ duvarda
        sol_boy = np.linalg.norm(quad[3] - quad[0])
        sag_boy = np.linalg.norm(quad[2] - quad[1])
        en = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
        egim = (sol_boy - sag_boy) / max(sol_boy + sag_boy, 1e-6)
        en /= max(0.35, 1 - 2.5 * abs(egim))   # yandan bakınca kısalan genişliği yaklaşık geri aç

        # Bölmeler, pencere perspektifle düz bir dikdörtgene açılıp sayılır: yan duvardaki yamuk pencerede
        # eğik çıtalar ve çevreleyen kutunun boş köşeleri sahte çıta gibi görünmesin
        dw = max(12, int(round(max(np.linalg.norm(quad[1] - quad[0]), np.linalg.norm(quad[2] - quad[3])))))
        dh = max(12, int(round(max(sol_boy, sag_boy))))
        M = cv2.getPerspectiveTransform(np.float32(quad), np.float32([[0, 0], [dw, 0], [dw, dh], [0, dh]]))
        duz = cv2.warpPerspective(parlak, M, (dw, dh)).astype(np.float32) / 255.0

        pencereler.append({
            "quad": quad / olcek,
            "nx": bolme_say(duz.mean(axis=0)),
            "ny": bolme_say(duz.mean(axis=1)),
            "alt_oran": alt_cam_orani(duz.mean(axis=1)),   # gerçek pencerede alt camın boyu
            "renk": np.array(cv2.mean(kucuk, mask=maske)[:3]),
            "parlaklik": cv2.mean(gray, mask=maske)[0] / 255.0,
            "alan": alan / (olcek ** 2),
            "egim": float(egim),
            "oran": float(en / max(sol_boy, sag_boy, 1e-6)),   # yaklaşık gerçek en-boy
            "kenar": bool(kenar),                               # kadrajın kenarında kesilmiş pencere
            # Yansıma için pencerenin düzeltilmiş görüntüsü ve hangi kısmının parlak cam olduğu (kesilen kutudaki
            # duvar / kasa yansımaya girmesin)
            "doku": pencere_dokusu(img, quad / olcek),
            "doku_maske": pencere_dokusu(cv2.resize(birlesik, (W, H), interpolation=cv2.INTER_LINEAR), quad / olcek),
        })

    # Kasa çizgileriyle bulunan pencereler (parlak olmasa da: yeşillik, gece manzarası). Parlaklıkla bulunan bir
    # pencereyle büyük ölçüde örtüşen parlak alan atılır (aynı pencere; kasa çizgileriyle bulunan daha doğru)
    for p in kasali_pencereleri_bul(img):
        x0, y0 = p["quad"].min(axis=0); x1, y1 = p["quad"].max(axis=0)

        def ortusme(q):
            a0, b0 = q["quad"].min(axis=0); a1, b1 = q["quad"].max(axis=0)
            kesisim = max(0, min(x1, a1) - max(x0, a0)) * max(0, min(y1, b1) - max(y0, b0))
            return kesisim / max(1.0, (a1 - a0) * (b1 - b0))
        pencereler = [q for q in pencereler if ortusme(q) < 0.3] + [p]
    pencereler.sort(key=lambda p: p["alan"] * max(p["parlaklik"], 0.6), reverse=True)

    # Odada perde görünüyorsa: perdeli pencere kadrajın kenarından perdenin iç kenarına kadar fotoğraftan alınır
    # (camda perdesi, rengi ve kasasıyla aynen yansısın). Öndeki diğer pencerelerin önüne geçer.
    perde = perde_bul(img)
    if perde is not None and (perde["x0"] < 0.3 * W or perde["x1"] > 0.7 * W):
        if perde["x0"] < 0.3 * W:
            px0, px1 = 0.0, min(perde["x1"] + 0.01 * W, 0.35 * W)
        else:
            px0, px1 = max(perde["x0"] - 0.01 * W, 0.65 * W), float(W - 1)
        py0, py1 = 0.04 * H, 0.85 * H
        tam = np.float32([[px0, py0], [px1, py0], [px1, py1], [px0, py1]])
        pencereler = [q for q in pencereler if not (q.get("kenar") and
                      min(q["quad"][:, 0].max(), px1) - max(q["quad"][:, 0].min(), px0) > 0)]
        pencereler.insert(0, {
            "quad": tam, "nx": 1, "ny": 1, "alt_oran": None, "renk": np.float32(perde["renk"]),
            "parlaklik": float(np.mean(perde["renk"])) / 255.0, "alan": float((px1 - px0) * (py1 - py0)),
            "egim": 0.0, "oran": float((px1 - px0) / (py1 - py0)), "kenar": True, "perdeli": True,
            "doku": pencere_dokusu(img, tam, pay=0.0), "doku_maske": None,
        })

    # Pencere yoksa kullanılacak genel ışık kaynağı: büyük ölçekte en parlak bölge
    yumusak = cv2.GaussianBlur(gray, (0, 0), 0.05 * max(h, w))
    _, maks, _, konum = cv2.minMaxLoc(yumusak)
    r = max(3, int(0.05 * max(h, w)))
    bolge = kucuk[max(0, konum[1] - r):konum[1] + r, max(0, konum[0] - r):konum[0] + r]
    isik = {
        "konum": np.array(konum, dtype=np.float32) / olcek,
        "parlaklik": maks / 255.0,
        "renk": bolge.reshape(-1, 3).mean(axis=0),
    }

    # Kadraj dışındaki pencere: odanın sol ve sağ kenarlarının (zemin hariç) parlaklık farkından ışığın
    # hangi yandan geldiği (otomatik yansıma pencere bulamazsa bunu kullanır)
    serit = max(2, int(0.18 * w))
    ust, alt = int(0.05 * h), int(0.75 * h)
    sol = float(gray[ust:alt, :serit].mean())
    sag = float(gray[ust:alt, -serit:].mean())
    fark = (sag - sol) / max(sag + sol, 1.0)
    yan_taraf = kucuk[ust:alt, -serit:] if fark >= 0 else kucuk[ust:alt, :serit]
    yan_isik = {
        "taraf": 0 if abs(fark) < 0.04 else (1 if fark > 0 else -1),
        "fark": float(fark),
        "parlaklik": max(sol, sag) / 255.0,
        "renk": yan_taraf.reshape(-1, 3).mean(axis=0),
    }
    # Işığın tarafı için odanın küçük gri kopyası (tablo yeri belli olunca onun dışındaki parlaklığa bakılır)
    gri_kucuk = cv2.resize(gray, (256, max(1, int(round(256 * h / w)))), interpolation=cv2.INTER_AREA)
    return {"pencereler": pencereler[:3], "isik": isik, "yan_isik": yan_isik, "boyut": (W, H),
            "tavan_pencereleri": tavan_pencerelerini_bul(img),   # ışıklıklar: sadece üst ışık için kullanılır
            "gri_kucuk": gri_kucuk,
            "perde": perde,   # odada görünen perde (yoksa None): yansımaya sadece o zaman perde eklenir
            "ortam": float(gray.mean()) / 255.0}   # odanın genel aydınlığı (otomatik yansıma gücü için)

def _pencere_yapisi_guvenilir(p):
    """En-boyu pencereye benzemeyen (ince şerit) ya da dörtgeni üçgene dönmüş parlak alanın (yerdeki güneş lekesi,
    perde parlaması) bölmelerine / çıtalarına güvenilmez; bu alanlar sadece ışığın yönü için kullanılır.
    Kadrajın kenarında kesilmiş boydan pencere ince görünse de gerçek penceredir."""
    if p.get("kenar"):
        return True
    q = np.asarray(p["quad"], np.float32)
    kenarlar = [np.linalg.norm(q[(i + 1) % 4] - q[i]) for i in range(4)]
    return 0.25 <= p.get("oran", 0.8) <= 4.0 and min(kenarlar) >= 0.2 * max(kenarlar)


def _sanal_pencere(merkez, r, renk, parlaklik, egim=0.0):
    """Pencere görünmeyen odada ışığın geldiği yerde varsayılan 2x2 bölmeli pencere."""
    merkez = np.float32(merkez)
    return {"quad": np.float32([merkez + [-r, -r], merkez + [r, -r], merkez + [r, r], merkez + [-r, r]]),
            "nx": 2, "ny": 2, "renk": 0.6 * np.asarray(renk) + 0.4 * 255, "parlaklik": parlaklik,
            "egim": egim, "oran": 0.8, "alan": 4 * r * r}


def yansima_penceresi(analiz, tablo_pts, oto=False):
    """Yansıması çizilecek tek pencere: en büyük / en parlak pencere (camda tek yansıma olur).
    Pencere görünmüyorsa ışığın geldiği yerde 2x2 bölmeli sanal pencere; otomatik modda ışık bir yandan
    geliyorsa o yandaki (kadraj dışı) pencere."""
    W, H = analiz["boyut"]
    tablo_pts = np.float32(tablo_pts)
    tablo_kontur = tablo_pts.reshape(-1, 1, 2)
    tx0, tx1 = float(tablo_pts[:, 0].min()), float(tablo_pts[:, 0].max())

    def tabloyla_ortusuyor(p):
        """Tablonun kendisi / içi pencere sanılmasın; kenardaki pencere tablonun yatay aralığına taşmasın
        (eski tablonun parçası yeni tablonun yansımasına girmesin)."""
        if cv2.pointPolygonTest(tablo_kontur, tuple(map(float, p["quad"].mean(axis=0))), False) >= 0:
            return True
        if p.get("kenar"):
            px0, px1 = float(p["quad"][:, 0].min()), float(p["quad"][:, 0].max())
            return min(px1, tx1) - max(px0, tx0) > 0.03 * (tx1 - tx0)
        return False
    pencereler = [p for p in analiz["pencereler"] if not tabloyla_ortusuyor(p)]
    if pencereler:
        return pencereler[0]
    yan = analiz.get("yan_isik") or {"taraf": 0}
    if oto and yan["taraf"]:
        # Işık bir yandan geliyor: o yanda, tablonun üst hizasında yan duvara bakan sanal pencere
        nokta = tablo_pts.mean(axis=0) + np.float32([yan["taraf"] * 0.55, -0.15]) * 0.5 * np.hypot(W, H)
        return _sanal_pencere(nokta, 0.08 * np.hypot(W, H), yan["renk"], yan["parlaklik"], egim=-0.15 * yan["taraf"])
    isik = analiz["isik"]
    return _sanal_pencere(isik["konum"], 0.08 * np.hypot(W, H), isik["renk"], isik["parlaklik"])


def isik_tarafi(analiz, tablo_pts):
    """Işığın (yansımanın) tablonun hangi yanından geldiği: -1 sol, +1 sağ.
    Odada pencere gibi görünen (güvenilir) bir pencere varsa onun tarafı. Yoksa odanın üst %60'ında (zemin,
    kanepe, yatak gibi açık renkli eşyalar sayılmasın), tablonun kendisi hariç üç ölçünün çoğunluğu:
      - en parlak bölge (bulanık; tablonun hizasındaysa sağ / sol duvar şeritlerinin farkı)
      - en parlak %2'lik piksellerin ağırlık merkezi (pencere ışığı, güneş lekesi)
      - tablonun solundaki ve sağındaki üst bölgenin ortalama parlaklığı
    Test odalarında elle seçilen tarafla 28 odanın 27'sinde, oda sunumlarındaki yansımalarla 12'nin 9'unda aynı
    (tek başına en parlak bölge: 24/28 ve 9/12; eski kural: 23/28 ve 10/12)."""
    W, H = analiz["boyut"]
    tablo_pts = kose_noktalarini_sirala(np.float32(tablo_pts))
    merkez_x = float(tablo_pts[:, 0].mean())
    yan_isareti = 1.0 if (analiz.get("yan_isik") or {}).get("fark", 0.0) > 0 else -1.0
    p = yansima_penceresi(analiz, tablo_pts)
    if any(p is q for q in analiz["pencereler"]) and _pencere_yapisi_guvenilir(p):
        dx = float(p["quad"].mean(axis=0)[0]) - merkez_x
        if abs(dx) >= 0.03 * W:
            return 1.0 if dx > 0 else -1.0
    gri = analiz.get("gri_kucuk")
    if gri is None:
        return yan_isareti
    hh, hw = gri.shape
    q = tablo_pts * [hw / W, hh / H]
    mx = merkez_x * hw / W
    maske = np.zeros((hh, hw), np.uint8)
    maske[:max(1, int(0.6 * hh))] = 255
    cv2.fillConvexPoly(maske, np.int32(np.round(q)), 0)
    if not cv2.countNonZero(maske):
        return yan_isareti
    gri = gri.astype(np.float32)
    oylar = []
    # 1) En parlak bölge
    _, _, _, konum = cv2.minMaxLoc(cv2.GaussianBlur(gri, (0, 0), 0.05 * max(hw, hh)), mask=maske)
    dx = konum[0] + 0.5 - mx
    oylar.append(np.sign(dx) if abs(dx) >= 0.03 * hw else yan_isareti)
    # 2) En parlak %2'lik piksellerin ağırlık merkezi
    secili = maske > 0
    ys, xs = np.nonzero(secili & (gri >= np.percentile(gri[secili], 98)))
    oylar.append(1.0 if xs.mean() + 0.5 > mx else -1.0)
    # 3) Tablonun solundaki / sağındaki üst bölgenin ortalaması
    ayrim = int(np.clip(round(mx), 1, hw - 1))
    sol = cv2.mean(gri[:, :ayrim], mask=maske[:, :ayrim])[0]
    sag = cv2.mean(gri[:, ayrim:], mask=maske[:, ayrim:])[0]
    oylar.append(1.0 if sag > sol else -1.0)
    return 1.0 if sum(oylar) > 0 else -1.0


# --- Otomatik yansıma: kullanıcının web'de elle ayarladığı 29 kayıtlı odadan (2026-10-29) çıkarıldı ---
# Yansıyan pencere ışık kenarına doğru kaydırılır, büyütülür ve Y ekseninde döndürülür; açı ışığın tarafına ve
# tablonun çerçeveli olup olmamasına göre değişir. Kontrast her zaman en yüksekte, temperli cam sadece çerçevesizde.
# (ışık tarafı, çerçevesiz): (dondur_y, kaydir_x, kaydir_y, boyut) — her gruptaki odaların ortancası
OTOMATIK_YANSIMA = {
    (1, False):  (-54,  25, -12, 135),
    (1, True):   (-44,  10,  -5, 120),
    (-1, False): ( 50, -20, -12, 135),
    (-1, True):  (-122, -20, -5, 120),
}
OTOMATIK_ANAHTARLAR = ("dondur_x", "dondur_y", "dondur_z", "kaydir_x", "kaydir_y", "boyut", "kontrast", "temperli")
# Işık üstten: yansıyan pencere camın üstünde ortada, üst kenarı biraz geriye yatık (aşağıdan bakılıyormuş gibi)
# çerçevesiz: (dondur_x, boyut)
OTOMATIK_UST = {False: (-30, 125), True: (-30, 115)}

def cercevesiz_mi(oda, tablo_pts, tip=None):
    """Tablonun çerçevesi yok mu (yuvarlak, çerçevesiz kanvas / cam): tablonun hemen dışındaki bant ile biraz daha
    dışındaki duvar aynı renkteyse çerçeve yoktur; çerçeve bandı duvardan belirgin farklıdır (kayıtlı 29 odanın
    27'sinde kullanıcının temperli seçimiyle aynı)."""
    if tip == "yuvarlak":
        return True
    q = kose_noktalarini_sirala(np.float32(tablo_pts))
    m = q.mean(axis=0)
    h, w = oda.shape[:2]
    dis = m + (q - m) * 1.14
    x0, y0 = np.maximum(np.floor(dis.min(axis=0)).astype(int), 0)
    x1, y1 = np.minimum(np.ceil(dis.max(axis=0)).astype(int), [w, h])
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    lab = cv2.cvtColor(oda[y0:y1, x0:x1], cv2.COLOR_BGR2LAB).astype(np.float32)

    def bant(ic, dis_f):
        maske = np.zeros(lab.shape[:2], np.uint8)
        cv2.fillConvexPoly(maske, np.int32(np.round(m + (q - m) * dis_f - [x0, y0])), 255)
        cv2.fillConvexPoly(maske, np.int32(np.round(m + (q - m) * ic - [x0, y0])), 0)
        v = lab[maske > 0]
        return v.mean(axis=0) if len(v) else None

    yakin, uzak = bant(1.005, 1.04), bant(1.08, 1.14)
    if yakin is None or uzak is None:
        return False
    return float(np.linalg.norm(yakin - uzak)) < 30

def otomatik_yansima_ayari(oda, analiz, tablo_pts, tip=None, ayar=None, cercevesiz=None):
    """"Otomatik Yansımaya Dön" / yeni oda: ayarın yansıma geometrisini, kontrastını ve temperli camını kullanıcının
    kendi odalarında seçtiği değerlere göre doldurur (OTOMATIK_YANSIMA). Güç, taraf ve temperli kaydırıcıları
    ayar'daki gibi kalır. cercevesiz verilmezse fotoğraftan tahmin edilir (boş duvara yerleştirilen tablo çerçevesizdir)."""
    a = yansima_ayari_duzelt(ayar)
    q = kose_noktalarini_sirala(np.float32(tablo_pts))
    if cercevesiz is None:
        cercevesiz = cercevesiz_mi(oda, q, tip)
    if a["taraf"] == "ust":
        dondur_x, boyut = OTOMATIK_UST[bool(cercevesiz)]
        a.update(dondur_x=dondur_x, dondur_y=0, dondur_z=0, kaydir_x=0, kaydir_y=0, boyut=boyut, kontrast=100,
                 temperli=bool(cercevesiz))
        return a
    if a["taraf"] != "oto":
        taraf = 1 if a["taraf"] == "sag" else -1
    else:
        taraf = 1 if analiz is None or isik_tarafi(analiz, q) > 0 else -1
    if a["taraf"] == "oto" and analiz is not None and _ustteki_pencere(analiz, q) is not None:
        # Otomatik: tablonun üstünde gerçek pencere var, ışık üstten
        dondur_x, boyut = OTOMATIK_UST[bool(cercevesiz)]
        a.update(dondur_x=dondur_x, dondur_y=0, dondur_z=0, kaydir_x=0, kaydir_y=0, boyut=boyut, kontrast=100,
                 temperli=bool(cercevesiz))
        return a
    dondur_y, kaydir_x, kaydir_y, boyut = OTOMATIK_YANSIMA[(taraf, bool(cercevesiz))]
    a.update(dondur_y=dondur_y, dondur_x=0, dondur_z=0, kaydir_x=kaydir_x, kaydir_y=kaydir_y, boyut=boyut,
             kontrast=100, temperli=bool(cercevesiz))
    return a

def otomatik_mi(ayar, otomatik):
    """Ayarın geometrisi otomatik değerlerde mi (elle değiştirilmemiş mi)."""
    return all(ayar.get(k) == otomatik[k] for k in OTOMATIK_ANAHTARLAR)

def _oda_tohumu(analiz):
    """Aynı oda her açılışta aynı yansımayı versin, farklı odalar farklı: odanın boyutu ve aydınlığından sabit sayı."""
    W, H = analiz["boyut"]
    return int(W) * 31 + int(H) * 17 + int(round(float(analiz.get("ortam", 0.5)) * 1000))

def _yansiyan_pencere(analiz, q, taraf):
    """Camda ayna gibi görünecek odadaki pencere: ışık tarafında, tablonun dışında kalan en büyük güvenilir
    pencere; yoksa None."""
    yakin = float(q[:, 0].max() if taraf > 0 else q[:, 0].min())
    adaylar = [p for p in analiz["pencereler"] if _pencere_yapisi_guvenilir(p)
               and (float(p["quad"][:, 0].min() if taraf > 0 else p["quad"][:, 0].max()) - yakin) * taraf > 0]
    return max(adaylar, key=lambda p: p["alan"]) if adaylar else None

def _ustteki_pencere(analiz, q):
    """Tablonun üstünde duran gerçek pencere (tavan penceresi, duvarın üst kısmındaki pencere): alt kenarı tablonun
    üst kenarı hizasında ya da üstünde, yatayda tablonun aralığıyla büyük ölçüde örtüşen, yeterince büyük, güvenilir
    pencere. Kadrajın yan kenarında kesik boydan pencereler ve perdeler sayılmaz (onlar sol / sağ ışıktır).
    Yoksa None. Sıkı tutulur: kullanıcının kayıtlı 29 odasının hiçbirinde bulunmaz (sol / sağ otomatikleri bozulmaz)."""
    tx0, tx1 = float(q[:, 0].min()), float(q[:, 0].max())
    ty0, ty1 = float(q[:, 1].min()), float(q[:, 1].max())
    tw, th = tx1 - tx0, ty1 - ty0
    adaylar = []
    for p in analiz["pencereler"]:
        if p.get("kenar") or p.get("perdeli") or not _pencere_yapisi_guvenilir(p):
            continue
        px0, px1 = float(p["quad"][:, 0].min()), float(p["quad"][:, 0].max())
        py1 = float(p["quad"][:, 1].max())
        en = px1 - px0
        ortusme = min(px1, tx1 + 0.15 * tw) - max(px0, tx0 - 0.15 * tw)
        if py1 <= ty0 + 0.05 * th and en > 0 and ortusme >= 0.6 * en and p["alan"] >= 0.03 * tw * th:
            adaylar.append(p)
    # Tavandaki ışıklık (fotoğrafın üst kenarında): tablonun üstünde, yatayda tablonun ortasını kapsıyorsa
    orta = 0.5 * (tx0 + tx1)
    for p in analiz.get("tavan_pencereleri", []):
        px0, px1 = float(p["quad"][:, 0].min()), float(p["quad"][:, 0].max())
        if float(p["quad"][:, 1].max()) <= ty0 and px0 <= orta <= px1 and p["alan"] >= 0.02 * tw * th:
            adaylar.append(p)
    return max(adaylar, key=lambda p: p["alan"]) if adaylar else None

def isik_yonu(analiz, tablo_pts):
    """Otomatik modda ışığın geldiği yön: "ust" (tablonun üstünde gerçek pencere varsa), yoksa "sag" / "sol"."""
    q = kose_noktalarini_sirala(np.float32(tablo_pts))
    if _ustteki_pencere(analiz, q) is not None:
        return "ust"
    return "sag" if isik_tarafi(analiz, q) > 0 else "sol"

def _pencere_perspektif_dortgeni(p, taraf):
    """Odadaki pencerenin fotoğraftaki perspektifli dörtgeni: [yakın üst, uzak üst, uzak alt, yakın alt] (tam boy
    piksel; "yakın" tabloya bakan kenar). Algılanan dörtgen düz kutudur; yan duvardaki pencerenin üst / alt kenarı
    perspektifle eğiktir ve kaçış noktasına doğru daralır: çıtaların eğimi yüksekliğe göre (eğim = a + b·y)
    ölçülüp üst ve alt kenar o eğimlerle çizilir."""
    x0, x1 = float(p["quad"][:, 0].min()), float(p["quad"][:, 0].max())
    y0, y1 = float(p["quad"][:, 1].min()), float(p["quad"][:, 1].max())
    yakin_x, uzak_x = (x0, x1) if taraf > 0 else (x1, x0)
    a, b = 0.0, 0.0
    if p.get("doku") is not None:
        egimler, ys = _yatay_cizgi_egimleri(p["doku"])
        if egimler:
            ys = y0 + np.float32(ys) / p["doku"].shape[0] * (y1 - y0)   # dokudaki yükseklik -> fotoğraftaki
            egimler = np.float32(egimler)
            if len(egimler) >= 3 and np.ptp(ys) > 0.2 * (y1 - y0):
                b, a = (float(v) for v in np.polyfit(ys, egimler, 1))
            else:
                a = float(np.median(egimler))
    m_ust = float(np.clip(a + b * y0, -0.8, 0.8))
    m_alt = float(np.clip(a + b * y1, -0.8, 0.8))
    en = uzak_x - yakin_x
    # Kenarlar kutunun içinde kalır: üst kenarın en yüksek ucu kutunun üstünde, alt kenarın en alçak ucu altında
    ust_yakin = y0 - min(0.0, m_ust * en)
    alt_yakin = y1 - max(0.0, m_alt * en)
    return np.float32([[yakin_x, ust_yakin], [uzak_x, ust_yakin + m_ust * en],
                       [uzak_x, alt_yakin + m_alt * en], [yakin_x, alt_yakin]])

def cam_yansimasi_plani(analiz, tablo_pts, ayar=None, tip=None):
    """Ayna gibi yansımanın planı (örnek: Referans Yansımalar klasörü). Camda odanın kendisi ayna gibi ters görünür.
    Plan, tablodaki her noktanın odanın fotoğrafında hangi noktayı yansıttığını veren 3x3 dönüşümdür (tam boy piksel).
      - Odada pencere varsa (referanslardaki gibi): yan duvarda perspektifle daralmış pencere aynada Y ekseni
        etrafında döndürülmüş gibi karşıdan görünür: tablonun ışık kenarından başlar, içeri doğru uzanır, dikey kasalar
        dik, yatay çıtalar tablonun kenarlarına paralel; içerik ayna gibi ters. Yansıyan pencere gerçeğinin ~%80'i
        boyunda, tablonun eninin %25-60'ı genişliğinde.
      - Pencere görünmüyorsa tablonun hemen yanındaki duvar ve eşyalar ayna ekseninden ters çevrilip yansır.
    ayar["cesit"] boyu / konumu biraz değiştirir; elle ayarlar kaydırır, büyütür ve Y ekseninde döndürür."""
    ayar = yansima_ayari_duzelt(ayar)
    q = kose_noktalarini_sirala(np.float32(tablo_pts))
    rng = np.random.default_rng(_oda_tohumu(analiz) + 7919 * ayar["cesit"])
    # Işık üstten: yansıyan pencere camın üstünde ortada; hangi pencerenin yansıyacağı ve aynanın sağ / sol yönü için
    # yine odanın ışık tarafına bakılır (plan["taraf"] üstte 0: kenar parlaması ve gölge tepeden)
    # Otomatikte tablonun üstünde gerçek pencere varsa ışık üstten sayılır
    ust_pencere = _ustteki_pencere(analiz, q) if ayar["taraf"] in ("oto", "ust") else None
    ust = ayar["taraf"] == "ust" or (ayar["taraf"] == "oto" and ust_pencere is not None)
    if ayar["taraf"] in ("oto", "ust"):
        taraf = isik_tarafi(analiz, q)
    else:
        taraf = 1.0 if ayar["taraf"] == "sag" else -1.0

    tx0, tx1 = float(q[:, 0].min()), float(q[:, 0].max())
    ty0, ty1 = float(q[:, 1].min()), float(q[:, 1].max())
    tw, th = tx1 - tx0, ty1 - ty0
    yakin = tx1 if taraf > 0 else tx0
    # Üst ışıkta yandaki pencere değil, tablonun üstündeki gerçek pencere yansır (yoksa örnek pencere)
    p = ust_pencere if ust else _yansiyan_pencere(analiz, q, taraf)
    olcek = 0.8
    kay_v = 0.0
    if ayar["cesit"]:
        olcek = float(rng.uniform(0.7, 0.9))     # başka bir çeşit: yansıma biraz farklı boyda / yükseklikte
        kay_v = float(rng.uniform(-0.08, 0.08))
    boyut = ayar["boyut"] / 100.0
    kx, ky = ayar["kaydir_x"] / 100.0, ayar["kaydir_y"] / 100.0

    # Odada pencere yoksa referans fotoğraflardan alınmış gerçek pencerelerden biri yansır (çeşide göre)
    ornek = None if p is not None else _pencere_ornegi(_oda_tohumu(analiz) + ayar["cesit"])

    if p is not None or ornek is not None:
        if p is not None and ust:
            # Üstteki pencere aynada dikeyde ters görünür: pencerenin tabloya yakın alt kenarı camın üstüne gelir
            pq = kose_noktalarini_sirala(np.float32(p["quad"]))
            kaynak = np.float32([pq[3], pq[2], pq[1], pq[0]])
            en_boy = float(np.clip(np.linalg.norm(pq[1] - pq[0]) / max(1.0, np.linalg.norm(pq[3] - pq[0])), 0.4,
                                   12.0 if p.get("tavan") else 2.5))
        elif p is not None:
            kaynak = _pencere_perspektif_dortgeni(p, taraf)
            en_boy = 0.62
        else:
            oh, ow = ornek.shape[:2]
            kaynak = np.float32([[0, 0], [ow, 0], [ow, oh], [0, oh]] if taraf > 0 else
                                [[ow, 0], [0, 0], [0, oh], [ow, oh]])
            en_boy = ow / oh
        # Referanslarda ölçülen oranlar (6 fotoğraf): yansıyan pencere camın boyunun ~%66'sı (%62-75), üstü camın
        # üstünden ~%17 aşağıda; eni boyunun ~%62'si kadar (camın %35-52'si; örnek pencerede kendi oranı); ışık
        # kenarına dayalı ya da biraz içeride. Pencerenin fotoğraftaki boyu ne olursa olsun bu oranlar
        hv = 0.66 * olcek / 0.8                                           # tablo boyuna oranla
        wd = float(np.clip(en_boy * hv * th / tw, 0.3, 0.55 if p is not None and not ust else 0.6)) * boyut   # tablo enine
        hv *= boyut
        if p is not None and p.get("tavan"):
            # Tavandaki ışıklık geniş ve basık bir şerittir: camın üstünde geniş, kendi en-boy oranında yansır
            wd = 0.8 * boyut * olcek / 0.8
            hv = float(np.clip(wd * tw / th / en_boy, 0.12, 0.6))
        vc = (0.03 if ust else 0.17) + hv / 2 + kay_v + ky                 # üst ışıkta camın üstüne dayalı
        # Pencere düzlemi kendi dikey ekseni etrafında döner (kullanıcı isteğiyle 0°'de bir önceki sürüme göre
        # 90° daha, yani ters yönde: pencerenin tabloya bakan kenarı camın içine, dış kenarı ışık kenarına gelir).
        # dondur_y ile -180..180° döndürülür (±180° önceki yön); yana döndükçe en daralır, uzaklaşan kenar kısalır.
        # Ayrıca dondur_x (yatay eksen: üst / alt kenar öne-arkaya yatar, yatan kenar kısalır) ve dondur_z (cam
        # düzleminde döner). Sıra: önce Y, sonra X, en son Z; X = Z = 0 iken sonuç sadece Y döndürmesiyle aynıdır.
        def _aci(derece):
            a = np.radians(derece)
            c, s = float(np.cos(a)), float(np.sin(a))
            if abs(c) < 0.05:                                              # tam yandan: çizgiye dönmesin
                c = 0.05 if c >= 0 else -0.05
            return c, s
        cos_a, sin_a = _aci(ayar["dondur_y"])
        cos_b, sin_b = _aci(ayar["dondur_x"])
        aci_z = np.radians(ayar["dondur_z"])
        cos_c, sin_c = float(np.cos(aci_z)), float(np.sin(aci_z))
        uzaklik = 2.5                                                      # bakış uzaklığı (pencere boyuna göre)
        # pencerenin ortası: ışık kenarına yakın; üst ışıkta yatayda ortada
        uc = (0.5 if ust else (1.0 if taraf > 0 else 0.0) - taraf * (0.03 + wd / 2)) + kx
        oran = tw / th                                                     # yatay birimi dikey birime çevirir

        def nokta(x_rel, y_rel):
            # x_rel: pencere düzleminde ortadan yatay uzaklık (tablo eni), y_rel: dikey (tablo boyu). Hesap tablo
            # boyu biriminde: X = x_rel * oran, Y = y_rel, Z = derinlik (+ uzağa)
            x, y = x_rel * oran, y_rel
            x, z = x * cos_a, x * sin_a                                    # Y ekseni
            y, z = y * cos_b - z * sin_b, y * sin_b + z * cos_b            # X ekseni
            x, y = x * cos_c - y * sin_c, x * sin_c + y * cos_c            # Z ekseni (cam düzleminde)
            p_olcek = 1.0 / max(0.3, 1.0 + z / hv / uzaklik)
            return [uc + x / oran * p_olcek, vc + y * p_olcek]

        yakin_x, uzak_x = -taraf * wd / 2, taraf * wd / 2                 # 0°: yakın kenar içeride
        hedef_birim = np.float32([nokta(yakin_x, -hv / 2), nokta(uzak_x, -hv / 2),
                                  nokta(uzak_x, hv / 2), nokta(yakin_x, hv / 2)])
        pencere_birim = cv2.convexHull(hedef_birim).reshape(-1, 2)
        birim = cv2.getPerspectiveTransform(np.float32([[0, 0], [1, 0], [1, 1], [0, 1]]), q)
        hedef = cv2.perspectiveTransform(hedef_birim.reshape(-1, 1, 2), birim).reshape(4, 2)
        donusum = cv2.getPerspectiveTransform(np.float32(hedef), kaynak).astype(np.float64)
    else:
        pencere_birim = None
        # Pencere görünmüyor ve örnek pencere de yok: ayna ekseni tablonun hemen yanında (üst ışıkta hemen üstünde);
        # yansıyan oda göz hizası etrafında küçülür
        dx, dy = kx * tw, ky * th
        s = max(0.2, olcek * boyut)
        if ust:
            # kaynak_x = orta + (x - dx - orta) / s ; kaynak_y = eksen - (y - dy - eksen) / s (tavan tarafı aynada)
            eksen = ty0 - 0.05 * th
            orta = 0.5 * (tx0 + tx1)
            donusum = np.array([[1 / s, 0, orta - (orta + dx) / s],
                                [0, -1 / s, eksen + (eksen + dy) / s],
                                [0, 0, 1]], np.float64)
        else:
            eksen = yakin + taraf * 0.05 * tw
            ufuk = 0.5 * (ty0 + ty1) + kay_v * th
            # kaynak_x = eksen - (x - dx - eksen) / s ; kaynak_y = ufuk + (y - dy - ufuk) / s
            donusum = np.array([[-1 / s, 0, eksen + (eksen + dx) / s],
                                [0, 1 / s, ufuk - (ufuk + dy) / s],
                                [0, 0, 1]], np.float64)

    return {
        "taraf": 0.0 if ust else taraf,                          # -1 sol, +1 sağ, 0 üst (kenar parlaması / gölge)
        "donusum": donusum,                                      # tablodaki nokta -> kaynaktaki nokta
        "ornek": ornek,                                          # kaynak örnek pencere resmi (yoksa odanın kendisi)
        "pencere_birim": pencere_birim,                          # yansıyan pencerenin tablodaki yeri (0-1)
        "tablo": q,                                              # odadaki tablo yeri: aynada kullanılmaz
        "guc": 0.5 * ayar["guc"] / 100.0,
        # Kontrast: odanın koyu kısımları camı bulandırmasın, pencere / açık yerler belirgin yansısın
        "kontrast_esik": 0.45 * ayar["kontrast"] / 100.0,
        "gama": 1.0 + ayar["kontrast"] / 100.0,
        "bulaniklik": ayar["bulaniklik"] / 100.0,
        "sicaklik": ayar["sicaklik"] / 100.0,
        "sonme": ayar["asagi_sonme"] / 100.0,
        "pencere_var": p is not None,
    }

def yansima_aciklamasi(plan):
    """Yansıma planının kısa açıklaması (bilgi satırı için)."""
    taraf = "üstte" if plan["taraf"] == 0 else "solda" if plan["taraf"] < 0 else "sağda"
    if plan["pencere_var"]:
        return f"odanın ayna yansıması, odadaki pencere camda görünür ({taraf})"
    if plan.get("ornek") is not None:
        return f"odada pencere yok: örnek pencerenin ayna yansıması ({taraf})"
    return f"odanın ayna yansıması ({taraf} taraftaki duvar)"

# Odada pencere görünmüyorsa yansıyacak gerçek pencereler: referans fotoğraflardaki (Referans Yansımalar, örnek
# yansımalar) odalardan kırpılmış pencereler. cekirdek.py'nin yanındaki klasörde durur (masaüstünde de yerel)
PENCERE_ORNEK_KLASORU = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pencere_ornekleri")
_pencere_ornekleri = None

def _pencere_ornegi(sira):
    """sira'ya göre (oda + çeşit) bir örnek pencere resmi (BGR); klasör boşsa None."""
    global _pencere_ornekleri
    if _pencere_ornekleri is None:
        yollar = sorted(glob.glob(os.path.join(PENCERE_ORNEK_KLASORU, "*.jpg")))
        _pencere_ornekleri = [r for r in (turkce_karakter_destekli_imread(y) for y in yollar) if r is not None]
    if not _pencere_ornekleri:
        return None
    return _pencere_ornekleri[int(sira) % len(_pencere_ornekleri)]

def ayna_katmani(oda, cizim_pts, dst_pts, sw, sh, plan):
    """Tablo düzleminde (sw x sh) ayna yansıması katmanı (BGR, 0-1): tablonun her noktası plan["donusum"] ile odanın
    fotoğrafındaki kaynağından örneklenir. Ekran (screen) karışımıyla eklenir: açık yerler (pencere) belirgin, koyu
    yerler neredeyse görünmez. oda / cizim_pts çizilen (gerekirse küçültülmüş) oda ve tablo köşeleri; plan tam boy
    (dst_pts) koordinatlarında."""
    cizim_pts = np.float32(cizim_pts)
    dst = kose_noktalarini_sirala(np.float32(dst_pts))
    k = float(np.linalg.norm(cizim_pts[1] - cizim_pts[0]) / max(1e-6, np.linalg.norm(dst[1] - dst[0])))
    olcek = np.diag([k, k, 1.0])
    ornek = plan.get("ornek")
    # Çizim koordinatlarında; kaynak örnek pencere resmiyse onun kendi pikselleri (ölçeklenmez)
    D = (np.eye(3) if ornek is not None else olcek) @ plan["donusum"] @ np.linalg.inv(olcek)

    # Tablo düzlemindeki her noktanın odadaki yeri, oradan aynadaki kaynağı
    H = cv2.getPerspectiveTransform(np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]]), cizim_pts).astype(np.float64)
    us, vs = np.meshgrid(np.arange(sw, dtype=np.float64) + 0.5, np.arange(sh, dtype=np.float64) + 0.5)
    T = D @ H
    w = T[2, 0] * us + T[2, 1] * vs + T[2, 2]
    on = w > 1e-6                                             # dönüşümün arkasına düşen (geçersiz) noktalar
    w = np.where(on, w, 1.0)
    kaynak_x = ((T[0, 0] * us + T[0, 1] * vs + T[0, 2]) / w).astype(np.float32)
    kaynak_y = ((T[1, 0] * us + T[1, 1] * vs + T[1, 2]) / w).astype(np.float32)
    kaynak_x[~on] = -1e5

    if ornek is not None:
        kaynak_oda = ornek
    else:
        # Odadaki (eski) tablo cama yansımaz: yeri çevresindeki duvarın rengiyle doldurulur (aynada boş duvar)
        oh, ow = oda.shape[:2]
        tablo_maske = np.zeros((oh, ow), np.uint8)
        cv2.fillConvexPoly(tablo_maske, np.int32(np.round(kose_noktalarini_sirala(np.float32(plan["tablo"]) * k))), 255)
        pay = max(3, int(0.03 * max(oh, ow)))
        halka = cv2.subtract(cv2.dilate(tablo_maske, np.ones((2 * pay + 1, 2 * pay + 1), np.uint8)), tablo_maske)
        kaynak_oda = oda.copy()
        if cv2.countNonZero(halka):
            kaynak_oda[tablo_maske > 0] = np.uint8(cv2.mean(oda, mask=halka)[:3])
    oh, ow = kaynak_oda.shape[:2]
    R = cv2.remap(kaynak_oda, kaynak_x, kaynak_y, cv2.INTER_LINEAR,
                  borderMode=cv2.BORDER_REPLICATE).astype(np.float32) / 255.0

    # Fotoğrafın dışına düşen kısım bilinmiyor: orası odanın ortalama, koyuca tonunda kalır (yumuşak geçişle)
    gecerli = cv2.remap(np.full((oh, ow), 255, np.uint8), kaynak_x, kaynak_y, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT, borderValue=0).astype(np.float32) / 255.0
    gecerli = cv2.GaussianBlur(gecerli, (0, 0), 0.01 * max(sw, sh))[..., None]
    dolgu = 0.5 * np.float32(cv2.mean(oda)[:3]) / 255.0
    R = R * gecerli + dolgu * (1 - gecerli)

    # Renk sıcaklığı: + sıcak (kırmızı artar, mavi azalır), - soğuk
    if plan["sicaklik"]:
        R = np.clip(R * np.float32([1 - 0.18 * plan["sicaklik"], 1.0, 1 + 0.18 * plan["sicaklik"]]), 0, 1)
    # Kontrast: koyu kısımlar söner, açık kısımlar (pencere) kalır
    t = plan["kontrast_esik"]
    R = np.clip((R - t) / (1 - t), 0, 1) ** plan["gama"]
    # Cam ayna kadar net değil: hafif yumuşama; elle bulanıklık
    R = cv2.GaussianBlur(R, (0, 0), 0.6 + plan["bulaniklik"] * 0.015 * max(sw, sh))
    guc = np.full((sh, 1, 1), plan["guc"], np.float32)
    # Referanslardaki gibi pencere belirgin, çevresindeki oda (duvar, tavan) daha soluk yansır
    if plan.get("pencere_birim") is not None:
        maske = np.zeros((sh, sw), np.float32)
        cv2.fillConvexPoly(maske, np.int32(np.round(np.float32(plan["pencere_birim"]) * [sw, sh])), 1.0)
        maske = cv2.GaussianBlur(maske, (0, 0), 0.02 * max(sw, sh))
        guc = guc * (0.35 + 0.65 * maske)[..., None]
    if plan["sonme"] > 0:
        v = np.linspace(0, 1, sh, dtype=np.float32)[:, None, None]
        guc *= 1 - plan["sonme"] * v ** 1.2
    return np.clip(R * guc, 0, 1).astype(np.float32)

def _yatay_cizgi_egimleri(doku):
    """Pencere görüntüsündeki yataya yakın çizgilerin (çıta, pervaz) eğimleri ve yükseklikleri."""
    h, w = doku.shape[:2]
    if w < 12 or h < 12:
        return [], []
    g = cv2.cvtColor(doku, cv2.COLOR_BGR2GRAY)
    kenar = cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), 40, 120)
    parcalar = cv2.HoughLinesP(kenar, 1, np.pi / 360, threshold=max(8, int(0.25 * w)),
                               minLineLength=max(8, int(0.35 * w)), maxLineGap=max(3, int(0.05 * w)))
    egimler, ys = [], []
    for x1, y1, x2, y2 in (parcalar.reshape(-1, 4) if parcalar is not None else []):
        if x2 == x1:
            continue
        m = (y2 - y1) / (x2 - x1)
        if abs(m) < 0.7:   # yataya yakın çizgiler (çıta, pervaz)
            egimler.append(m)
            ys.append((y1 + y2) / 2)
    return egimler, ys

def pencere_sira_sayisi(doku):
    """Odadaki pencerenin kaç sıra camı olduğu (yatay çıtalardan); güvenilir ölçülemezse None.
    Pencerenin içinden üç dar dikey şerit alınır (dar şeritte eğik çıta bile kısa bir yatay çizgidir); her
    şeritte yatay kenar tepeleri bulunur, en az iki şeritte aynı hizada olanlar çıta sayılır. Gerçek çıtalar eşit
    aralıklıdır: aralıklar düzenli değilse (yaprak, bina, bulut kenarları) ya da camlar çok ince çıkıyorsa
    (jaluzi) sayı verilmez. Test odalarında 0037: 5 sıra, 0039: 3 sıra."""
    g = cv2.cvtColor(doku, cv2.COLOR_BGR2GRAY).astype(np.float32)
    h, w = g.shape
    if h < 40 or w < 6:
        return None
    gy = np.abs(cv2.Sobel(cv2.GaussianBlur(g, (3, 3), 0), cv2.CV_32F, 0, 1, ksize=3))
    tepeler = []
    for c in (0.25, 0.45, 0.65):
        x0 = int(c * w)
        x1 = max(x0 + 2, int((c + 0.1) * w))
        p = cv2.GaussianBlur(gy[:, x0:x1].mean(axis=1).reshape(1, -1), (0, 0), 1.5).ravel()
        esik = 3.0 * float(np.median(p)) + 2
        tepeler.append([i for i in range(2, h - 2) if p[i] == p[i - 2:i + 3].max() and p[i] > esik])
    def ayikla(adaylar):
        citalar = []
        for y in sorted(adaylar):
            if (not citalar or y - citalar[-1] >= 0.05 * h) and 0.06 * h < y < 0.94 * h:
                citalar.append(y)
        return citalar

    def esit_aralikli(citalar, en_az, sapma):
        if len(citalar) < en_az:
            return None
        aralik = np.diff(citalar) / h
        ort = float(aralik.mean())
        if ort < 0.1 or float(aralik.std()) > sapma * ort:
            return None
        return int(np.clip(round(1.0 / ort), 2, IZGARA_ARALIGI[1]))

    tol = 0.05 * h
    ortak = [y for y in tepeler[1] if any(abs(y - z) < tol for t in (tepeler[0], tepeler[2]) for z in t)]
    sayi = esit_aralikli(ayikla(ortak), 2, 0.25)
    if sayi is None:
        # Kenarı kesik ya da perspektifle eğik pencerede yan şeritler tutmayabilir: ortadaki şeritte en az üç
        # çıta çok düzenli aralıklıysa yeter
        sayi = esit_aralikli(ayikla(tepeler[1]), 3, 0.15)
    if sayi is None:
        # Boydan pencerede ortada hiç düz yatay çizgi yoksa tek büyük cam (yaprak / bina kenarları düz çizgi vermez)
        _, ys = _yatay_cizgi_egimleri(doku)
        if h > 3 * w and not any(0.1 * h < y < 0.9 * h for y in ys):
            sayi = 1
    return sayi

def _cam_kenari_ve_golge(oda, tablo, alfa, cizim_pts, taraf, ayar=None):
    """Tabloyu odayla birleştirir. ayar["temperli"] açıksa temperli cam görünümü (değerler TEMPERLI_KONTROLLERI'nden):
      - Duvar gölgesi: duvardan önde duran cam pano arkasından, ışığın tersine ve aşağı yumuşak gölge düşürür
      - Cam kalınlığı: tablonun hemen dışında camın hafif yeşilimsi yan yüzü (ince bant)
      - Cam tonu: temperli camın hafif yeşil-mavi tonu
      - Yüzey parlaklığı: ışık tarafından karşıya azalan hafif cam parlaklığı
      - Kenar parlaklığı: cilalı cam kenarında, ışığa bakan tarafta daha güçlü ince parlak çizgi
    Kapalıysa düz birleştirme."""
    ayar = yansima_ayari_duzelt(ayar)
    temperli = ayar["temperli"]
    H, W = oda.shape[:2]
    kosegen = float(np.linalg.norm(cizim_pts[0] - cizim_pts[2]))
    kisa = float(min(np.linalg.norm(cizim_pts[1] - cizim_pts[0]), np.linalg.norm(cizim_pts[3] - cizim_pts[0])))
    pay = int(0.06 * kosegen) + 8 if temperli else 2
    x0, y0 = np.maximum(np.floor(cizim_pts.min(axis=0)).astype(int) - pay, 0)
    x1, y1 = np.minimum(np.ceil(cizim_pts.max(axis=0)).astype(int) + pay, [W, H])
    o = oda[y0:y1, x0:x1].astype(np.float32)
    t = tablo[y0:y1, x0:x1].astype(np.float32)
    a = np.ascontiguousarray(alfa[y0:y1, x0:x1], dtype=np.float32)
    h, w = a.shape
    d = np.float32([taraf * 0.6, -0.8])   # ışığın yönü (ışık tarafı ve yukarısı)

    if temperli:
        # Örnek: Tablolar/*-cercevesiz (0021, 0022, 0023). Dıştan içe: hemen dışında başlayan yumuşak duvar
        # gölgesi, en dışta ince açık parlak hat, mat adaçayı yeşili opak cam kenarı (her yanda aynı kalınlıkta,
        # köşeleri hafif yuvarlak), tabloyla birleştiği yerde 1 piksellik koyu çizgi
        a8 = (a * 255 + 0.5).astype(np.uint8)   # morfoloji 8 bit maskede çok daha hızlı
        cam_disi8 = a8
        kalinlik = ayar["cam_kalinlik"] * kisa / 1000.0
        if kalinlik >= 0.5:
            r = max(1, int(round(kalinlik)))
            cam_disi8 = cv2.dilate(a8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        cam_disi = cam_disi8.astype(np.float32) / 255

        # Duvar gölgesi: duvardan önde duran cam panonun arkasından duvara düşer; camın hemen dışından başlar,
        # ışığın tersine ve aşağıda daha geniş. İki katman: kenara yakın koyu, dar gölge + daha geniş yumuşak gölge
        golge_gucu = ayar["cam_golge"] / 100.0
        if golge_gucu > 0:
            k = 4
            kucuk = cv2.resize(cam_disi, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
            boyut = (kucuk.shape[1], kucuk.shape[0])
            golge = np.zeros_like(kucuk)
            for kay, bulanik, agirlik in ((0.007, 0.006, 0.55), (0.02, 0.022, 0.45)):
                kayma = np.float32([[1, 0, -taraf * kay * kosegen / k], [0, 1, 1.3 * kay * kosegen / k]])
                golge += agirlik * cv2.GaussianBlur(cv2.warpAffine(kucuk, kayma, boyut),
                                                    (0, 0), max(1.0, bulanik * kosegen / k))
            golge = cv2.resize(golge, (w, h), interpolation=cv2.INTER_LINEAR)
            o *= (1 - 0.9 * golge_gucu * golge)[..., None]

        if kalinlik >= 0.5:
            bant = np.clip(cam_disi - a, 0, 1)
            # Cam kenarının rengi duvarın aydınlığına göre (örneklerde duvardan biraz koyu, yeşilimsi)
            sec = bant > 0.5
            aydinlik = float(o[sec].mean()) if sec.any() else 150.0
            renk = np.float32([137, 150, 143]) * float(np.clip(aydinlik / 160.0, 0.55, 1.35))
            o += (0.92 * bant)[..., None] * (renk - o)
            # Tabloyla birleşim: ince koyu çizgi
            ic = np.clip(cv2.dilate(a8, np.ones((3, 3), np.uint8)).astype(np.float32) / 255 - a, 0, 1)[..., None]
            o += 0.55 * ic * (np.float32([45, 55, 48]) - o)

        # Cam tonu: hafif yeşil-mavi (kırmızı ve mavi biraz azalır)
        ton = ayar["cam_ton"] / 100.0
        if ton > 0:
            t *= np.float32([1 - 0.06 * ton, 1.0, 1 - 0.1 * ton])

    s = o + (t - o) * a[..., None]

    if temperli:
        # Yüzey parlaklığı + kenar parlaklığı: tek "screen" katmanında
        parlatma = np.zeros((h, w), np.float32)
        parlak = ayar["cam_parlaklik"] / 100.0
        if parlak > 0:
            merkez = cizim_pts.mean(axis=0)
            dn = d / np.linalg.norm(d)
            xs = (np.arange(x0, x1, dtype=np.float32) - merkez[0]) * dn[0]
            ys = (np.arange(y0, y1, dtype=np.float32) - merkez[1]) * dn[1]
            yon = np.clip((ys[:, None] + xs[None, :]) / (0.5 * kosegen), -1, 1)   # ışık tarafı +1
            # Işık tarafından karşıya azalan parlaklık + ışık köşesine yakın geniş, yumuşak çapraz parlama
            parlama = np.exp(-((yon - 0.45) / 0.22) ** 2)
            parlatma = parlak * (0.03 + 0.04 * (1 + yon) + 0.1 * parlama) * a
        kenar_gucu = ayar["cam_kenar"] / 100.0
        if kenar_gucu > 0 and kalinlik >= 0.5:
            # Cilalı kenar: cam kenarının en dışında ince açık hat; ışığa bakan kenarlarda daha parlak
            kalin = max(1, int(round(0.35 * kalinlik)))
            kenar = cv2.subtract(cam_disi8, cv2.erode(cam_disi8, np.ones((3, 3), np.uint8), iterations=kalin))
            gx = cv2.Sobel(cam_disi8, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(cam_disi8, cv2.CV_32F, 0, 1, ksize=3)
            bakis = np.clip(-(gx * d[0] + gy * d[1]) / (cv2.magnitude(gx, gy) + 1e-6), 0, 1)
            cizgi = (kenar.astype(np.float32) / 255) * (0.12 + 0.18 * bakis) * kenar_gucu
            parlatma = 1 - (1 - parlatma) * (1 - cizgi)
        s = 255 - (255 - s) * (1 - parlatma[..., None])

    sonuc = oda.copy()
    sonuc[y0:y1, x0:x1] = np.clip(s + 0.5, 0, 255).astype(np.uint8)
    return sonuc

# ----------------------------------------------------------------------
# ORTAK ÇİZİM: oda_sunum ve tablo_ekle aynı sonucu versin diye tablo yerleştirme tek yerde
# ----------------------------------------------------------------------
def tablo_olcegi(eser, cizim_pts, pay=1.25):
    """Tablo resmi, çizimde kapladığı boyuttan (pay kadar fazlası) büyük olmasın; fazlası warp'ta zaten
    kaybolur. Ölçek 0.05'lik adımlara yuvarlanır (sürüklerken her harekette yeniden hesaplanmasın)."""
    tablo_boyu = max(np.linalg.norm(cizim_pts[i] - cizim_pts[(i + 1) % 4]) for i in range(4))
    olcek = pay * tablo_boyu / max(eser.shape[:2])
    return min(1.0, np.ceil(olcek * 20) / 20)

def tablo_resmi_hazirla(eser, cizim_pts, pay=1.25):
    """Tablo resmini çizim boyutuna küçültür (oda_sunum tam çizimiyle aynı)."""
    olcek = tablo_olcegi(eser, cizim_pts, pay)
    if olcek < 1.0:
        eser = cv2.resize(eser, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA)
    return eser

def tabloyu_sahneye_yerlestir(oda, art, cizim_pts, dst_pts, tip, cercevesiz, isik_analizi, ayar=None, hizli=False):
    """Hazır (çizim boyutuna küçültülmüş) tablo resmini cam tablo olarak odaya yerleştirir: otomatik cam yansıması,
    tablo tipi maskesi (yuvarlakta elips) ve temperli cam görünümü (kenar / duvar gölgesi; ayar["temperli"]).
    oda / cizim_pts: çizilen (gerekirse küçültülmüş) oda ve içindeki tablo köşeleri.
    dst_pts: tam boy oda koordinatları (ışık analizi bu koordinatlarda).
    cercevesiz: tablonun boş duvara eklenip eklenmediği; artık çizimi değiştirmez (temperli görünüm her tabloda
    ayardan açılır), eski çağrılar bozulmasın diye duruyor.
    hizli: sürüklerken kenar / gölge atlanır (akıcı kalsın)."""
    ayar = yansima_ayari_duzelt(ayar)
    cizim_pts = np.float32(cizim_pts)
    h_art, w_art = art.shape[:2]
    matrix = cv2.getPerspectiveTransform(np.float32([[0, 0], [w_art, 0], [w_art, h_art], [0, h_art]]), cizim_pts)
    oda_h, oda_w = oda.shape[:2]

    # Yansıma ve maske tablo resminden bağımsız, tablo yerinin boyutunda üretilir: aynı odaya hangi resim konursa
    # konsun yansıma birebir aynı yerde olur
    tablo_boyu = max(np.linalg.norm(cizim_pts[i] - cizim_pts[(i + 1) % 4]) for i in range(4))
    cozunurluk = int(np.ceil(min(1600.0, 1.25 * tablo_boyu)))
    oran = alan_orani(kose_noktalarini_sirala(cizim_pts))
    sw, sh = (cozunurluk, cozunurluk / oran) if oran >= 1 else (cozunurluk * oran, cozunurluk)
    sw, sh = max(2, int(round(sw))), max(2, int(round(sh)))

    taraf = 1.0
    if isik_analizi is not None and ayar["acik"] and ayar["guc"] > 0:
        plan = cam_yansimasi_plani(isik_analizi, dst_pts, ayar, tip)
        taraf = plan["taraf"]
        R = 255.0 * cv2.resize(ayna_katmani(oda, cizim_pts, dst_pts, sw, sh, plan), (w_art, h_art),
                               interpolation=cv2.INTER_LINEAR)
        art = (255 - (255 - art.astype(np.float32)) * (1 - R / 255.0)).astype(np.uint8)
    elif isik_analizi is not None:
        yon = isik_yonu(isik_analizi, dst_pts) if ayar["taraf"] == "oto" else ayar["taraf"]
        taraf = 0.0 if yon == "ust" else 1.0 if yon == "sag" else -1.0
    warped_art = cv2.warpPerspective(art, matrix, (oda_w, oda_h), borderMode=cv2.BORDER_REPLICATE)

    # Tablo tipine göre görünür alan (yuvarlakta elips), kenarları yumuşak
    maske_matrisi = cv2.getPerspectiveTransform(np.float32([[0, 0], [sw, 0], [sw, sh], [0, sh]]), cizim_pts)
    alfa = cv2.warpPerspective(tablo_maskesi(sw, sh, tip), maske_matrisi, (oda_w, oda_h))
    return _cam_kenari_ve_golge(oda, warped_art, alfa, cizim_pts, taraf,
                                {**ayar, "temperli": ayar["temperli"] and not hizli})

# ----------------------------------------------------------------------
# TABLO EKLEME YARDIMCILARI
# ----------------------------------------------------------------------
YANSIMA_AYAR_SUTUNLARI = (["cam_acik", "cam_gucu", "cam_taraf", "cam_cesit", "cam_temperli"]
                          + ["cam_" + k for k, *_ in YANSIMA_KONTROLLERI]
                          + [k for k, *_ in TEMPERLI_KONTROLLERI])


def yansima_ayarini_coz(kayit):
    """Veritabanındaki cam_* sütunlarını (boş olanlar varsayılan) yansıma ayarına çevirir."""
    return yansima_ayari_duzelt({"acik": kayit.get("cam_acik"), "guc": kayit.get("cam_gucu"),
                                 "taraf": kayit.get("cam_taraf"), "cesit": kayit.get("cam_cesit"),
                                 "temperli": kayit.get("cam_temperli"),
                                 **{k: kayit.get("cam_" + k) for k, *_ in YANSIMA_KONTROLLERI},
                                 **{k: kayit.get(k) for k, *_ in TEMPERLI_KONTROLLERI}})


def yansima_kaydi(ayar):
    """Yansıma ayarının veritabanı sütunları."""
    a = yansima_ayari_duzelt(ayar)
    return {"cam_acik": int(a["acik"]), "cam_gucu": a["guc"], "cam_taraf": a["taraf"], "cam_cesit": a["cesit"],
            "cam_temperli": int(a["temperli"]), **{"cam_" + k: a[k] for k, *_ in YANSIMA_KONTROLLERI},
            **{k: a[k] for k, *_ in TEMPERLI_KONTROLLERI}}


def kayitli_odalari_getir(oda_klasoru=None):
    conn = veritabani_baglan(oda_klasoru)  # eski veritabanına yansıma / tablo tipi sütunlarını da ekler
    try:
        satirlar = conn.execute(
            f"SELECT id, ad, dosya, genislik, yukseklik, bos_duvar, tablo_tipi, "
            f"{', '.join(KOORDINAT_SUTUNLARI + YANSIMA_AYAR_SUTUNLARI)} "
            "FROM odalar ORDER BY id").fetchall()
    finally:
        conn.close()

    odalar = []
    for oda_id, ad, dosya, genislik, yukseklik, bos_duvar, tablo_tipi, *degerler in satirlar:
        k = degerler[:8]
        if None in k:
            continue
        kose = np.float32(k).reshape(4, 2)
        odalar.append({
            "id": oda_id, "ad": ad, "dosya": dosya,
            "genislik": genislik, "yukseklik": yukseklik,
            "bos_duvar": bool(bos_duvar),
            "kose": kose,
            # Eski kayıtlarda tip yoksa koordinattan tahmin edilir
            "tip": tablo_tipi if tablo_tipi in TABLO_TIPLERI else tablo_tipi_tahmin(kose),
            "yansima": yansima_ayarini_coz(dict(zip(YANSIMA_AYAR_SUTUNLARI, degerler[8:]))),
        })
    odalar.sort(key=lambda o: oda_adi_sirasi(o["ad"]))   # alfabetik (aynı adlılar kayıt sırasıyla)
    return odalar


def orana_kirp(eser, oran):
    """Eseri bozmadan, ortadan kırparak hedef en-boy oranına getirir."""
    h, w = eser.shape[:2]
    if w / h > oran:
        yeni_w = max(1, int(round(h * oran)))
        x = (w - yeni_w) // 2
        return eser[:, x:x + yeni_w]
    yeni_h = max(1, int(round(w / oran)))
    y = (h - yeni_h) // 2
    return eser[y:y + yeni_h, :]


def resim_tipi_tahmin(resim):
    """Yüklenen resmin en-boyundan varsayılan tablo tipi (yuvarlak elle seçilir)."""
    h, w = resim.shape[:2]
    return oran_tipi(w / h)


def tabloyu_odaya_ekle(oda_resmi, kose, eser, cercevesiz=False, isik_analizi=None, yansima_ayari=None,
                       tip="yatay", olcek=1.0):
    """Eseri odadaki 4 köşeli alana (koordinata göre kırpıp) perspektifle cam tablo olarak yerleştirir.
    tip == "yuvarlak" ise alanın içine oturan elips şeklinde gösterilir.
    isik_analizi verilirse odanın ışığına / penceresine göre otomatik cam yansıması eklenir.
    olcek < 1: ekran önizlemesi; oda o ölçekte küçültülüp çizilir (çok daha hızlı, ekranda fark edilmez)."""
    dst_pts = kose_noktalarini_sirala(np.float32(kose))   # tam boy oda koordinatları (ışık analizi bunlara göre)
    eser = orana_kirp(eser, alan_orani(dst_pts))
    if olcek < 1.0:
        oda_resmi = cv2.resize(oda_resmi, None, fx=olcek, fy=olcek, interpolation=cv2.INTER_AREA)
    cizim_pts = (dst_pts * olcek).astype(np.float32)      # çizilen resmin koordinatları
    art = tablo_resmi_hazirla(eser, cizim_pts, 1.0 if olcek < 1.0 else 1.25)
    return tabloyu_sahneye_yerlestir(oda_resmi, art, cizim_pts, dst_pts, tip, cercevesiz, isik_analizi,
                                     yansima_ayari)


def resim_yaz(yol, resim):
    uzanti = os.path.splitext(yol)[1].lower() or ".jpg"
    params = [cv2.IMWRITE_JPEG_QUALITY, 95] if uzanti in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(uzanti, resim, params)
    if not ok:
        raise IOError(f"Resim kodlanamadı: {yol}")
    buf.tofile(yol)


def tip_etiketi(tip):
    return f"{TABLO_TIPI_SIMGE[tip]} {TABLO_TIPLERI[tip]}"


# Resim tipleri: yuvarlak resim yoktur; yuvarlak odalarda kare resimler (elips içinde) kullanılır
RESIM_TIPLERI = ["yatay", "dikey", "kare"]


def oda_resim_tipi(oda):
    """Odaya hangi tipteki resimlerin uyduğu: yuvarlak oda -> kare resim, diğerleri kendi tipi."""
    return "kare" if oda["tip"] == "yuvarlak" else oda["tip"]


def yol_anahtari(yol):
    return os.path.normcase(os.path.abspath(yol))
