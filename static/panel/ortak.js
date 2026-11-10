/* Tablo Stüdyo yönetim paneli: ortak yardımcılar, bildirim (toast), pencere (modal), AJAX ve sayfa yönlendirmesi.
   Sayfalar tek HTML içinde bölüm bölümdür; adres çubuğundaki #oda, #urunler, #urun/12 gibi işaretle değişir. */
"use strict";

// ======================================================================
// YARDIMCILAR
// ======================================================================
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

// localStorage gizli pencerede / kapalıyken hata verebilir: sadece kolaylık için kullanılır
const depo = {
  al(k, v) { try { return localStorage.getItem(k) ?? v; } catch { return v; } },
  yaz(k, v) { try { localStorage.setItem(k, v); } catch { /* yok say */ } },
};

/** En fazla ms'de bir çağırır; son çağrı kaybolmaz. .iptal() bekleyen çağrıyı siler. */
function kisitla(fn, ms) {
  let son = 0, zam = null, arg = [];
  const f = (...a) => {
    arg = a;
    const kalan = ms - (Date.now() - son);
    if (kalan <= 0) { son = Date.now(); fn(...arg); }
    else if (!zam) zam = setTimeout(() => { zam = null; son = Date.now(); fn(...arg); }, kalan);
  };
  f.iptal = () => { clearTimeout(zam); zam = null; };
  return f;
}

/** Son çağrıdan ms sonra bir kez çağırır (arama kutusu gibi). */
function bekletip(fn, ms) {
  let zam = null;
  return (...a) => { clearTimeout(zam); zam = setTimeout(() => fn(...a), ms); };
}

const kisalt = (s, n) => (s.length <= n ? s : s.slice(0, n - 1) + "…");
const paraBicimi = new Intl.NumberFormat("tr-TR", { style: "currency", currency: "TRY" });
const para = n => paraBicimi.format(n);
const TIP_SIMGE = { yatay: "▭", dikey: "▯", kare: "□", yuvarlak: "◯" };

function indir(url) {
  const a = el("a", { href: url, download: "" });
  document.body.append(a);
  a.click();
  a.remove();
}

/** JSON API çağrısı. Hata olursa sunucunun mesajıyla Error fırlatır. */
async function api(yol, { yontem = "GET", veri, form } = {}) {
  const ayar = { method: yontem, headers: {} };
  if (veri !== undefined) {
    ayar.headers["Content-Type"] = "application/json";
    ayar.body = JSON.stringify(veri);
  }
  if (form) ayar.body = form;
  const y = await fetch(yol, ayar);
  if (y.status === 401) {
    location.href = "/giris";
    throw new Error("Oturum kapalı, lütfen tekrar giriş yapın.");
  }
  const j = await y.json().catch(() => ({}));
  if (!y.ok) throw new Error(j.hata || `İşlem yapılamadı (${y.status})`);
  return j;
}

async function yukle(url, dosyalar, alan) {
  const fd = new FormData();
  for (const d of dosyalar) fd.append(alan, d, d.name);
  return api(url, { yontem: "POST", form: fd });
}

// ======================================================================
// BİLDİRİM (Bootstrap toast) VE PENCERELER (Bootstrap modal)
// ======================================================================
const TOAST_RENK = { info: "primary", success: "success", warning: "warning", danger: "danger", secondary: "secondary" };

function bildir(metin, tur = "info") {
  const renk = TOAST_RENK[tur] || "primary";
  const t = el("div", { class: `toast align-items-center text-bg-${renk} border-0`, role: "alert",
    "aria-live": "assertive", "aria-atomic": "true" },
  el("div", { class: "d-flex" },
    el("div", { class: "toast-body", style: "white-space:pre-line" }, metin),
    el("button", { type: "button", class: `btn-close ${renk === "warning" ? "" : "btn-close-white"} me-2 m-auto`,
      "data-bs-dismiss": "toast", "aria-label": "Kapat" })));
  $("#toast-kok").append(t);
  t.addEventListener("hidden.bs.toast", () => t.remove());
  new bootstrap.Toast(t, { delay: tur === "danger" ? 7000 : 4500 }).show();
}

const modalAcikMi = () => !!document.querySelector(".modal.show, .modal.showing");

/**
 * Pencere açar. butonlar: [{ metin, sinif, ana, tikla(kapat) }]. Enter ana butona basar, Esc kapatır.
 * boyut: "sm" | "lg" | "xl" | "" (orta). Dönüş: kapat fonksiyonu.
 */
function modalAc(baslik, icerik, butonlar, kapaninca, boyut = "lg") {
  const alt = el("div", { class: "modal-footer" });
  const m = el("div", { class: "modal fade", tabindex: "-1", "aria-hidden": "true" },
    el("div", { class: `modal-dialog modal-dialog-centered modal-dialog-scrollable ${boyut ? "modal-" + boyut : ""}` },
      el("div", { class: "modal-content" },
        el("div", { class: "modal-header" }, el("h5", { class: "modal-title" }, baslik),
          el("button", { type: "button", class: "btn-close", "data-bs-dismiss": "modal", "aria-label": "Kapat" })),
        el("div", { class: "modal-body" }, icerik),
        alt)));
  for (const b of butonlar) {
    alt.append(el("button", { type: "button", class: `btn ${b.sinif || "btn-outline-secondary"}${b.ana ? " ana-buton" : ""}`,
      onclick: () => b.tikla(kapat) }, b.metin));
  }
  document.body.append(m);
  const bm = new bootstrap.Modal(m);
  let kapali = false;
  function kapat() {
    if (kapali) return;
    kapali = true;
    bm.hide();
  }
  m.addEventListener("hidden.bs.modal", () => {
    kapali = true;
    bm.dispose();
    m.remove();
    kapaninca?.();
  });
  m.addEventListener("keydown", e => {
    if (e.key === "Enter" && !["BUTTON", "TEXTAREA"].includes(e.target.tagName)) {
      const ana = $(".ana-buton", alt);
      if (ana) { e.preventDefault(); ana.click(); }
    }
  });
  bm.show();
  kapat.eleman = m;
  return kapat;
}

function sor(baslik, etiket, varsayilan = "") {
  return new Promise(coz => {
    let sonuc = null;
    const girdi = el("input", { type: "text", class: "form-control", value: varsayilan });
    const kapat = modalAc(baslik, el("label", { class: "w-100" }, el("div", { class: "mb-2" }, etiket), girdi), [
      { metin: "İptal", tikla: k => k() },
      { metin: "Tamam", sinif: "btn-primary", ana: true, tikla: k => { sonuc = girdi.value; k(); } },
    ], () => coz(sonuc), "");
    kapat.eleman.addEventListener("shown.bs.modal", () => { girdi.focus(); girdi.select(); });
  });
}

function onayla(metin, evet = "Evet", sinif = "btn-danger") {
  return new Promise(coz => {
    let sonuc = false;
    modalAc("Onay", el("p", { class: "mb-0" }, metin), [
      { metin: "Vazgeç", tikla: k => k() },
      { metin: evet, sinif, ana: true, tikla: k => { sonuc = true; k(); } },
    ], () => coz(sonuc), "");
  });
}

// ======================================================================
// HESAP: şifre değiştir, çıkış
// ======================================================================
const Hesap = { ad: "", kullanici_adi: "" };

function hesapYaz(k) {
  Object.assign(Hesap, k);
  $("#ad-etiket").textContent = k.ad;
  $("#kullanici-adi-etiket").textContent = "@" + k.kullanici_adi;
  $("#avatar-harf").textContent = (k.ad || "?").trim().charAt(0).toLocaleUpperCase("tr-TR");
}

async function hesapPenceresi() {
  let ben;
  try { ben = await api("/api/magaza/ben"); } catch (h) { return bildir(h.message, "danger"); }
  const metin = (deger, oto, uzunluk) => el("input", { type: "text", class: "form-control", value: deger || "",
    autocomplete: oto, maxlength: uzunluk });
  const ad = metin(ben.ad, "name", 50), kadi = metin(ben.kullanici_adi, "username", 30);
  const eposta = el("input", { type: "email", class: "form-control", value: ben.eposta || "", autocomplete: "email",
    maxlength: 100, placeholder: "isteğe bağlı" });
  const sifreGirdisi = oto => el("input", { type: "password", class: "form-control", autocomplete: oto, minlength: 6 });
  const eski = sifreGirdisi("current-password"), yeni = sifreGirdisi("new-password"), yeni2 = sifreGirdisi("new-password");
  const satir = (etiket, g, ipucu) => el("div", { class: "mb-3" }, el("label", { class: "form-label" }, etiket), g,
    ipucu ? el("div", { class: "form-text" }, ipucu) : null);

  const bilgiKaydet = async () => {
    for (const g of [ad, kadi, eposta]) g.classList.remove("is-invalid");
    try {
      const k = await api("/api/hesap/bilgiler", { yontem: "PUT",
        veri: { ad: ad.value, kullanici_adi: kadi.value.trim(), eposta: eposta.value.trim() } });
      hesapYaz(k);
      bildir("Hesap bilgileriniz kaydedildi.", "success");
    } catch (h) {
      if (/kullanıcı adı/i.test(h.message)) kadi.classList.add("is-invalid");
      if (/e-posta/i.test(h.message)) eposta.classList.add("is-invalid");
      if (/ad soyad/i.test(h.message)) ad.classList.add("is-invalid");
      bildir(h.message, "danger");
    }
  };
  const sifreKaydet = async () => {
    if (!eski.value || !yeni.value) return bildir("Mevcut ve yeni şifreyi yazın.", "warning");
    if (yeni.value !== yeni2.value) return bildir("Yeni şifreler birbirini tutmuyor.", "warning");
    try {
      await api("/api/sifre", { yontem: "POST", veri: { eski: eski.value, yeni: yeni.value } });
    } catch (h) { return bildir(h.message, "danger"); }
    for (const g of [eski, yeni, yeni2]) g.value = "";
    bildir("Şifre değiştirildi. Diğer cihazlardaki oturumlar kapatıldı.", "success");
  };

  const kapat = modalAc("👤 Hesap Ayarları", el("form", { onsubmit: e => e.preventDefault() },
    el("h6", { class: "mb-3" }, "Hesap Bilgileri"),
    satir("Ad soyad", ad),
    el("div", { class: "row" },
      el("div", { class: "col-sm-6" }, satir("Kullanıcı adı", kadi, "Harf (Türkçe karakter olmadan), rakam, . - _")),
      el("div", { class: "col-sm-6" }, satir("E-posta", eposta, "Kullanıcı adı ya da e-postayla giriş yapılır."))),
    el("button", { type: "button", class: "btn btn-primary", onclick: bilgiKaydet }, "Bilgileri Kaydet"),
    el("hr", { class: "my-4" }),
    el("h6", { class: "mb-3" }, "Şifre Değiştir"),
    satir("Mevcut şifre", eski),
    el("div", { class: "row" },
      el("div", { class: "col-sm-6" }, satir("Yeni şifre (en az 6 karakter)", yeni)),
      el("div", { class: "col-sm-6" }, satir("Yeni şifre (tekrar)", yeni2))),
    el("button", { type: "button", class: "btn btn-outline-primary", onclick: sifreKaydet }, "Şifreyi Değiştir")), [
    { metin: "Kapat", tikla: k => k() },
  ], null, "");
  kapat.eleman.addEventListener("shown.bs.modal", () => ad.focus());
}

function cikisYap() {
  const f = el("form", { method: "post", action: "/cikis" });
  document.body.append(f);
  f.submit();
}

// ======================================================================
// SAYFA YÖNLENDİRMESİ (#gosterge, #oda, #tablo, #urunler, #urun-ekle, #urun/12)
// ======================================================================
const SAYFALAR = {
  gosterge: { bolum: "s-gosterge", menu: "gosterge", baslik: "Gösterge Paneli" },
  oda: { bolum: "s-oda", menu: "oda", ust: "Stüdyo", baslik: "Oda Sunumları" },
  tablo: { bolum: "s-tablo", menu: "tablo", ust: "Stüdyo", baslik: "Tablo Ekle" },  urunler: { bolum: "s-urunler", menu: "magaza", alt: "urunler", ust: "Mağaza", baslik: "Ürün Listesi" },
  "urun-ekle": { bolum: "s-urun-form", menu: "magaza", alt: "urun-ekle", ust: "Mağaza", baslik: "Ürün Ekle" },
  urun: { bolum: "s-urun-form", menu: "magaza", ust: "Mağaza", baslik: "Ürün Düzenle" },
  siparisler: { bolum: "s-siparisler", menu: "siparisler", ust: "Satış", baslik: "Siparişler" },
  musteriler: { bolum: "s-musteriler", menu: "musteriler", ust: "Satış", baslik: "Müşteriler" },
  destek: { bolum: "s-destek", menu: "destek", ust: "Satış", baslik: "Canlı Destek" },
  degerlendirmeler: { bolum: "s-degerlendirmeler", menu: "degerlendirmeler", ust: "Satış", baslik: "Değerlendirmeler" },
  saticilar: { bolum: "s-saticilar", menu: "saticilar", ust: "Yönetim", baslik: "Satıcılar" },
};

const Panel = {
  sayfa: null,
  girisler: {},     // sayfa adı -> [fn(param)] sayfa her açıldığında
  girince(ad, fn) { (this.girisler[ad] ||= []).push(fn); },
  git(hash) { if (location.hash === hash) sayfaGoster(); else location.hash = hash; },
};

function rotaCoz() {
  const [ad, param] = (location.hash.slice(1) || "gosterge").split("/");
  return SAYFALAR[ad] ? { ad, param } : { ad: "gosterge", param: undefined };
}

function sayfaGoster() {
  const { ad, param } = rotaCoz();
  const s = SAYFALAR[ad];
  // Pencere açıkken sayfa değiştiyse (ör. geri tuşu) pencere yeni sayfanın üstünde kalmasın
  for (const m of $$(".modal.show")) bootstrap.Modal.getInstance(m)?.hide();
  for (const b of $$("section.sayfa")) b.hidden = b.id !== s.bolum;

  for (const li of $$("#menuler > li.menu[data-menu]")) li.classList.toggle("active", li.dataset.menu === s.menu);
  for (const li of $$("#magaza-menu li[data-alt]")) li.classList.toggle("active", li.dataset.alt === s.alt);
  const alt = $("#magaza-menu");
  const magazada = s.menu === "magaza";
  alt.classList.toggle("show", magazada);
  $('[href="#magaza-menu"]').setAttribute("aria-expanded", String(magazada));

  $("#kirinti").replaceChildren(...[
    el("li", { class: "breadcrumb-item" }, el("a", { href: "#gosterge" }, "Panel")),
    s.ust ? el("li", { class: "breadcrumb-item" }, s.ust) : null,
    el("li", { class: "breadcrumb-item active", "aria-current": "page" }, s.baslik)].filter(Boolean));
  document.title = `${s.baslik} | Tablo Stüdyo Panel`;

  // Telefonda menü açıksa sayfa seçilince kapansın
  if (window.innerWidth <= 991) {
    $("#container").classList.add("sidebar-closed");
    $("#container").classList.remove("sbar-open");
    $(".overlay").classList.remove("show");
    document.documentElement.classList.remove("sidebar-noneoverflow");
    document.body.classList.remove("sidebar-noneoverflow");
  }

  const ilk = Panel.sayfa !== ad;
  Panel.sayfa = ad;
  for (const fn of Panel.girisler[ad] || []) fn(param, ilk);
  if (ilk) window.scrollTo(0, 0);
}

window.addEventListener("hashchange", sayfaGoster);

// Sayfadan ayrılırken kaydedilmemiş form uyarısı (ürün formu kendi bayrağını tutar)
const Kaydedilmemis = { var: false };
window.addEventListener("beforeunload", e => { if (Kaydedilmemis.var) { e.preventDefault(); e.returnValue = ""; } });

// ======================================================================
// GÖRÜNÜM: Equation (şablonun kendi görünümü) ya da Etsy (etsy.css). Koyu / açık tema ikisinde ortaktır
// (şablonun ay / güneş düğmesi). Seçim tarayıcıda saklanır; <head>'deki satır sayfa çizilmeden uygular.
// ======================================================================
/** Koyu / açık tema ya da görünüm değişirken yumuşak geçiş: tarayıcı destekliyorsa sayfanın eski ve yeni hâli
    arasında çapraz geçiş (View Transitions), desteklemiyorsa renkler CSS geçişiyle kayar (html.tema-gecisi). */
function yumusakGecis(degistir) {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return degistir();
  if (document.startViewTransition) { document.startViewTransition(degistir); return; }
  const h = document.documentElement;
  h.classList.add("tema-gecisi");
  degistir();
  clearTimeout(yumusakGecis.zaman);
  yumusakGecis.zaman = setTimeout(() => h.classList.remove("tema-gecisi"), 450);
}

// Şablonun ay / güneş düğmesi (app.js) body.dark'ı anında değiştirir: tıklama önce burada yakalanır, şablonun
// işleyicisi yumuşak geçişin içinde çalıştırılır. click() değil dispatchEvent: tıklama sürerken aynı öğede
// click() tarayıcıca yok sayılır (yedek yolda geçiş aynı tıklamanın içinde olur)
let temaDugmesiIletiliyor = false;
document.addEventListener("click", e => {
  const dugme = e.target.closest?.(".theme-toggle");
  if (!dugme || temaDugmesiIletiliyor) return;
  e.preventDefault();
  e.stopImmediatePropagation();
  yumusakGecis(() => {
    temaDugmesiIletiliyor = true;
    try { dugme.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })); }
    finally { temaDugmesiIletiliyor = false; }
  });
}, true);

const Gorunum = {
  ad: { equation: "Equation", etsy: "Etsy" },
  simdiki() { return document.documentElement.dataset.yapi === "etsy" ? "etsy" : "equation"; },
  yaz() {
    const diger = this.simdiki() === "etsy" ? "equation" : "etsy";
    $("#yapi-ad").textContent = `${this.ad[diger]} Görünümü`;
    $("#yapi-degistir").title = `${this.ad[diger]} görünümüne geç`;
  },
  degistir() {
    const yeni = this.simdiki() === "etsy" ? "equation" : "etsy";
    yumusakGecis(() => {
      document.documentElement.dataset.yapi = yeni;
      this.yaz();
    });
    depo.yaz("panel_yapi", yeni);
    bildir(`${this.ad[yeni]} görünümüne geçildi.`, "secondary");
  },
};

// ======================================================================
// BAŞLANGIÇ
// ======================================================================
Gorunum.yaz();
$("#yapi-degistir").onclick = () => Gorunum.degistir();
$("#hesap").onclick = hesapPenceresi;
$("#cikis").onclick = cikisYap;
$("#yil").textContent = new Date().getFullYear();
