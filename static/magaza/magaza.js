/* Tablo Stüdyo mağazası: ürünler AJAX ile gelir (arama, kategori, sıralama sayfa yenilenmeden değişir).
   Sepet ve favoriler üyede hesaba bağlıdır (sunucuda), giriş yapmamış ziyaretçide bu tarayıcıda. */
"use strict";

const $ = (s, k = document) => k.querySelector(s);
const $$ = (s, k = document) => [...k.querySelectorAll(s)];

function el(etiket, ozellik = {}, ...cocuklar) {
  const e = document.createElement(etiket);
  for (const [k, v] of Object.entries(ozellik)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value" || k === "checked" || k === "disabled") e[k] = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of cocuklar.flat()) {
    if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return e;
}

function svg(yol, boyut = 20, dolu = false) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("width", boyut);
  s.setAttribute("height", boyut);
  s.setAttribute("fill", dolu ? "currentColor" : "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.8");
  s.innerHTML = yol;
  return s;
}
const KALP = '<path d="M12 20.5s-7.5-4.6-9.3-9.3C1.6 8.1 3.6 4.5 7.2 4.5c2 0 3.6 1.1 4.8 2.8 1.2-1.7 2.8-2.8 4.8-2.8 3.6 0 5.6 3.6 4.5 6.7-1.8 4.7-9.3 9.3-9.3 9.3z"/>';

const paraBicimi = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY" });
const para = n => paraBicimi.format(n);
const TIP_ADLARI = { yatay: "Yatay", dikey: "Dikey", kare: "Kare", yuvarlak: "Yuvarlak" };
const indirimOrani = u => (u.eski_fiyat > u.fiyat ? Math.round((1 - u.fiyat / u.eski_fiyat) * 100) : 0);

const depo = {
  al(k, v) { try { return JSON.parse(localStorage.getItem(k)) ?? v; } catch { return v; } },
  yaz(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* gizli pencere: yok say */ } },
};

async function getir(yol) {
  const y = await fetch(yol, { headers: { Accept: "application/json" } });
  const j = await y.json().catch(() => ({}));
  if (!y.ok) throw Object.assign(new Error(j.hata || "Bağlantı hatası"), { durum: y.status });
  return j;
}

function bildir(metin, tur = "dark") {
  const t = el("div", { class: `toast align-items-center text-bg-${tur} border-0`, role: "status", "aria-live": "polite" },
    el("div", { class: "d-flex" }, el("div", { class: "toast-body" }, metin),
      el("button", { type: "button", class: "btn-close btn-close-white me-2 m-auto", "data-bs-dismiss": "toast", "aria-label": "Kapat" })));
  $("#toast-kok").append(t);
  t.addEventListener("hidden.bs.toast", () => t.remove());
  new bootstrap.Toast(t, { delay: 3500 }).show();
}

// ======================================================================
// FAVORİLER VE SEPET: üyede hesaba bağlı (sunucuda; her üye sadece kendininkini görür, başka cihazda da aynısı),
// giriş yapmamış ziyaretçide bu tarayıcıda. Ziyaretçi giriş yapınca tarayıcıdaki sepeti hesabına eklenir.
// ======================================================================
const ozetle = u => ({ id: u.id, ad: u.ad, fiyat: u.fiyat, kapak: u.kapak, adres: u.adres });
const MISAFIR_SEPET = "magaza_sepet_misafir", MISAFIR_FAVORI = "magaza_favoriler_misafir";
// Eski sürüm sepeti / favorileri tarayıcıda herkes için ortak anahtarda tutuyordu (bir kullanıcının sepeti
// başkasında görünüyordu): o ortak kayıtlar silinir
try { localStorage.removeItem("magaza_sepet"); localStorage.removeItem("magaza_favoriler"); } catch { /* yok say */ }

const HesapDeposu = {
  yuklendi: false,     // üyenin sepeti / favorileri sunucudan geldi mi (gelmeden yazılırsa sunucudaki ezilmesin)
  sayac: 0,
  uye() { return !!Oturum.kisi.giris; },
  /** Listenin tamamını sunucuya yazar; sürüm sayesinde sırasız ulaşan eski istek yenisini ezmez. keepalive:
      sipariş verip sayfadan hemen çıkılsa da istek tamamlanır. */
  async yaz(yol, govde) {
    try {
      const y = await fetch(yol, { method: "PUT", headers: { "Content-Type": "application/json" }, keepalive: true,
        body: JSON.stringify({ ...govde, surum: Date.now() + (++this.sayac % 1000) / 1000 }) });
      if (y.ok) depo.yaz("magaza_hesap_degisti", Date.now());   // bu hesabın açık diğer sekmeleri yenilensin
    } catch { /* bağlantı yoksa bir sonraki değişiklikte yazılır */ }
  },
};

const Favori = {
  liste: depo.al(MISAFIR_FAVORI, []),
  var(id) { return this.liste.some(f => f.id === id); },
  async degistir(u) {
    if (HesapDeposu.uye() && !HesapDeposu.yuklendi) await Sepet.hazir;
    if (this.var(u.id)) {
      this.liste = this.liste.filter(f => f.id !== u.id);
      bildir("Favorilerden çıkarıldı.");
    } else {
      this.liste.unshift(ozetle(u));
      bildir("♥ Favorilere eklendi.");
    }
    this.kaydet();
  },
  kaydet() {
    if (HesapDeposu.uye()) HesapDeposu.yaz("/api/hesap/favoriler", { favoriler: this.liste.map(f => f.id) });
    else depo.yaz(MISAFIR_FAVORI, this.liste);
    this.ciz();
    favoriDugmeleriniYaz();
  },
  ciz() {
    $("#favori-sayi").textContent = this.liste.length || "";
    $("#favori-liste").replaceChildren(...(this.liste.length ? this.liste.map(f =>
      el("div", { class: "sepet-satir" },
        el("a", { href: f.adres }, el("img", { src: f.kapak, alt: "", loading: "lazy" })),
        el("div", {}, el("a", { class: "sepet-ad", href: f.adres }, f.ad), el("b", {}, para(f.fiyat)),
          el("button", { class: "sepet-kaldir", type: "button", onclick: () => this.degistir(f) }, "Kaldır")),
        el("button", { class: "btn btn-sm btn-dark rounded-pill", type: "button", onclick: () => Sepet.ekle(f) }, "Sepete")))
      : [el("div", { class: "bos-sonuc py-4" }, el("div", { class: "simge" }, "♡"),
        el("p", {}, "Beğendiğiniz tabloların kalbine dokunun; burada toplansın."))]));
  },
};

function favoriDugmeleriniYaz() {
  for (const b of $$(".favori[data-id], .favori-dugme[data-id]")) favoriDugmesiYaz(b, Favori.var(+b.dataset.id));
}

function favoriDugmesiYaz(b, aktif) {
  b.classList.toggle("aktif", aktif);
  b.setAttribute("aria-pressed", String(aktif));
  b.setAttribute("aria-label", aktif ? "Favorilerden çıkar" : "Favorilere ekle");
  if (b.classList.contains("favori-dugme")) {
    b.replaceChildren(svg(KALP, 18, aktif), aktif ? " Favorilerde" : " Favorilere Ekle");
  }
}

const Sepet = {
  liste: depo.al(MISAFIR_SEPET, []),
  hazir: null,   // üyenin sepeti sunucudan gelince çözülür (ödeme sayfası bekler)
  async ekle(u, adet = 1) {
    if (HesapDeposu.uye() && !HesapDeposu.yuklendi) await this.hazir;
    const var_ = this.liste.find(s => s.id === u.id);
    if (var_) var_.adet = Math.min(10, var_.adet + adet);
    else this.liste.push({ ...ozetle(u), adet });
    this.kaydet();
    bildir(`✓ “${u.ad}” sepete eklendi.`, "success");
  },
  adet(id, fark) {
    const s = this.liste.find(x => x.id === id);
    if (!s) return;
    s.adet = Math.max(1, Math.min(10, s.adet + fark));
    this.kaydet();
  },
  kaldir(id) {
    this.liste = this.liste.filter(s => s.id !== id);
    this.kaydet();
  },
  kaydet() {
    if (HesapDeposu.uye()) HesapDeposu.yaz("/api/hesap/sepet", { sepet: this.liste.map(s => ({ id: s.id, adet: s.adet })) });
    else depo.yaz(MISAFIR_SEPET, this.liste);
    this.ciz();
  },
  ciz() {
    const adet = this.liste.reduce((t, s) => t + s.adet, 0);
    $("#sepet-sayi").textContent = adet || "";
    $("#sepet-liste").replaceChildren(...(this.liste.length ? this.liste.map(s =>
      el("div", { class: "sepet-satir" },
        el("a", { href: s.adres }, el("img", { src: s.kapak, alt: "", loading: "lazy" })),
        el("div", {}, el("a", { class: "sepet-ad", href: s.adres }, s.ad),
          el("div", { class: "adet-secici" },
            el("button", { type: "button", "aria-label": "Azalt", onclick: () => this.adet(s.id, -1) }, "−"),
            el("span", {}, s.adet),
            el("button", { type: "button", "aria-label": "Artır", onclick: () => this.adet(s.id, 1) }, "+")),
          el("button", { class: "sepet-kaldir", type: "button", onclick: () => this.kaldir(s.id) }, "Kaldır")),
        el("b", {}, para(s.fiyat * s.adet))))
      : [el("div", { class: "bos-sonuc py-4" }, el("div", { class: "simge" }, "🛍️"), el("h3", {}, "Sepetiniz boş"),
        el("p", {}, "Beğendiğiniz tabloları sepete ekleyin."))]));
    const toplam = this.liste.reduce((t, s) => t + s.fiyat * s.adet, 0);
    $("#sepet-alt").replaceChildren(...(this.liste.length ? [
      el("div", { class: "sepet-toplam" }, el("span", {}, `Ara toplam (${adet} ürün)`), el("span", {}, para(toplam))),
      // Giriş yapılmadıysa sunucu önce giriş sayfasına, girişten sonra ödemeye yönlendirir
      el("a", { class: "dugme dugme-koyu dugme-tam", href: "/odeme" }, "Siparişi Tamamla"),
      el("p", { class: "sepet-not" }, HesapDeposu.uye() ? "Sepetiniz hesabınıza kayıtlı; başka cihazdan girince de görünür."
        : "Giriş yaptığınızda sepetiniz hesabınıza eklenir."),
    ] : []));
  },
};

// ======================================================================
// OTURUM: üstteki "Giriş Yap" / hesap menüsü
// ======================================================================
const Oturum = {
  kisi: { giris: false },
  hazir: null,   // oturum bilgisi gelince çözülen söz (ödeme / hesap sayfaları bekler)

  async yukle() {
    try { this.kisi = await getir("/api/magaza/ben"); } catch { this.kisi = { giris: false }; }
    this.ciz();
    return this.kisi;
  },

  ciz() {
    const alan = $("#hesap-alani");
    if (!alan) return;
    const burasi = location.pathname + location.search;
    if (!this.kisi.giris) {
      alan.replaceChildren(el("a", { class: "ust-link giris-link", href: `/giris?sonra=${encodeURIComponent(burasi)}` }, "Giriş Yap"));
      return;
    }
    const k = this.kisi;
    const cikis = el("form", { method: "post", action: "/cikis", class: "m-0" },
      el("button", { type: "submit", class: "dropdown-item" }, "Çıkış Yap"));
    alan.replaceChildren(el("div", { class: "dropdown" },
      el("button", { class: "ust-link hesap-dugme dropdown-toggle", type: "button", "data-bs-toggle": "dropdown",
        "aria-expanded": "false" }, el("span", { class: "hesap-harf" }, (k.ad || "?").trim().charAt(0).toLocaleUpperCase("tr-TR")),
      el("span", { class: "hesap-ad" }, k.ad.split(" ")[0])),
      el("ul", { class: "dropdown-menu dropdown-menu-end hesap-menu" },
        el("li", {}, el("div", { class: "dropdown-header" }, el("b", {}, k.ad), el("div", { class: "small" }, `@${k.kullanici_adi}`), k.eposta ? el("div", { class: "small" }, k.eposta) : null)),
        el("li", {}, el("a", { class: "dropdown-item", href: "/hesabim#siparisler" }, "📦 Siparişlerim")),
        el("li", {}, el("a", { class: "dropdown-item", href: "/hesabim#bilgiler" }, "👤 Hesap Bilgilerim")),
        k.rol === "satici" ? el("li", {}, el("a", { class: "dropdown-item", href: "/panel" }, "🛠️ Satıcı Paneli")) : null,
        el("li", {}, el("hr", { class: "dropdown-divider" })),
        el("li", {}, cikis))));
  },
};

// ======================================================================
// ÜRÜN KARTI
// ======================================================================
function urunKarti(u) {
  const indirim = indirimOrani(u);
  const fav = el("button", { class: "favori", type: "button", "data-id": u.id,
    onclick: e => { e.preventDefault(); e.stopPropagation(); Favori.degistir(u); } }, svg(KALP, 20));
  favoriDugmesiYaz(fav, Favori.var(u.id));
  return el("a", { class: "kart", href: u.adres },
    el("div", { class: "kart-gorsel" },
      el("img", { class: "birinci", src: u.kapak, alt: u.ad, loading: "lazy", decoding: "async" }),
      u.ikinci !== u.kapak ? el("img", { class: "ikinci", src: u.ikinci, alt: "", loading: "lazy", decoding: "async" }) : null,
      fav,
      indirim ? el("span", { class: "rozet-indirim" }, `%${indirim} indirim`) : null),
    el("div", { class: "kart-ad" }, u.ad),
    u.puan_sayi ? el("div", { class: "kart-puan" }, yildizlar(u.puan, "yildiz-kucuk"), el("span", {}, `${puanYaz(u.puan)} (${u.puan_sayi})`)) : null,
    el("div", { class: "kart-alt" }, `Tablo Stüdyo · ${TIP_ADLARI[u.tip]} cam tablo`),
    el("div", { class: "kart-fiyat" },
      el("b", { class: indirim ? "indirimli" : "" }, para(u.fiyat)),
      indirim ? el("del", {}, para(u.eski_fiyat)) : null,
      indirim ? el("span", {}, `(%${indirim} indirim)`) : null),
    u.oda_sayisi > 1 ? el("span", { class: "kart-etiket" }, `${u.oda_sayisi} oda görünümü`) : null);
}

/** Yıldızlar (puan yuvarlanır): ★★★★☆ */
function yildizlar(puan, sinif = "") {
  const tam = Math.round(puan);
  return el("span", { class: `yildizlar ${sinif}`, role: "img", "aria-label": `5 üzerinden ${String(puan).replace(".", ",")} yıldız` },
    ...[1, 2, 3, 4, 5].map(i => el("span", { class: i <= tam ? "dolu" : "", "aria-hidden": "true" }, "★")));
}
const puanYaz = p => p.toLocaleString("tr-TR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function iskeletKartlar(n) {
  return Array.from({ length: n }, () => el("div", { class: "kart iskelet", "aria-hidden": "true" },
    el("div", { class: "kart-gorsel" }), el("div", { class: "satir-iskelet" }), el("div", { class: "satir-iskelet kisa" })));
}

// ======================================================================
// LİSTE SAYFASI: arama, kategori, sıralama (AJAX)
// ======================================================================
const Liste = {
  durum: { ara: "", tip: "", sirala: "yeni" },
  istek: 0,
  ilk: true,

  basla() {
    const p = new URLSearchParams(location.search);
    this.durum = { ara: p.get("ara") || "", tip: p.get("tip") || "", sirala: p.get("sirala") || "yeni" };
    $("#arama").value = this.durum.ara;
    $("#siralama").value = this.durum.sirala;

    $("#arama-formu").addEventListener("submit", e => { e.preventDefault(); this.degistir({ ara: $("#arama").value.trim() }, true); });
    let zam = null;
    $("#arama").addEventListener("input", () => {
      clearTimeout(zam);
      zam = setTimeout(() => this.degistir({ ara: $("#arama").value.trim() }), 300);
    });
    $("#siralama").onchange = () => this.degistir({ sirala: $("#siralama").value });
    for (const a of $$("#kategoriler a")) {
      a.addEventListener("click", e => { e.preventDefault(); this.degistir({ tip: a.dataset.tip, ara: "" }, true); $("#arama").value = ""; });
    }
    window.addEventListener("popstate", () => {
      const q = new URLSearchParams(location.search);
      this.durum = { ara: q.get("ara") || "", tip: q.get("tip") || "", sirala: q.get("sirala") || "yeni" };
      $("#arama").value = this.durum.ara;
      $("#siralama").value = this.durum.sirala;
      this.yukle();
    });
    this.yukle();
  },

  degistir(degisen, kaydir = false) {
    Object.assign(this.durum, degisen);
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(this.durum)) if (v && !(k === "sirala" && v === "yeni")) q.set(k, v);
    history.pushState(null, "", q.toString() ? `/?${q}` : "/");
    this.yukle(kaydir);
  },

  async yukle(kaydir = false) {
    const no = ++this.istek;
    const { ara, tip, sirala } = this.durum;
    const vitrinde = !ara && !tip;
    $("#vitrin").hidden = !vitrinde;
    $("#kategori-bolumu").hidden = !vitrinde;
    for (const a of $$("#kategoriler a")) a.classList.toggle("aktif", a.dataset.tip === tip && !ara);
    $("#sonuc-baslik").textContent = ara ? `“${ara}” için sonuçlar`
      : tip ? `${TIP_ADLARI[tip]} cam tablolar` : "Bütün cam tablolar";
    document.title = ara ? `${ara} — Tablo Stüdyo` : tip ? `${TIP_ADLARI[tip]} Cam Tablolar — Tablo Stüdyo` : "Tablo Stüdyo — Cam Tablolar";
    if (!this.ilk) $("#izgara").replaceChildren(...iskeletKartlar(8));
    else $("#izgara").replaceChildren(...iskeletKartlar(10));
    if (kaydir) $("#sonuclar").scrollIntoView({ behavior: "smooth", block: "start" });

    let j;
    try {
      j = await getir(`/api/magaza/urunler?${new URLSearchParams({ ara, tip, sirala })}`);
    } catch (h) {
      if (no === this.istek) { $("#izgara").replaceChildren(); bildir("Ürünler yüklenemedi: " + h.message, "danger"); }
      return;
    }
    if (no !== this.istek) return;   // bu arada başka arama yapıldı
    $("#sonuc-adet").textContent = `${j.toplam} ürün`;
    $("#izgara").replaceChildren(...j.urunler.map(urunKarti));
    $("#bos-sonuc").hidden = j.toplam > 0;
    this.cipler(j);
    if (vitrinde && this.ilk) this.vitrin(j.urunler);
    this.ilk = false;
  },

  cipler(j) {
    const { tip } = this.durum;
    const sayilar = j.tipler;
    $("#cipler").replaceChildren(
      el("button", { class: `cip ${!tip ? "aktif" : ""}`, type: "button", onclick: () => this.degistir({ tip: "" }) }, "Tümü"),
      ...Object.entries(TIP_ADLARI).filter(([t]) => !sayilar || sayilar[t] || t === tip).map(([t, ad]) =>
        el("button", { class: `cip ${tip === t ? "aktif" : ""}`, type: "button", onclick: () => this.degistir({ tip: t }) },
          sayilar ? `${ad} (${sayilar[t]})` : ad)));
  },

  vitrin(urunler) {
    // Üst banttaki kolaj: her açılışta her tablo tipinden (kare, yatay, dikey, yuvarlak) rastgele bir ürün, yerleri
    // de rastgele. Ürünü olmayan tipin yerine başka tiplerden rastgele ürün gelir (en fazla 4 görsel).
    const karistir = dizi => {
      const d = [...dizi];
      for (let i = d.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [d[i], d[j]] = [d[j], d[i]];
      }
      return d;
    };
    const karisik = karistir(urunler);
    const secilen = Object.keys(TIP_ADLARI).map(t => karisik.find(u => u.tip === t)).filter(Boolean);
    for (const u of karisik) {
      if (secilen.length >= 4) break;
      if (!secilen.includes(u)) secilen.push(u);
    }
    const kolaj = $("#kolaj");
    kolaj.dataset.adet = secilen.length;
    kolaj.replaceChildren(...karistir(secilen).map(u =>
      el("a", { href: u.adres, "aria-label": u.ad }, el("img", { src: u.kapak, alt: "", decoding: "async" }))));
    $("#vitrin").classList.toggle("d-none", !urunler.length);
    // Tablo şekline göre: her tipin ilk ürününün görseli
    const ilkler = {};
    const sayilar = {};
    for (const u of urunler) { ilkler[u.tip] ||= u; sayilar[u.tip] = (sayilar[u.tip] || 0) + 1; }
    const tipler = Object.keys(TIP_ADLARI).filter(t => ilkler[t]);
    $("#kategori-bolumu").hidden = !tipler.length;
    $("#daireler").replaceChildren(...tipler.map(t =>
      el("a", { class: "daire", href: `/?tip=${t}`, onclick: e => { e.preventDefault(); this.degistir({ tip: t }, true); } },
        el("div", { class: "daire-gorsel" }, el("img", { src: ilkler[t].kapak, alt: "", loading: "lazy" })),
        el("div", { class: "daire-ad" }, `${TIP_ADLARI[t]} Tablolar`),
        el("div", { class: "daire-sayi" }, `${sayilar[t]} ürün`))));
  },
};

// ======================================================================
// ÜRÜN SAYFASI
// ======================================================================
const UrunSayfasi = {
  u: null,
  sira: 0,

  async basla() {
    $("#arama-formu").addEventListener("submit", e => {
      e.preventDefault();
      const q = $("#arama").value.trim();
      location.href = q ? `/?ara=${encodeURIComponent(q)}` : "/";
    });
    const id = parseInt(location.pathname.split("/").pop(), 10);
    const hedef = location.hash;   // "Değerlendir" bağlantısıyla gelindiyse (#degerlendirmeler) oraya kayılır
    let u;
    try {
      u = await getir(`/api/magaza/urun/${id}`);
    } catch {
      $("#urun-duzen").hidden = true;
      $("#bulunamadi").hidden = false;
      document.title = "Ürün bulunamadı — Tablo Stüdyo";
      return;
    }
    // Galeride sadece odalardaki görünümler; tablonun tek başına resmi sadece hiç oda yoksa gösterilir
    const odalarda = u.gorseller.filter(g => g.oda_id != null);
    if (odalarda.length) u.gorseller = odalarda;
    this.u = u;
    document.title = `${u.ad} — Tablo Stüdyo`;
    if (!location.pathname.endsWith(u.adres.split("/").pop())) history.replaceState(null, "", u.adres);
    for (const a of $$("#kategoriler a")) a.classList.toggle("aktif", a.dataset.tip === u.tip);
    $("#kirinti").replaceChildren(el("a", { href: "/" }, "Ana sayfa"), el("span", {}, "›"),
      el("a", { href: `/?tip=${u.tip}` }, `${TIP_ADLARI[u.tip]} cam tablolar`), el("span", {}, "›"),
      el("span", { "aria-current": "page" }, u.ad));
    this.galeri();
    this.bilgi();
    Degerlendirme.basla(u, hedef === "#degerlendirmeler");
    if (u.benzerler.length) {
      $("#benzer-bolum").hidden = false;
      $("#benzerler").replaceChildren(...u.benzerler.map(urunKarti));
    }
  },

  galeri() {
    const g = this.u.gorseller;
    $("#kucukler").replaceChildren(...g.map((x, i) => el("button", { class: "kucuk", type: "button", role: "tab",
      "aria-label": `Görsel ${i + 1}`, onclick: () => this.goster(i) }, el("img", { src: x.k, alt: "", loading: "lazy" }))));
    $("#galeri-onceki").onclick = () => this.goster(this.sira - 1);
    $("#galeri-sonraki").onclick = () => this.goster(this.sira + 1);
    $("#ana-gorsel").addEventListener("click", e => { if (!e.target.closest("button")) this.buyut(); });
    $("#buyuk-onceki").onclick = () => { this.goster(this.sira - 1); $("#buyuk-resim").src = g[this.sira].b; };
    $("#buyuk-sonraki").onclick = () => { this.goster(this.sira + 1); $("#buyuk-resim").src = g[this.sira].b; };
    document.addEventListener("keydown", e => {
      if (e.target.matches("input, select, textarea")) return;
      const buyukAcik = $("#buyuk-galeri").classList.contains("show");
      if (e.key === "ArrowLeft") { this.goster(this.sira - 1); if (buyukAcik) $("#buyuk-resim").src = g[this.sira].b; }
      if (e.key === "ArrowRight") { this.goster(this.sira + 1); if (buyukAcik) $("#buyuk-resim").src = g[this.sira].b; }
    });
    // Parmakla kaydırma
    let x0 = null;
    $("#ana-gorsel").addEventListener("touchstart", e => { x0 = e.touches[0].clientX; }, { passive: true });
    $("#ana-gorsel").addEventListener("touchend", e => {
      if (x0 == null) return;
      const dx = e.changedTouches[0].clientX - x0;
      if (Math.abs(dx) > 40) this.goster(this.sira + (dx < 0 ? 1 : -1));
      x0 = null;
    });
    const tek = g.length < 2;
    $("#galeri-onceki").hidden = $("#galeri-sonraki").hidden = $("#buyuk-onceki").hidden = $("#buyuk-sonraki").hidden = tek;
    this.goster(0);
    // Sonraki görselleri önceden yükle (geçişler beklemesin)
    setTimeout(() => g.slice(1).forEach(x => { new Image().src = x.o; }), 600);
  },

  goster(i) {
    const g = this.u.gorseller;
    this.sira = (i + g.length) % g.length;
    const img = $("#ana-resim");
    img.classList.add("yukleniyor");
    img.onload = () => { img.classList.remove("yukleniyor"); $("#ana-gorsel").classList.remove("iskelet"); };
    img.src = g[this.sira].o;
    img.alt = `${this.u.ad} — görsel ${this.sira + 1}`;
    $$("#kucukler .kucuk").forEach((b, j) => { b.classList.toggle("aktif", j === this.sira); b.setAttribute("aria-selected", String(j === this.sira)); });
    $$("#kucukler .kucuk")[this.sira]?.scrollIntoView({ block: "nearest", inline: "nearest" });
    $("#galeri-sayac").textContent = `${this.sira + 1} / ${g.length}`;
  },

  buyut() {
    $("#buyuk-resim").src = this.u.gorseller[this.sira].b;
    $("#buyuk-resim").alt = $("#ana-resim").alt;
    bootstrap.Modal.getOrCreateInstance($("#buyuk-galeri")).show();
  },

  bilgi() {
    const u = this.u;
    const indirim = indirimOrani(u);
    const adet = el("select", { id: "adet" }, ...Array.from({ length: 10 }, (_, i) => el("option", { value: i + 1 }, i + 1)));
    const fav = el("button", { class: "dugme dugme-acik dugme-tam favori-dugme", type: "button", "data-id": u.id,
      onclick: () => Favori.degistir(u) });
    favoriDugmesiYaz(fav, Favori.var(u.id));
    const odaSayisi = u.gorseller.filter(g => g.oda_id != null).length;
    $("#bilgi").replaceChildren(
      el("div", { class: "bilgi-fiyat" },
        el("span", { class: `simdiki ${indirim ? "indirimli" : ""}` }, para(u.fiyat)),
        indirim ? el("del", {}, para(u.eski_fiyat)) : null,
        indirim ? el("span", { class: "indirim" }, `%${indirim} indirim`) : null),
      el("h1", { class: "urun-baslik mt-2" }, u.ad),
      u.puan_sayi ? el("a", { class: "baslik-puan", href: "#degerlendirmeler" }, yildizlar(u.puan),
        el("span", {}, `${puanYaz(u.puan)} · ${u.puan_sayi} değerlendirme`)) : null,
      el("div", { class: "satici-satiri" }, el("img", { src: "/static/panel/logo.svg", width: 26, height: 26, alt: "" }),
        el("b", {}, "Tablo Stüdyo")),
      el("div", { class: "secenek" }, el("label", { for: "adet" }, "Adet"), adet),
      el("div", { class: "dugme-yigini" },
        el("button", { class: "dugme dugme-koyu dugme-tam", type: "button", onclick: () => Sepet.ekle(u, +adet.value) }, "Sepete Ekle"),
        fav),
      el("ul", { class: "ozellik-listesi" },
        el("li", {}, el("span", { class: "simge" }, "🔷"), el("div", {}, el("b", {}, "Temperli cam tablo"),
          el("div", { class: "text-muted small" }, `${TIP_ADLARI[u.tip]} model`))),
        odaSayisi ? el("li", {}, el("span", { class: "simge" }, "🏠"), el("div", {}, el("b", {}, `${odaSayisi} farklı odada görün`),
          el("div", { class: "text-muted small" }, "Galerideki görseller tablonun gerçek oda fotoğraflarındaki halidir."))) : null),
      el("div", { class: "akordeon mt-4" },
        el("details", { open: true }, el("summary", {}, "Ürün açıklaması"),
          el("div", { class: "icerik" }, u.aciklama || "Bu ürün için henüz açıklama eklenmedi.")),
        el("details", {}, el("summary", {}, "Ürün özellikleri"),
          el("div", { class: "icerik" }, el("table", { class: "ozellik-tablosu" }, el("tbody", {},
            el("tr", {}, el("td", {}, "Tablo şekli"), el("td", {}, TIP_ADLARI[u.tip])),
            el("tr", {}, el("td", {}, "Malzeme"), el("td", {}, "Temperli cam")),
            el("tr", {}, el("td", {}, "Ürün kodu"), el("td", {}, `TS-${String(u.id).padStart(5, "0")}`))))))));
  },
};

// ======================================================================
// ÜRÜN DEĞERLENDİRMELERİ (ürün sayfası): yıldız ve yorum. Sadece ürünü satın alıp teslim almış müşteri yazar.
// ======================================================================
const Degerlendirme = {
  u: null,
  veri: null,
  duzenle: false,   // kendi değerlendirmesi varken formu açtı mı

  async basla(u, kaydir) {
    this.u = u;
    await this.yukle();
    $("#degerlendirmeler").hidden = false;
    if (kaydir) $("#degerlendirmeler").scrollIntoView({ behavior: "smooth", block: "start" });
  },

  async yukle() {
    try { this.veri = await getir(`/api/magaza/urun/${this.u.id}/degerlendirmeler`); } catch { return; }
    this.ciz();
  },

  ciz() {
    const v = this.veri;
    // Özet: ortalama ve 5→1 dağılımı
    $("#dg-ozet").replaceChildren(el("div", { class: "dg-ozet" },
      el("div", { class: "dg-ortalama" }, v.sayi ? puanYaz(v.ortalama) : "—"),
      yildizlar(v.ortalama, "yildiz-buyuk"),
      el("div", { class: "dg-sayi" }, v.sayi ? `${v.sayi} değerlendirme` : "Henüz değerlendirme yok"),
      el("div", { class: "dg-dagilim" }, ...Object.keys(v.dagilim).sort((a, b) => b - a).map(p => el("div", { class: "dg-satir" },
        el("span", {}, `${p} ★`),
        el("span", { class: "dg-cubuk" }, el("span", { style: `width:${v.sayi ? (v.dagilim[p] / v.sayi) * 100 : 0}%` })),
        el("span", { class: "dg-adet" }, v.dagilim[p]))))));
    $("#dg-form").replaceChildren(this.form());
    $("#dg-liste").replaceChildren(...(v.liste.length ? v.liste.map(d => el("article", { class: `dg-yorum ${d.benim ? "benim" : ""}` },
      el("div", { class: "dg-yorum-ust" }, yildizlar(d.puan, "yildiz-kucuk"), el("b", {}, d.ad),
        d.benim ? el("span", { class: "dg-etiket" }, "Sizin değerlendirmeniz") : null,
        el("span", { class: "dg-tarih" }, new Date(d.tarih.replace(" ", "T")).toLocaleDateString("tr-TR",
          { day: "numeric", month: "long", year: "numeric" }) + (d.duzenlendi ? " · düzenlendi" : ""))),
      d.yorum ? el("p", { class: "dg-metin" }, d.yorum) : null,
      d.satici_cevap ? el("div", { class: "dg-cevap" }, el("b", {}, "Tablo Stüdyo'nun cevabı"), el("p", {}, d.satici_cevap)) : null))
      : [el("p", { class: "dg-bos" }, "Bu ürün henüz değerlendirilmedi. Satın alıp teslim alanlar ilk yorumu yazabilir.")]));
  },

  form() {
    const v = this.veri, benim = v.benim;
    if (!v.giris) {
      return el("div", { class: "dg-bilgi" }, "Satın aldığınız ürünleri değerlendirmek için ",
        el("a", { href: `/giris?sonra=${encodeURIComponent(location.pathname)}` }, "giriş yapın"), ".");
    }
    if (!v.yazabilir && !benim) {
      return el("div", { class: "dg-bilgi" }, "Bu ürünü satın alıp teslim aldıktan sonra değerlendirebilirsiniz.");
    }
    if (benim && !this.duzenle) {
      return el("div", { class: "dg-bilgi" },
        benim.gorunur ? "Bu ürünü değerlendirdiniz, teşekkürler! " : "Değerlendirmeniz mağaza tarafından gizlendi. ",
        v.yazabilir ? el("button", { class: "dg-baglanti", type: "button", onclick: () => { this.duzenle = true; this.ciz(); } }, "Düzenle") : null,
        " · ", el("button", { class: "dg-baglanti", type: "button", onclick: () => this.sil() }, "Sil"));
    }
    let puan = benim?.puan || 0;
    const ADLAR = ["", "Çok kötü", "Kötü", "Orta", "İyi", "Çok iyi"];
    const ad = el("span", { class: "dg-puan-adi" }, ADLAR[puan] || "Puanınızı seçin");
    const secici = el("div", { class: "dg-secici", role: "radiogroup", "aria-label": "Puanınız" });
    const yaz = () => {
      $$("button", secici).forEach((b, i) => {
        b.classList.toggle("dolu", i < puan);
        b.setAttribute("aria-checked", String(i + 1 === puan));
        b.tabIndex = (puan ? i + 1 === puan : i === 0) ? 0 : -1;
      });
      ad.textContent = ADLAR[puan] || "Puanınızı seçin";
    };
    for (let i = 1; i <= 5; i++) {
      secici.append(el("button", { type: "button", role: "radio", "aria-label": `${i} yıldız — ${ADLAR[i]}`,
        onclick: () => { puan = i; yaz(); },
        onkeydown: e => {
          const fark = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
          if (!fark) return;
          e.preventDefault();
          puan = Math.min(5, Math.max(1, (puan || i) + fark));
          yaz();
          $$("button", secici)[puan - 1].focus();
        } }, "★"));
    }
    yaz();
    const yorum = el("textarea", { class: "dg-girdi", maxlength: 1000, rows: 4, placeholder: "Ürün hakkında düşünceleriniz (isteğe bağlı): cam kalitesi, renkler, paketleme…" });
    yorum.value = benim?.yorum || "";
    const gonder = el("button", { class: "dugme dugme-koyu", type: "submit" }, benim ? "Değişiklikleri Kaydet" : "Değerlendirmeyi Gönder");
    return el("form", { class: "dg-formu", onsubmit: async e => {
      e.preventDefault();
      if (!puan) return bildir("Lütfen yıldız seçin.", "danger");
      gonder.disabled = true;
      try {
        this.veri = await this.istek("PUT", { puan, yorum: yorum.value });
        this.duzenle = false;
        this.ciz();
        bildir("Değerlendirmeniz için teşekkürler!", "success");
      } catch (h) { gonder.disabled = false; bildir(h.message, "danger"); }
    } },
    el("h3", {}, benim ? "Değerlendirmenizi düzenleyin" : "Bu ürünü değerlendirin"),
    el("div", { class: "dg-secim-satiri" }, secici, ad), yorum,
    el("div", { class: "dg-form-alt" }, gonder,
      benim ? el("button", { class: "dugme dugme-acik", type: "button", onclick: () => { this.duzenle = false; this.ciz(); } }, "Vazgeç") : null));
  },

  async sil() {
    if (!confirm("Değerlendirmeniz silinsin mi?")) return;
    try {
      this.veri = await this.istek("DELETE");
      this.ciz();
      bildir("Değerlendirmeniz silindi.");
    } catch (h) { bildir(h.message, "danger"); }
  },

  async istek(yontem, veri) {
    const y = await fetch(`/api/hesap/degerlendirme/${this.u.id}`, { method: yontem,
      headers: { "Content-Type": "application/json" }, body: veri ? JSON.stringify(veri) : undefined });
    const j = await y.json().catch(() => ({}));
    if (!y.ok) throw new Error(j.hata || "İşlem yapılamadı.");
    return j;
  },
};

// ======================================================================
// BAŞLANGIÇ
// ======================================================================
for (const y of $$(".yil")) y.textContent = new Date().getFullYear();
Favori.ciz();
Sepet.ciz();

/** Üyenin sepeti ve favorileri sunucudan gelir; bu tarayıcıda misafirken eklenenler hesaba katılır ve tarayıcıdan
    silinir (sonraki kişi görmesin). Misafirde bir şey yapılmaz. */
async function hesapVerileriniYukle(kisi) {
  if (!kisi.giris) return;
  let j;
  try { j = await getir("/api/hesap/sepet"); } catch { HesapDeposu.yuklendi = true; return; }
  const sepet = j.sepet.map(u => ({ ...ozetle(u), adet: u.adet }));
  const favori = j.favoriler.map(ozetle);
  const misafirSepet = depo.al(MISAFIR_SEPET, []), misafirFavori = depo.al(MISAFIR_FAVORI, []);
  for (const m of misafirSepet) {
    const var_ = sepet.find(s => s.id === m.id);
    if (var_) var_.adet = Math.min(10, var_.adet + m.adet);
    else sepet.push(m);
  }
  for (const m of misafirFavori) if (!favori.some(f => f.id === m.id)) favori.unshift(m);
  Sepet.liste = sepet;
  Favori.liste = favori;
  HesapDeposu.yuklendi = true;
  if (misafirSepet.length || misafirFavori.length) {
    try { localStorage.removeItem(MISAFIR_SEPET); localStorage.removeItem(MISAFIR_FAVORI); } catch { /* yok say */ }
    if (misafirSepet.length) Sepet.kaydet();
    if (misafirFavori.length) Favori.kaydet();
  }
  Sepet.ciz();
  Favori.ciz();
  favoriDugmeleriniYaz();
}

// Başka sekmede sepet / favori değişirse bu sekme de güncellensin (misafirde tarayıcıdan, üyede sunucudan)
window.addEventListener("storage", e => {
  if (HesapDeposu.uye()) {
    if (e.key === "magaza_hesap_degisti") hesapVerileriniYukle(Oturum.kisi);
    return;
  }
  if (e.key === MISAFIR_SEPET) { Sepet.liste = depo.al(MISAFIR_SEPET, []); Sepet.ciz(); }
  if (e.key === MISAFIR_FAVORI) { Favori.liste = depo.al(MISAFIR_FAVORI, []); Favori.ciz(); favoriDugmeleriniYaz(); }
});

// Mağazadan kaldırılan ürünler misafirin sepetinde / favorilerinde kalmasın (üyede sunucu zaten ayıklar)
async function kaldirilanlariTemizle() {
  if (HesapDeposu.uye() || (!Sepet.liste.length && !Favori.liste.length)) return;
  let j;
  try { j = await getir("/api/magaza/urunler"); } catch { return; }
  const yayinda = new Set(j.urunler.map(u => u.id));
  const sepet = Sepet.liste.filter(s => yayinda.has(s.id));
  const favori = Favori.liste.filter(f => yayinda.has(f.id));
  if (sepet.length !== Sepet.liste.length) { Sepet.liste = sepet; Sepet.kaydet(); }
  if (favori.length !== Favori.liste.length) { Favori.liste = favori; Favori.kaydet(); }
}

Oturum.hazir = Oturum.yukle();
Sepet.hazir = Oturum.hazir.then(hesapVerileriniYukle);
if (document.body.dataset.sayfa === "liste") Liste.basla();
else if (document.body.dataset.sayfa === "urun") UrunSayfasi.basla();
else {
  // Hesap ve ödeme sayfaları (hesap.js): üstteki arama mağazada arar
  $("#arama-formu")?.addEventListener("submit", e => {
    e.preventDefault();
    const q = $("#arama").value.trim();
    location.href = q ? `/?ara=${encodeURIComponent(q)}` : "/";
  });
}
Sepet.hazir.then(kaldirilanlariTemizle);
