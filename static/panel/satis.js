/* Panelde satış ve yönetim: siparişler (durum, kargo takibi), üye müşteriler, satıcı hesapları. */
"use strict";

const DURUM_RENK = { alindi: "warning", hazirlaniyor: "primary", kargoda: "secondary", teslim: "success", iptal: "danger",
  iade: "dark" };
const SON_DURUMLAR = ["teslim", "iptal", "iade"];
const acikTalep = s => ["bekliyor", "onaylandi"].includes(s.talep_durum);
const tarihYaz = s => (s ? new Date(s.replace(" ", "T")).toLocaleString("tr-TR", { day: "2-digit", month: "2-digit",
  year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const durumRozeti = (d, ad) => el("span", { class: `badge badge-light-${DURUM_RENK[d]}` }, ad);

/** Hesap açık / kapalı anahtarı (satıcı ve müşteri listelerinde). */
function aktiflikAnahtari(k, kilitli, ne) {
  const g = el("input", { class: "switch-input", type: "checkbox", role: "switch", id: `aktif-${k.id}`, checked: k.aktif,
    disabled: kilitli });
  g.onchange = async () => {
    if (!g.checked && !(await onayla(`${k.ad} adlı ${ne} hesabı kapatılsın mı? Giriş yapamaz, açık oturumları kapanır.`,
      "Hesabı Kapat"))) { g.checked = true; return; }
    try {
      await api(`/api/panel/kullanicilar/${k.id}`, { yontem: "PATCH", veri: { aktif: g.checked } });
      bildir(g.checked ? `${k.ad} hesabı açıldı.` : `${k.ad} hesabı kapatıldı.`, "success");
    } catch (h) { g.checked = !g.checked; bildir(h.message, "danger"); }
  };
  return el("div", { class: "switch form-switch-custom switch-inline form-switch-success mb-0" }, g,
    el("label", { class: "switch-label", for: `aktif-${k.id}` }, ""));
}

// ======================================================================
// SİPARİŞLER
// ======================================================================
const Satis = {
  veri: null,
  filtre: "",

  async yukle() {
    try { this.veri = await api("/api/panel/siparisler"); } catch (h) { return bildir(h.message, "danger"); }
    this.rozet();
    if (Panel.sayfa === "siparisler") this.ciz();
  },

  /** Mağazadan yeni sipariş geldi / iptal edildi (WebSocket). */
  degisti() {
    this.yukle();
    if (Panel.sayfa === "gosterge") gostergeYukle();
  },

  rozet() {
    const yeni = this.veri.sayilar.durumlar.alindi, talep = this.veri.sayilar.bekleyen_talep || 0;
    const n = yeni + talep;
    const r = $("#menu-siparis-rozet");
    r.hidden = !n;
    r.textContent = n;
    r.title = [yeni ? `${yeni} yeni sipariş onay bekliyor` : "", talep ? `${talep} iptal / iade talebi yanıt bekliyor` : ""]
      .filter(Boolean).join(", ");
  },

  ciz() {
    const { siparisler, sayilar, durumlar } = this.veri;
    $("#sp-filtre").replaceChildren(
      el("a", { class: `btn btn-sm ${!this.filtre ? "btn-primary" : "btn-outline-secondary"}`, href: "#siparisler" },
        `Tümü (${sayilar.toplam})`),
      ...Object.entries(durumlar).map(([d, ad]) => el("a", {
        class: `btn btn-sm ${this.filtre === d ? `btn-${DURUM_RENK[d]}` : "btn-outline-secondary"}`, href: `#siparisler/${d}` },
      `${ad} (${sayilar.durumlar[d]})`)),
      el("a", { class: `btn btn-sm ${this.filtre === "talepler" ? "btn-warning" : "btn-outline-warning"}`, href: "#siparisler/talepler" },
        `↩️ İptal / İade Talepleri (${sayilar.talepler || 0})`));
    const ara = $("#sp-ara").value.trim().toLocaleLowerCase("tr-TR");
    const uyar = s => (this.filtre === "talepler" ? acikTalep(s) : s.durum === this.filtre);
    const liste = siparisler.filter(s => (!this.filtre || uyar(s))
      && (!ara || `${s.no} ${s.musteri.ad} ${s.musteri.eposta} ${s.teslimat.ad} ${s.teslimat.telefon}`.toLocaleLowerCase("tr-TR").includes(ara)));
    $("#sp-bos").hidden = liste.length > 0;
    $("#sp-liste").replaceChildren(...liste.map(s => el("tr", { class: s.durum === "alindi" || s.talep_durum === "bekliyor" ? "yeni-siparis" : "" },
      el("td", {}, el("b", {}, s.no), el("div", { class: "small text-muted" }, tarihYaz(s.olusturma))),
      el("td", {}, el("div", {}, s.musteri.ad), el("div", { class: "small text-muted" }, s.musteri.eposta)),
      el("td", {}, el("div", { class: "kalem-kucukler" }, ...s.kalemler.slice(0, 4).map(k =>
        el("img", { src: k.gorsel, alt: "", title: `${k.ad} × ${k.adet}`, loading: "lazy" }))),
      el("div", { class: "small text-muted" }, `${s.adet} ürün`)),
      el("td", {}, el("b", {}, para(s.toplam))),
      el("td", { class: "small" }, s.odeme_adi),
      el("td", {}, durumRozeti(s.durum, s.durum_adi),
        acikTalep(s) ? el("div", { class: "mt-1" }, el("span", { class: "badge badge-light-warning" },
          `↩️ ${s.talep_adi}: ${s.talep_durum_adi}`)) : null),
      el("td", { class: "text-end" }, el("button", { class: "btn btn-sm btn-outline-primary", onclick: () => this.detay(s.id) },
        "Detay / Durum")))));
  },

  detay(id) {
    const s = this.veri.siparisler.find(x => x.id === id);
    if (!s) return;
    const { durumlar } = this.veri;
    const bitti = SON_DURUMLAR.includes(s.durum);
    const t = s.teslimat;
    // "İade edildi" elle seçilmez: müşterinin iade talebiyle tamamlanır
    const secim = el("select", { class: "form-select", disabled: bitti },
      ...Object.entries(durumlar).filter(([d]) => d !== "iade" || s.durum === "iade").map(([d, ad]) => el("option", { value: d }, ad)));
    secim.value = s.durum;
    const firma = el("input", { class: "form-control", value: s.kargo_firmasi, placeholder: "Örn. Yurtiçi Kargo", disabled: bitti });
    const takip = el("input", { class: "form-control", value: s.takip_no, placeholder: "Takip numarası", disabled: bitti });
    const aciklama = el("input", { class: "form-control", placeholder: "Müşterinin göreceği kısa not (isteğe bağlı)", disabled: bitti });
    const kargoSatiri = el("div", { class: "row g-2 mb-2" },
      el("div", { class: "col-sm-6" }, el("label", { class: "form-label" }, "Kargo firması"), firma),
      el("div", { class: "col-sm-6" }, el("label", { class: "form-label" }, "Takip numarası"), takip));
    const kargoGoster = () => { kargoSatiri.hidden = !["kargoda", "teslim"].includes(secim.value); };
    secim.onchange = kargoGoster;
    kargoGoster();

    const icerik = el("div", { class: "siparis-detay" },
      el("div", { class: "row g-3 mb-3" },
        el("div", { class: "col-md-6" }, el("h6", {}, "Müşteri"),
          el("div", {}, s.musteri.ad), el("div", { class: "text-muted small" }, s.musteri.eposta),
          el("div", { class: "text-muted small mt-1" }, `Sipariş: ${tarihYaz(s.olusturma)} • ${s.odeme_adi}`)),
        el("div", { class: "col-md-6" }, el("h6", {}, "Teslimat adresi"),
          el("div", {}, `${t.ad} — ${t.telefon}`), el("div", { class: "small" }, `${t.adres}, ${t.ilce} / ${t.il}`),
          t.not ? el("div", { class: "small mt-1 text-warning" }, `Not: ${t.not}`) : null)),
      el("table", { class: "table table-sm align-middle" }, el("tbody", {},
        ...s.kalemler.map(k => el("tr", {},
          el("td", { style: "width:60px" }, el("img", { src: k.gorsel, alt: "", class: "detay-kucuk" })),
          el("td", {}, el("a", { href: `#urun/${k.urun_id}` }, k.ad), el("div", { class: "small text-muted" }, `${TIP_SIMGE[k.tip] || ""} ${k.adet} × ${para(k.fiyat)}`)),
          el("td", { class: "text-end" }, el("b", {}, para(k.adet * k.fiyat))))),
        el("tr", {}, el("td", { colspan: 2, class: "text-end" }, el("b", {}, "Toplam")), el("td", { class: "text-end" }, el("b", {}, para(s.toplam)))))),
      this.talepBolumu(s, () => kapat()),
      el("h6", { class: "mt-3" }, "Durum"),
      bitti ? el("p", { class: "text-muted small" }, `Sipariş ${s.durum_adi.toLocaleLowerCase("tr-TR")}; durumu artık değiştirilemez.`) : null,
      el("div", { class: "mb-2" }, secim),
      kargoSatiri,
      el("div", { class: "mb-3" }, aciklama),
      el("h6", {}, "Geçmiş"),
      el("ul", { class: "siparis-gecmis" }, ...s.gecmis.map(g => el("li", {},
        durumRozeti(g.durum, durumlar[g.durum]), " ", g.aciklama,
        el("div", { class: "small text-muted" }, `${tarihYaz(g.zaman)} • ${g.kim}`)))));

    const kapat = modalAc(`📦 Sipariş ${s.no}`, icerik, [
      { metin: "Kapat", tikla: k => k() },
      bitti ? null : { metin: "Durumu Kaydet", sinif: "btn-primary", ana: true, tikla: async k => {
        if (secim.value === "iptal" && s.durum !== "iptal"
          && !(await onayla(`${s.no} iptal edilsin mi? Bu işlem geri alınamaz.`, "İptal Et"))) return;
        try {
          await api(`/api/panel/siparisler/${s.id}`, { yontem: "PATCH", veri: { durum: secim.value,
            kargo_firmasi: firma.value, takip_no: takip.value, aciklama: aciklama.value } });
          bildir(`${s.no}: ${durumlar[secim.value]}`, "success");
          k();
          this.yukle();
        } catch (h) { bildir(h.message, "danger"); }
      } },
    ].filter(Boolean), null, "lg");
  },

  /** Sipariş detayında müşterinin iptal / iade talebi ve satıcının kararı. */
  talepBolumu(s, kapat) {
    if (!s.talep) return null;
    const iade = s.talep === "iade";
    const notu = el("textarea", { class: "form-control", rows: 2, maxlength: 500 });
    const karar = async (k, onay) => {
      if (onay && !(await onayla(onay, "Evet", "btn-primary"))) return;
      try {
        await api(`/api/panel/siparisler/${s.id}/talep`, { yontem: "POST", veri: { karar: k, not: notu.value } });
        bildir(`${s.no}: ${s.talep_adi} — ${{ onayla: "onaylandı", reddet: "reddedildi", tamamla: "iade tamamlandı" }[k]}.`, "success");
        kapat();
        this.yukle();
      } catch (h) { bildir(h.message, "danger"); }
    };
    const dugmeler = [];
    let ipucu = "";
    if (s.talep_durum === "bekliyor") {
      ipucu = iade ? "Onaylarken müşteriye ürünü nasıl geri göndereceğini yazın (kargo firması, adres / anlaşmalı kargo kodu). Reddederken sebebini yazın."
        : "Onaylarsanız sipariş iptal edilir" + (s.odeme === "kart" ? " ve ödeme karta iade edilir." : ".") + " Reddederken sebebini yazın.";
      dugmeler.push(el("button", { class: "btn btn-success", onclick: () => karar("onayla",
        iade ? null : `${s.no} iptal edilsin mi?${s.odeme === "kart" ? " Ödeme karta iade edilir." : ""}`) }, "Onayla"),
      el("button", { class: "btn btn-outline-danger", onclick: () => karar("reddet") }, "Reddet"));
    } else if (s.talep_durum === "onaylandi" && iade) {
      ipucu = "Ürün size geri ulaştığında iadeyi tamamlayın: " + (s.odeme === "kart" ? `${para(s.toplam)} müşterinin kartına iade edilir.`
        : `${para(s.toplam)} tutarı müşterinin IBAN'ına (${s.talep_iban}) havaleyle gönderin.`);
      dugmeler.push(el("button", { class: "btn btn-primary", onclick: () => karar("tamamla",
        `Ürün geri alındı mı? ${s.odeme === "kart" ? `${para(s.toplam)} karta iade edilecek.` : "Para iadesini IBAN'a gönderdiğinizi onaylıyorsunuz."}`) },
      "Ürün Geri Geldi — İadeyi Tamamla"));
    }
    return el("div", { class: `talep-panel border rounded p-3 mb-3 ${acikTalep(s) ? "border-warning" : ""}` },
      el("div", { class: "d-flex justify-content-between align-items-center mb-1" },
        el("h6", { class: "mb-0" }, `↩️ ${s.talep_adi}`), el("span", { class: "badge badge-light-warning" }, s.talep_durum_adi)),
      el("div", { class: "small" }, el("b", {}, "Sebep: "), s.talep_neden, s.talep_aciklama ? ` — ${s.talep_aciklama}` : ""),
      el("div", { class: "small text-muted" }, `Talep: ${tarihYaz(s.talep_zaman)}`, s.talep_iban ? ` • IBAN: ${s.talep_iban}` : ""),
      s.talep_cevap ? el("div", { class: "small mt-1" }, el("b", {}, "Yanıtınız: "), s.talep_cevap) : null,
      dugmeler.length ? el("div", { class: "mt-2" },
        el("label", { class: "form-label small mb-1" }, iade && s.talep_durum === "bekliyor" ? "Müşteriye not / iade talimatı" : "Müşteriye not"),
        notu, el("div", { class: "form-text" }, ipucu),
        el("div", { class: "d-flex gap-2 mt-2" }, ...dugmeler)) : null);
  },
};

Panel.girince("siparisler", param => {
  Satis.filtre = Object.keys(DURUM_RENK).includes(param) || param === "talepler" ? param : "";
  if (Satis.veri) Satis.ciz();
  Satis.yukle();
});
$("#sp-ara").addEventListener("input", bekletip(() => Satis.veri && Satis.ciz(), 200));

// ======================================================================
// MÜŞTERİLER
// ======================================================================
let musteriListesi = [];
async function musterileriYukle() {
  try { musteriListesi = await api("/api/panel/musteriler"); } catch (h) { return bildir(h.message, "danger"); }
  musterileriCiz();
}
function musterileriCiz() {
  const ara = $("#mu-ara").value.trim().toLocaleLowerCase("tr-TR");
  const liste = musteriListesi.filter(m => !ara || `${m.ad} ${m.kullanici_adi} ${m.eposta} ${m.telefon}`.toLocaleLowerCase("tr-TR").includes(ara));
  $("#mu-toplam").textContent = musteriListesi.length;
  $("#mu-bos").hidden = liste.length > 0;
  $("#mu-liste").replaceChildren(...liste.map(m => el("tr", {},
    el("td", {}, el("div", { class: "fw-bold" }, m.ad), el("div", { class: "small text-muted" }, `@${m.kullanici_adi} · ${m.eposta}`)),
    el("td", {}, m.telefon || "—"),
    el("td", { class: "small" }, tarihYaz(m.kayit_tarihi)),
    el("td", {}, m.siparis_sayisi ? el("a", { href: "#siparisler", onclick: () => { $("#sp-ara").value = m.eposta || m.ad; } },
      `${m.siparis_sayisi} sipariş`) : "—"),
    el("td", {}, m.harcama ? el("b", {}, para(m.harcama)) : "—"),
    el("td", { class: "small" }, m.son_siparis ? tarihYaz(m.son_siparis) : "—"),
    el("td", {}, aktiflikAnahtari(m, false, "müşteri")))));
}
Panel.girince("musteriler", musterileriYukle);
$("#mu-ara").addEventListener("input", bekletip(musterileriCiz, 200));

// ======================================================================
// SATICILAR
// ======================================================================
async function saticilariYukle() {
  let j;
  try { j = await api("/api/panel/saticilar"); } catch (h) { return bildir(h.message, "danger"); }
  $("#sa-liste").replaceChildren(...j.saticilar.map(k => el("tr", {},
    el("td", {}, el("div", { class: "fw-bold" }, k.ad, k.id === j.ben ? el("span", { class: "badge badge-light-info ms-2" }, "Siz") : null)),
    el("td", {}, `@${k.kullanici_adi}`, k.eposta ? el("div", { class: "small text-muted" }, k.eposta) : null),
    el("td", { class: "small" }, tarihYaz(k.kayit_tarihi)),
    el("td", { class: "small" }, tarihYaz(k.son_giris)),
    el("td", {}, aktiflikAnahtari(k, k.id === j.ben, "satıcı")))));
}
Panel.girince("saticilar", saticilariYukle);

$("#sa-ekle").onclick = () => {
  const alan = (etiket, girdi, ipucu) => el("div", { class: "mb-3" }, el("label", { class: "form-label" }, etiket), girdi,
    ipucu ? el("div", { class: "form-text" }, ipucu) : null);
  const ad = el("input", { class: "form-control", maxlength: 50, autocomplete: "off" });
  const kadi = el("input", { class: "form-control", maxlength: 30, autocomplete: "off", pattern: "[A-Za-z0-9._\\-]{3,30}" });
  const eposta = el("input", { class: "form-control", type: "email", maxlength: 100, autocomplete: "off", placeholder: "isteğe bağlı" });
  const sifre = el("input", { class: "form-control", type: "password", autocomplete: "new-password", minlength: 6 });
  const sifre2 = el("input", { class: "form-control", type: "password", autocomplete: "new-password", minlength: 6 });
  const kapat = modalAc("＋ Yeni Satıcı", el("form", { onsubmit: e => e.preventDefault() },
    el("p", { class: "text-muted small" }, "Yeni satıcı panele bu kullanıcı adı (ya da e-posta) ve şifreyle girer; kendi odalarını ve ürünlerini yönetir."),
    alan("Ad soyad", ad), alan("Kullanıcı adı", kadi, "3-30 karakter: harf (Türkçe karakter olmadan), rakam, nokta, tire, alt çizgi."),
    alan("E-posta", eposta),
    alan("Şifre", sifre, "En az 6 karakter."), alan("Şifre (tekrar)", sifre2)), [
    { metin: "Vazgeç", tikla: k => k() },
    { metin: "Satıcıyı Ekle", sinif: "btn-success", ana: true, tikla: async k => {
      if (!ad.value.trim() || !kadi.value.trim() || !sifre.value) return bildir("Bütün alanları doldurun.", "warning");
      if (sifre.value !== sifre2.value) return bildir("Şifreler birbirini tutmuyor.", "warning");
      try {
        const j = await api("/api/panel/saticilar", { yontem: "POST", veri: { ad: ad.value, kullanici_adi: kadi.value, eposta: eposta.value, sifre: sifre.value } });
        bildir(`${j.satici.ad} satıcı olarak eklendi (@${j.satici.kullanici_adi}).`, "success");
        k();
        saticilariYukle();
      } catch (h) { bildir(h.message, "danger"); }
    } },
  ], null, "");
  kapat.eleman.addEventListener("shown.bs.modal", () => ad.focus());
};

// Menüdeki yeni sipariş rozeti panel açılınca da görünsün
Satis.yukle();
