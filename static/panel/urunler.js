/* Panelde gösterge paneli, ürün listesi ve ürün ekle / düzenle formu. Bütün işlemler AJAX (fetch) ile yapılır.
   Ürün görselleri, ürünün tipindeki kayıtlı odalarda "Tablo Ekle"deki çizimin aynısıdır; oda "Oda Sunumları"nda
   değişince görseller de değişir. */
"use strict";

const TIP_ADLARI = { yatay: "Yatay", dikey: "Dikey", kare: "Kare", yuvarlak: "Yuvarlak" };
const tipRozeti = tip => el("span", { class: `badge tip-rozet tip-${tip}` }, `${TIP_SIMGE[tip]} ${TIP_ADLARI[tip]}`);

/** "1.250,50" / "1250.5" / "1250" -> 1250.5 (boşsa null, geçersizse NaN) */
function fiyatOku(metin) {
  let s = String(metin || "").trim().replace(/\s|₺|TL/gi, "");
  if (!s) return null;
  if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
  return /^\d+(\.\d+)?$/.test(s) ? Number(s) : NaN;
}

const fiyatYaz = n => (n == null ? "" : n.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

function fiyatHucresi(u) {
  return el("div", { class: "fiyat-hucre" }, el("b", {}, para(u.fiyat)),
    u.eski_fiyat ? el("del", { class: "text-muted small ms-1" }, para(u.eski_fiyat)) : null);
}

/** Yüklenene kadar dönen işaret gösteren görsel kutusu. */
function yuklenenGorsel(src, alt = "", sinif = "") {
  const kutu = el("div", { class: `gorsel-kutu yukleniyor ${sinif}` });
  const img = el("img", { src, alt, loading: "lazy", decoding: "async" });
  img.onload = () => kutu.classList.remove("yukleniyor");
  img.onerror = () => { kutu.classList.remove("yukleniyor"); kutu.classList.add("hatali"); };
  kutu.append(img, el("span", { class: "spinner-border spinner-border-sm text-primary" }));
  return kutu;
}

// ======================================================================
// GÖSTERGE PANELİ
// ======================================================================
async function gostergeYukle() {
  let o;
  try { o = await api("/api/panel/ozet"); } catch (h) { return bildir(h.message, "danger"); }
  hesapYaz(o.kullanici);
  $("#g-oda").textContent = o.oda_sayisi;
  $("#g-urun").textContent = o.urun_sayisi;
  $("#g-yayinda").textContent = o.yayinda;
  $("#g-tipler").textContent = Object.entries(o.oda_tipleri).map(([t, n]) => `${TIP_SIMGE[t]} ${n}`).join("  ");
  $("#g-bekleyen").textContent = o.siparis.durumlar.alindi + o.siparis.durumlar.hazirlaniyor;
  $("#g-ciro").textContent = para(o.siparis.ciro);
  $("#g-siparis-sayisi").textContent = `Toplam satış (${o.siparis.toplam} sipariş, iptaller hariç tutar)`;
  $("#g-musteri").textContent = o.musteri_sayisi;
  $("#g-son-urunler").replaceChildren(...(o.son_urunler.length ? o.son_urunler.map(u =>
    el("div", { class: "col-sm-6 col-lg-4" },
      el("a", { class: "mini-urun", href: `#urun/${u.id}` },
        yuklenenGorsel(u.kapak, u.ad),
        el("div", { class: "mini-urun-govde" },
          el("div", { class: "mini-urun-ad" }, u.ad),
          el("div", { class: "d-flex justify-content-between align-items-center" }, fiyatHucresi(u),
            el("span", { class: `badge ${u.yayinda ? "badge-light-success" : "badge-light-secondary"}` },
              u.yayinda ? "Yayında" : "Taslak"))))))
    : [el("div", { class: "col-12 bos-durum" }, el("div", { class: "fs-1" }, "🖼️"), el("p", {}, "Henüz ürün eklenmedi."),
      el("a", { class: "btn btn-success", href: "#urun-ekle" }, "＋ İlk Ürünü Ekle"))]));
}
Panel.girince("gosterge", gostergeYukle);

// ======================================================================
// ÜRÜN LİSTESİ
// ======================================================================
for (const [t, ad] of Object.entries(TIP_ADLARI)) $("#ul-tip").append(el("option", { value: t }, `${TIP_SIMGE[t]} ${ad}`));

let listeIstegi = 0;
async function urunleriYukle() {
  const no = ++listeIstegi;
  const q = new URLSearchParams({ ara: $("#ul-ara").value, tip: $("#ul-tip").value, sirala: $("#ul-sirala").value });
  let j;
  try { j = await api(`/api/urunler?${q}`); } catch (h) { return bildir(h.message, "danger"); }
  if (no !== listeIstegi) return;   // bu arada yeni arama yapıldı
  $("#ul-toplam").textContent = j.toplam;
  const filtreli = $("#ul-ara").value || $("#ul-tip").value;
  $("#ul-bos").hidden = j.toplam > 0;
  $("#ul-bos p").textContent = filtreli ? "Aramaya uyan ürün yok." : "Henüz ürün yok.";
  $("#ul-liste").replaceChildren(...j.urunler.map(u => {
    const yayin = el("input", { class: "switch-input", type: "checkbox", role: "switch", id: `yayin-${u.id}`, checked: u.yayinda });
    // Mağazada değilse ürünün sayfası kapalı: bağlantısı da gösterilmez
    const gor = el("a", { class: "btn btn-sm btn-outline-secondary me-1", href: u.adres, target: "_blank", title: "Mağazada gör" }, "👁");
    gor.hidden = !u.yayinda;
    yayin.onchange = async () => {
      try {
        await api(`/api/urunler/${u.id}/yayin`, { yontem: "PATCH", veri: { yayinda: yayin.checked } });
        gor.hidden = !yayin.checked;
        bildir(yayin.checked ? `“${u.ad}” mağazada yayında.` : `“${u.ad}” mağazadan kaldırıldı; ürün sayfası artık açılmaz.`, "success");
      } catch (h) { yayin.checked = !yayin.checked; bildir(h.message, "danger"); }
    };
    return el("tr", {},
      el("td", {}, el("a", { class: "d-flex align-items-center gap-3 urun-satir", href: `#urun/${u.id}` },
        yuklenenGorsel(u.kapak, "", "urun-kucuk"),
        el("div", {}, el("div", { class: "fw-bold" }, u.ad), el("div", { class: "small text-muted" }, `#${u.id}`)))),
      el("td", {}, tipRozeti(u.tip)),
      el("td", {}, fiyatHucresi(u)),
      el("td", {}, el("div", { class: "switch form-switch-custom switch-inline form-switch-success mb-0" }, yayin,
        el("label", { class: "switch-label", for: `yayin-${u.id}` }, ""))),
      el("td", { class: "small text-muted" }, u.guncelleme),
      el("td", { class: "text-end text-nowrap" },
        gor,
        el("a", { class: "btn btn-sm btn-outline-primary me-1", href: `#urun/${u.id}`, title: "Düzenle" }, "✏️ Düzenle"),
        el("button", { class: "btn btn-sm btn-outline-danger", title: "Sil", onclick: () => urunSil(u) }, "🗑")));
  }));
}

async function urunSil(u) {
  if (!(await onayla(`“${u.ad}” ürünü silinsin mi? Mağazadan da kalkar.`, "Sil"))) return false;
  try {
    await api(`/api/urunler/${u.id}`, { yontem: "DELETE" });
  } catch (h) { bildir(h.message, "danger"); return false; }
  bildir(`“${u.ad}” silindi.`, "success");
  if (Panel.sayfa === "urunler") urunleriYukle();
  return true;
}

$("#ul-ara").addEventListener("input", bekletip(urunleriYukle, 250));
$("#ul-tip").onchange = urunleriYukle;
$("#ul-sirala").onchange = urunleriYukle;
Panel.girince("urunler", urunleriYukle);

// ======================================================================
// ÜRÜN EKLE / DÜZENLE
// ======================================================================
const F = {
  anahtar: null,       // "yeni" ya da ürün id'si: aynı forma geri dönülünce (ör. odayı düzenleyip) bilgiler kaybolmasın
  id: null,            // düzenlenen ürün
  urun: null,          // sunucudaki hali
  gecici: null,        // yeni seçilen (kaydedilmemiş) resmin anahtarı
  resimUrl: null,      // form içindeki resim önizlemesi
  tip: null,
  otoTip: null,        // resmin oranından bulunan tip
  secili: null,        // vitrinde gösterilecek odalar (Set); null = o tipteki hepsi
  kapak: null,
  odalar: [],          // kullanıcının kayıtlı odaları
  kirli: false,
  kaydediliyor: false,
};

function kirlet() { F.kirli = true; Kaydedilmemis.var = true; }
function temizle() { F.kirli = false; Kaydedilmemis.var = false; }

// Tip seçici
for (const [t, ad] of Object.entries(TIP_ADLARI)) {
  $("#uf-tipler").append(
    el("input", { type: "radio", class: "btn-check", name: "uf-tip", id: `uf-tip-${t}`, value: t, autocomplete: "off",
      onchange: () => tipSec(t, true) }),
    el("label", { class: "btn btn-outline-primary tip-secenek", for: `uf-tip-${t}` },
      el("span", { class: `tip-sekil sekil-${t}` }), el("span", {}, ad)));
}

function tipSec(tip, elle = false) {
  if (F.tip === tip && !elle) return;
  F.tip = tip;
  for (const r of $$("#uf-tipler input")) r.checked = r.value === tip;
  if (elle) kirlet();
  // Tip değişince o tipteki bütün odalar seçili gelir
  F.secili = null;
  F.kapak = null;
  tipIpucu();
  odalariCiz();
}

function tipIpucu() {
  const ip = $("#uf-tip-ipucu");
  if (!F.otoTip) {
    ip.textContent = "Resim seçince oranından otomatik belirlenir. Yuvarlak tablo için kendiniz seçin.";
  } else if (F.otoTip === "kare") {
    ip.innerHTML = "";
    ip.append("Resmin oranına göre: ", el("b", {}, "Kare"), ". Yuvarlak tablo olarak satacaksanız ",
      el("b", {}, "◯ Yuvarlak"), "'ı seçin.");
  } else {
    ip.innerHTML = "";
    ip.append("Resmin oranına göre: ", el("b", {}, TIP_ADLARI[F.otoTip]), ". Gerekirse değiştirebilirsiniz.");
  }
}

function formSifirla() {
  Object.assign(F, { id: null, urun: null, gecici: null, tip: null, otoTip: null, secili: null, kapak: null });
  if (F.resimUrl?.startsWith("blob:")) URL.revokeObjectURL(F.resimUrl);
  F.resimUrl = null;
  $("#uf").classList.remove("was-validated");
  for (const g of $$("#uf .is-invalid")) g.classList.remove("is-invalid");
  $("#uf-ad").value = "";
  $("#uf-aciklama").value = "";
  $("#uf-fiyat").value = "";
  $("#uf-eski-fiyat").value = "";
  $("#uf-yayinda").checked = true;
  $("#uf-resim-hata").hidden = true;
  for (const r of $$("#uf-tipler input")) r.checked = false;
  resimGoster();
  tipIpucu();
  temizle();
}

function formHazirla(id) {
  F.anahtar = id ?? "yeni";
  formSifirla();
  $("#uf-baslik").textContent = id == null ? "Yeni Ürün" : "Ürünü Düzenle";
  $("#uf-mevcut-islemler").hidden = id == null;
}

async function formAc(id) {
  if (F.anahtar === (id ?? "yeni") && F.kirli) {
    // Aynı forma geri dönüldü (ör. "Odayı düzenle"den): bilgiler dursun, oda görselleri yenilensin
    await odaListesiniAl();
    return;
  }
  formHazirla(id);
  await odaListesiniAl(false);
  if (id != null) {
    try {
      F.urun = await api(`/api/urunler/${id}`);
    } catch (h) {
      bildir(h.message, "danger");
      Panel.git("#urunler");
      return;
    }
    const u = F.urun;
    F.id = u.id;
    $("#uf-ad").value = u.ad;
    $("#uf-aciklama").value = u.aciklama;
    $("#uf-fiyat").value = fiyatYaz(u.fiyat);
    $("#uf-eski-fiyat").value = fiyatYaz(u.eski_fiyat);
    $("#uf-yayinda").checked = u.yayinda;
    $("#uf-magazada").href = u.adres;
    $("#uf-magazada").hidden = !u.yayinda;   // kayıtlı hali mağazada değilse sayfası açılmaz
    F.resimUrl = u.gorseller.at(-1).o;
    $("#uf-resim-bilgi").textContent = `${u.genislik} × ${u.yukseklik} piksel`;
    resimGoster();
    F.otoTip = u.genislik / u.yukseklik > 1.1 ? "yatay" : u.genislik / u.yukseklik < 1 / 1.1 ? "dikey" : "kare";
    tipSec(u.tip);
    F.secili = u.odalar == null ? null : new Set(u.odalar);
    F.kapak = u.kapak_oda;
    $("#uf-baslik").textContent = `Ürünü Düzenle — #${u.id}`;
  }
  odalariCiz();
}

Panel.girince("urun-ekle", () => formAc(null));
Panel.girince("urun", param => formAc(+param));

async function odaListesiniAl(ciz = true) {
  try { F.odalar = await api("/api/panel/odalar"); } catch (h) { bildir(h.message, "danger"); }
  if (ciz) odalariCiz();
}

// Oda "Oda Sunumları"nda kaydedilince açık formdaki görseller yenilensin
Studyo.dinleyiciler.push(() => { if ($("#s-urun-form").hidden) return; odaListesiniAl(); });

// ---------- ürün resmi ----------
function resimGoster() {
  const img = $("#uf-resim");
  img.hidden = !F.resimUrl;
  $("#uf-resim-bos").hidden = !!F.resimUrl;
  if (F.resimUrl) img.src = F.resimUrl;
  else img.removeAttribute("src");
  vitrinCiz();
}

async function resimDosyasi(dosya) {
  if (!dosya) return;
  if (!dosya.type.startsWith("image/")) return bildir("Lütfen bir resim dosyası seçin.", "warning");
  $("#uf-resim-mesgul").hidden = false;
  try {
    const j = await yukle("/api/urun/gecici", [dosya], "dosya");
    resimAyarla(j, URL.createObjectURL(dosya));
    if (!$("#uf-ad").value.trim()) $("#uf-ad").value = dosya.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").slice(0, 120);
  } catch (h) {
    bildir(h.message, "danger");
  } finally {
    $("#uf-resim-mesgul").hidden = true;
  }
}

function resimAyarla(j, url) {
  if (F.resimUrl?.startsWith("blob:")) URL.revokeObjectURL(F.resimUrl);
  F.gecici = j.gecici;
  F.resimUrl = url;
  F.otoTip = j.tip;
  $("#uf-resim-hata").hidden = true;
  $("#uf-resim-bilgi").textContent = `${j.genislik} × ${j.yukseklik} piksel`;
  resimGoster();
  kirlet();
  // Kare resim yuvarlak tablo da olabilir: kullanıcı yuvarlağı seçtiyse koru
  if (!(F.tip === "yuvarlak" && j.tip === "kare")) tipSec(j.tip);
  else { tipIpucu(); odalariCiz(); }
}

const resimAlani = $("#uf-resim-alani");
resimAlani.onclick = () => $("#uf-dosya").click();
resimAlani.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#uf-dosya").click(); } };
$("#uf-resim-sec").onclick = () => $("#uf-dosya").click();
$("#uf-dosya").onchange = e => { const d = e.target.files[0]; e.target.value = ""; resimDosyasi(d); };
for (const olay of ["dragenter", "dragover"]) {
  resimAlani.addEventListener(olay, e => { e.preventDefault(); resimAlani.classList.add("surukleniyor"); });
}
for (const olay of ["dragleave", "drop"]) {
  resimAlani.addEventListener(olay, e => { e.preventDefault(); resimAlani.classList.remove("surukleniyor"); });
}
resimAlani.addEventListener("drop", e => resimDosyasi(e.dataTransfer.files[0]));

// "Tablo Ekle"deki "Bu Resimden Ürün Oluştur" düğmesi için (Urunler.eserdenYeni)
async function eserdenAl(eserId) {
  $("#uf-resim-mesgul").hidden = false;
  try {
    const j = await api(`/api/urun/gecici-eser/${eserId}`, { yontem: "POST" });
    resimAyarla(j, `/api/eser/${eserId}/o.jpg`);
    // Tablo Ekle'de tipi elle değiştirildiyse onu kullan
    if (j.tip_tahmini && j.tip_tahmini !== F.tip) tipSec(j.tip_tahmini);
    if (!$("#uf-ad").value.trim()) $("#uf-ad").value = j.ad.replace(/[_-]+/g, " ").slice(0, 120);
  } catch (h) {
    bildir(h.message, "danger");
  } finally {
    $("#uf-resim-mesgul").hidden = true;
  }
}

// ---------- odalardaki görünüm ----------
function tipOdalari() {
  return F.tip ? F.odalar.filter(o => o.tip === F.tip) : [];
}

function seciliMi(oda) {
  return F.secili == null || F.secili.has(oda.id);
}

function vitrinOdalari() {
  const liste = tipOdalari().filter(seciliMi);
  if (F.secili != null) {
    const sira = [...F.secili];
    liste.sort((a, b) => sira.indexOf(a.id) - sira.indexOf(b.id));
  }
  const kapak = liste.find(o => o.id === F.kapak);
  return kapak ? [kapak, ...liste.filter(o => o !== kapak)] : liste;
}

function odaGorselAdresi(oda) {
  if (F.gecici) return `/api/urun/onizleme/${F.gecici}/${oda.id}.jpg?v=${encodeURIComponent(oda.guncelleme)}`;
  if (F.id) return `/magaza/gorsel/${F.id}/${oda.id}-o.jpg?v=${encodeURIComponent(F.urun.guncelleme + "|" + oda.guncelleme)}`;
  return null;
}

function odalariCiz() {
  const kutu = $("#uf-odalar");
  const odalar = tipOdalari();
  const vitrin = vitrinOdalari();
  $("#uf-oda-sayac").textContent = F.tip ? `${vitrin.length} / ${odalar.length} oda` : "";
  $("#uf-oda-hepsi").disabled = $("#uf-oda-hicbiri").disabled = !odalar.length;
  if (!F.tip) {
    kutu.replaceChildren(el("div", { class: "col-12 bos-durum" }, el("div", { class: "fs-2" }, "📐"),
      el("p", { class: "mb-0" }, "Resim seçin ya da tablo tipini belirleyin; o tipteki odalarınız burada görünür.")));
    vitrinCiz();
    return;
  }
  if (!odalar.length) {
    kutu.replaceChildren(el("div", { class: "col-12 bos-durum" }, el("div", { class: "fs-2" }, "🏠"),
      el("p", {}, `${TIP_ADLARI[F.tip]} tipinde kayıtlı oda yok.`),
      el("a", { class: "btn btn-sm btn-primary", href: "#oda" }, "Oda Sunumu Hazırla")));
    vitrinCiz();
    return;
  }
  const kapakId = vitrin[0]?.id;
  kutu.replaceChildren(...odalar.map(o => {
    const secili = seciliMi(o);
    const adres = odaGorselAdresi(o);
    const gorsel = adres ? yuklenenGorsel(adres, o.ad)
      : el("div", { class: "gorsel-kutu bos-oda" }, el("img", { src: o.kucuk, alt: o.ad, loading: "lazy" }),
        el("span", { class: "bos-oda-yazi" }, "Resim seçince tablo burada görünür"));
    const kutucuk = el("input", { class: "form-check-input", type: "checkbox", id: `uf-oda-${o.id}`, checked: secili,
      onchange: () => odaSec(o.id, kutucuk.checked) });
    return el("div", { class: "col-sm-6 col-xxl-4" },
      el("div", { class: `oda-kart ${secili ? "secili" : ""} ${o.id === kapakId ? "kapak" : ""}` },
        el("div", { class: "oda-gorsel" }, gorsel,
          o.id === kapakId ? el("span", { class: "kapak-rozet" }, "⭐ Kapak") : null),
        el("div", { class: "oda-alt" },
          el("div", { class: "form-check mb-0" }, kutucuk,
            el("label", { class: "form-check-label", for: `uf-oda-${o.id}` }, `#${o.id} ${o.ad}`)),
          el("div", { class: "oda-dugmeler" },
            el("button", { type: "button", class: "btn btn-sm btn-outline-warning", title: "Vitrinde kapak görseli yap",
              disabled: !secili || o.id === kapakId, onclick: () => { F.kapak = o.id; kirlet(); odalariCiz(); } }, "⭐"),
            el("button", { type: "button", class: "btn btn-sm btn-outline-primary", title: "Bu odanın yansımasını düzenle",
              onclick: () => Studyo.odayiAc(o.id) }, "✏️ Odayı düzenle")))));
  }));
  vitrinCiz();
}

function odaSec(id, secili) {
  const odalar = tipOdalari();
  const kume = F.secili ?? new Set(odalar.map(o => o.id));
  if (secili) kume.add(id); else kume.delete(id);
  // Hepsi seçiliyse null: sonradan bu tipte eklenen odalar da vitrine kendiliğinden girsin
  F.secili = odalar.every(o => kume.has(o.id)) ? null : kume;
  if (!secili && F.kapak === id) F.kapak = null;
  kirlet();
  odalariCiz();
}

$("#uf-oda-hepsi").onclick = () => { F.secili = null; kirlet(); odalariCiz(); };
$("#uf-oda-hicbiri").onclick = () => { F.secili = new Set(); F.kapak = null; kirlet(); odalariCiz(); };

// ---------- vitrin kartı önizlemesi (mağazadaki kart gibi) ----------
function vitrinCiz() {
  const kutu = $("#uf-vitrin");
  const kapak = vitrinOdalari()[0];
  const adres = kapak ? odaGorselAdresi(kapak) : F.resimUrl;
  const fiyat = fiyatOku($("#uf-fiyat").value), eski = fiyatOku($("#uf-eski-fiyat").value);
  const indirim = fiyat > 0 && eski > fiyat ? Math.round((1 - fiyat / eski) * 100) : 0;
  kutu.replaceChildren(
    adres ? yuklenenGorsel(adres, "") : el("div", { class: "gorsel-kutu bos-vitrin" }, "Görsel yok"),
    el("div", { class: "vitrin-ad" }, $("#uf-ad").value.trim() || "Ürün adı"),
    el("div", { class: "vitrin-fiyat" },
      el("b", { class: indirim ? "text-success" : "" }, fiyat > 0 ? para(fiyat) : "₺—"),
      indirim ? el("del", { class: "text-muted ms-2" }, para(eski)) : null,
      indirim ? el("span", { class: "text-muted ms-1" }, `(%${indirim} indirim)`) : null),
    el("div", { class: "small text-muted" }, F.tip ? `${TIP_SIMGE[F.tip]} ${TIP_ADLARI[F.tip]} cam tablo` : ""));
}

for (const id of ["#uf-ad", "#uf-aciklama", "#uf-fiyat", "#uf-eski-fiyat"]) {
  $(id).addEventListener("input", () => {
    kirlet();
    $(id).classList.remove("is-invalid");
    if (id !== "#uf-aciklama") vitrinCiz();
  });
}
$("#uf-yayinda").onchange = kirlet;
for (const id of ["#uf-fiyat", "#uf-eski-fiyat"]) {
  $(id).addEventListener("blur", () => {
    const n = fiyatOku($(id).value);
    if (n != null && !Number.isNaN(n)) $(id).value = fiyatYaz(n);
  });
}

// ---------- kaydet / sil ----------
function formuDogrula() {
  let ilkHata = null;
  const hata = (girdi, kosul) => {
    girdi.classList.toggle("is-invalid", kosul);
    if (kosul && !ilkHata) ilkHata = girdi;
  };
  hata($("#uf-ad"), !$("#uf-ad").value.trim());
  const fiyat = fiyatOku($("#uf-fiyat").value), eski = fiyatOku($("#uf-eski-fiyat").value);
  hata($("#uf-fiyat"), !(fiyat > 0));
  hata($("#uf-eski-fiyat"), eski !== null && !(eski > fiyat));
  const resimYok = !F.gecici && !F.id;
  $("#uf-resim-hata").hidden = !resimYok;
  if (resimYok && !ilkHata) ilkHata = resimAlani;
  if (!F.tip && !ilkHata) {
    bildir("Tablo tipini seçin.", "warning");
    ilkHata = $("#uf-tipler");
  }
  if (ilkHata) {
    ilkHata.scrollIntoView({ behavior: "smooth", block: "center" });
    ilkHata.focus?.({ preventScroll: true });
    return null;
  }
  const vitrin = vitrinOdalari();
  return {
    ad: $("#uf-ad").value.trim(),
    aciklama: $("#uf-aciklama").value.trim(),
    fiyat, eski_fiyat: eski,
    tip: F.tip,
    yayinda: $("#uf-yayinda").checked,
    odalar: F.secili == null ? null : vitrin.map(o => o.id),
    kapak_oda: F.kapak && vitrin.some(o => o.id === F.kapak) ? F.kapak : null,
    gecici: F.gecici,
  };
}

$("#uf").addEventListener("submit", async e => {
  e.preventDefault();
  if (F.kaydediliyor) return;
  const veri = formuDogrula();
  if (!veri) return;
  if (veri.yayinda && F.secili != null && !F.secili.size) {
    if (!(await onayla("Hiçbir oda seçilmedi; mağazada sadece tablonun kendi resmi görünecek. Yine de kaydedilsin mi?",
      "Kaydet", "btn-primary"))) return;
  }
  const dugme = $("#uf-kaydet");
  F.kaydediliyor = true;
  dugme.disabled = true;
  dugme.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Kaydediliyor…';
  try {
    const j = F.id == null
      ? await api("/api/urunler", { yontem: "POST", veri })
      : await api(`/api/urunler/${F.id}`, { yontem: "PUT", veri });
    temizle();
    bildir(F.id == null ? `“${j.urun.ad}” mağazaya eklendi. Görseller hazırlanıyor…` : `“${j.urun.ad}” güncellendi.`, "success");
    // Kaydedilen ürünün düzenleme sayfasına geç (yeni üründe adres değişir)
    F.anahtar = null;
    Panel.git(`#urun/${j.urun.id}`);
  } catch (h) {
    bildir(h.message, "danger");
  } finally {
    F.kaydediliyor = false;
    dugme.disabled = false;
    dugme.textContent = "💾 Ürünü Kaydet";
  }
});

$("#uf-sil").onclick = async () => {
  if (F.urun && (await urunSil(F.urun))) {
    temizle();
    F.anahtar = null;
    Panel.git("#urunler");
  }
};

// "Vazgeç": formdaki değişiklikler bırakılır
$$('#s-urun-form a[href="#urunler"]').forEach(a => a.addEventListener("click", () => { temizle(); F.anahtar = null; }));

// ======================================================================
// DIŞARIYA AÇIK: "Tablo Ekle"den ürün oluşturma
// ======================================================================
const Urunler = {
  async eserdenYeni(eserId) {
    // Form önce hazırlanıp "kirli" işaretlenir: sayfa açılınca (formAc) sıfırlanmasın
    formHazirla(null);
    kirlet();
    Panel.git("#urun-ekle");
    await eserdenAl(eserId);
  },
};

sayfaGoster();
