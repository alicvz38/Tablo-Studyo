/* Panelde "Oda Sunumları" ve "Tablo Ekle" (önceki app.js).
   Bütün durum sunucuda tutulur; bu sayfa işlemleri WebSocket ile sunucuya gönderir, gelen durumu ekrana yansıtır.
   Böylece aynı hesapla açılan her cihaz aynı çalışma alanını canlı görür. */
"use strict";

const renkKodu = id => `hsl(${[...String(id)].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7)} 75% 58%)`;

// ======================================================================
// DURUM VE BAĞLANTI
// ======================================================================
let K = null;              // sunucudan gelen kontrol tanımları (kaydırıcılar, tipler)
const D = { oda: null, odalar: [], eserler: [], atamalar: {}, secim: null, sekme: "oda" };
let benimId = null;
let ws = null;
let odalarDinleyici = null;  // açık "Kayıtlı Odalar" penceresi oda listesi değişince yenilensin

const resimTipi = oda => (oda.tip === "yuvarlak" ? "kare" : oda.tip);
const tipEtiketi = tip => `${K.simgeler[tip]} ${K.tipler[tip]}`;

function baglantiYaz(metin, tur) {
  const b = $("#baglanti");
  b.textContent = metin;
  b.className = `badge badge-light-${tur}`;
}

function baglan() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => baglantiYaz("● Bağlı", "success");
  ws.onmessage = e => mesajAl(JSON.parse(e.data));
  ws.onclose = e => {
    baglantiYaz("● Bağlantı koptu, yeniden deneniyor…", "danger");
    if (e.code === 4401) { location.href = "/giris"; return; }
    setTimeout(baglan, 1500);
  };
}

function gonder(m) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(m));
}
const aksiyon = (ad, veri = {}) => gonder({ tip: "aksiyon", ad, ...veri });

function mesajAl(m) {
  switch (m.tip) {
    case "hos_geldin":
      benimId = m.id;
      hesapYaz(m.kullanici);
      if (!K) { K = m.kontroller; panelOlustur(); tipButonlariOlustur(); }
      Object.assign(D, m.durum);
      odaGuncelle();
      tabloGuncelle();
      m.goruntuler.forEach(goruntuGeldi);
      break;
    case "durum":
      D[m.bolum] = m.veri;
      if (m.bolum === "oda") { mesgul(false); odaGuncelle(); }
      else if (m.bolum === "sekme") takipEt(m.veri);
      else {
        tabloGuncelle();
        if (m.bolum === "odalar") {
          odalarDinleyici?.();
          Studyo.odalarDegisti();
        }
      }
      break;
    case "goruntu":
      goruntuGeldi(m);
      break;
    case "kullanicilar":
      kullanicilariYaz(m.liste);
      break;
    case "imlec":
      imlecGeldi(m);
      break;
    case "bildirim":
      bildir(m.metin, m.tur);
      break;
    case "siparis":   // mağazadan yeni sipariş / iptal: sipariş listesi ve menü rozeti yenilensin
      Satis.degisti();
      break;
    case "destek":    // canlı destek: yeni sohbet, mesaj, yazıyor, kapandı, oy
      CanliDestek.olay(m);
      break;
    case "degerlendirme":   // mağazada yeni ürün değerlendirmesi
      PanelDegerlendirme.degisti();
      break;
    case "hata":
      mesgul(false);
      bildir(m.metin, "danger");
      break;
    case "ilerleme":
      if (m.bitti) ilerlemeGizle();
      else ilerlemeGoster(m.deger, m.toplam, m.metin);
      break;
    case "indirme_hazir":
      if (m.istemci === benimId) {
        indir(m.url);
        if (m.hatalar.length) bildir("Bazı resimler hazırlanamadı:\n" + m.hatalar.join("\n"), "warning");
      }
      bildir(m.ozet, m.hatalar.length ? "warning" : "success");
      break;
  }
}

/** Aynı hesabın başka cihazı Oda Sunumları / Tablo Ekle arasında geçti: "Ekranı takip et" açıksa biz de geçelim
 *  (sadece bu iki sayfadaysak; ürün formundaki kişiyi sayfasından koparmayalım). */
function takipEt(sekme) {
  D.sekme = sekme;
  if ($("#takip").checked && ["oda", "tablo"].includes(Panel.sayfa) && Panel.sayfa !== sekme) Panel.git("#" + sekme);
}

// ---------- görüntüler: sunucu çizer, sürüm numarasıyla duyurur ----------
const sonSurum = { oda: 0, tablo: 0 };
let odaResim = null;
let tabloResimUrl = null;

async function goruntuGeldi(m) {
  sonSurum[m.gorunum] = m.v;
  if (m.gorunum === "tablo") $("#t-mesgul").hidden = true;
  if (m.yok) {
    if (m.gorunum === "oda") { odaResim = null; tuvalCiz(); }
    else tabloResimYok();
    return;
  }
  let blob;
  try {
    const y = await fetch(`/api/goruntu/${m.gorunum}?v=${m.v}`, { cache: "no-store" });
    if (!y.ok) return;
    blob = await y.blob();
  } catch { return; }
  if (m.v !== sonSurum[m.gorunum]) return;   // bu arada daha yenisi geldi
  if (m.gorunum === "oda") {
    const bmp = await createImageBitmap(blob);
    if (m.v !== sonSurum.oda) return;
    odaResim?.close?.();
    odaResim = bmp;
    tuvalCiz();
  } else {
    const url = URL.createObjectURL(blob);
    const img = $("#t-resim");
    img.onload = () => { if (tabloResimUrl && tabloResimUrl !== url) URL.revokeObjectURL(tabloResimUrl); tabloResimUrl = url; };
    img.src = url;
    img.hidden = false;
    $("#t-bos").hidden = true;
  }
}

function tabloResimYok() {
  $("#t-resim").hidden = true;
  $("#t-bos").hidden = false;
  $("#t-bos").textContent = D.odalar.length ? "Soldan bir oda seçin."
    : "Kayıtlı oda yok.\n'Oda Sunumları' sayfasından oda ve tablo yeri kaydedin.";
}

// ---------- bağlı cihazlar, durum çubuğu ----------
const cihazAdi = {};   // bağlantı id -> "Bu cihaz" / "Cihaz 2" ...
function kullanicilariYaz(liste) {
  let n = 1;
  for (const k of liste) cihazAdi[k.id] = k.id === benimId ? "Bu cihaz" : `Cihaz ${++n}`;
  // Tek cihazda göstermeye gerek yok; aynı hesapla başka cihaz bağlanınca görünür
  $("#kullanicilar").replaceChildren(...(liste.length < 2 ? [] : liste.map(k =>
    el("span", { class: "kisi" }, el("i", { style: `background:${renkKodu(k.id)}` }), cihazAdi[k.id]))));
  for (const id of Object.keys(imlecler)) if (!liste.some(k => k.id === id)) delete imlecler[id];
  tuvalCiz();
}

function durumYaz(id, bilgi) {
  const m = $(`#${id} .mesaj`);
  const tur = bilgi?.tur || "secondary";
  m.className = `mesaj text-${tur === "secondary" ? "muted" : tur}`;
  m.textContent = bilgi ? bilgi.metin : "";
  m.title = m.textContent;
}

let ilerlemeVar = false;
function ilerlemeGoster(deger, toplam, metin) {
  ilerlemeVar = true;
  const p = $("#t-durum .progress");
  p.hidden = false;
  $(".progress-bar", p).style.width = `${Math.round((100 * deger) / Math.max(1, toplam))}%`;
  if (metin) durumYaz("t-durum", { metin, tur: "warning" });
}
function ilerlemeGizle() {
  ilerlemeVar = false;
  $("#t-durum .progress").hidden = true;
  tabloGuncelle();
}

let mesgulZamanlayici = null;
function mesgul(metin) {
  const m = $("#o-mesgul");
  clearTimeout(mesgulZamanlayici);
  if (!metin) { m.hidden = true; return; }
  $(".metin", m).textContent = metin;
  m.hidden = false;
  mesgulZamanlayici = setTimeout(() => { m.hidden = true; }, 60000);
}

// ======================================================================
// ODA SUNUMLARI
// ======================================================================
const kontrol = {};   // anahtar -> { ayarla(deger) }
const yerel = {};     // anahtar -> bu tarayıcıda son değiştirildiği zaman
const yerelMi = k => Date.now() - (yerel[k] || 0) < 800;   // kendi değişikliğimiz sunucudan dönerken titremesin
const ayarGondericileri = {};

function ayarDegeri(k) {
  return D.oda.ayar[k];
}

function ayarGonder(anahtar, deger, bitti = false) {
  yerel[anahtar] = Date.now();
  D.oda.ayar[anahtar] = deger;
  const g = ayarGondericileri[anahtar] ||= kisitla(d => aksiyon("ayar", { anahtar, deger: d }), 70);
  if (bitti) { g.iptal(); aksiyon("ayar", { anahtar, deger, bitti: true }); }
  else g(deger);
}

function panelOlustur() {
  const p = $("#o-panel");
  const baslik = metin => p.append(el("h6", { class: "panel-baslik" }, metin));
  const aciklama = metin => p.append(el("p", { class: "aciklama" }, metin));

  const kaydirici = (anahtar, etiket, alt, ust, varsayilan) => {
    const deger = el("span", { class: "badge badge-light-primary deger" });
    const giris = el("input", { type: "range", class: "form-range", min: alt, max: ust, step: 1 });
    giris.addEventListener("input", () => { deger.textContent = giris.value; ayarGonder(anahtar, +giris.value); });
    giris.addEventListener("change", () => ayarGonder(anahtar, +giris.value, true));
    giris.addEventListener("dblclick", () => {
      giris.value = varsayilan; deger.textContent = varsayilan; ayarGonder(anahtar, varsayilan, true);
    });
    const kutu = el("div", { class: "kaydirici" }, el("div", { class: "satir" }, el("span", {}, etiket), deger), giris);
    p.append(kutu);
    kontrol[anahtar] = { kutu, ayarla(v) { giris.value = v; deger.textContent = v; } };
  };
  const anahtarKutusu = (anahtar, etiket) => {
    const g = el("input", { type: "checkbox", class: "switch-input", role: "switch", id: `sw-${anahtar}` });
    g.addEventListener("change", () => ayarGonder(anahtar, g.checked, true));
    p.append(el("div", { class: "switch form-switch-custom switch-inline form-switch-primary d-block mb-2" },
      g, el("label", { class: "switch-label", for: `sw-${anahtar}` }, etiket)));
    kontrol[anahtar] = { ayarla(v) { g.checked = !!v; } };
  };
  const secimGrubu = (secenekler, tikla, izgara = false) => {
    // Dar panelde dört seçenek tek satıra sığmaz: tablo tipi 2x2 ızgara, taraflar bitişik düğme grubu
    const grup = el("div", { class: izgara ? "secim-izgara" : "btn-group btn-group-sm w-100 secim-grubu", role: "group" });
    for (const [deger, ad] of secenekler) {
      grup.append(el("button", { type: "button", class: `btn btn-outline-primary${izgara ? " btn-sm" : ""}`, "data-deger": deger,
        onclick: () => tikla(deger) }, ad));
    }
    p.append(grup);
    return { ayarla(v) { $$("button", grup).forEach(b => b.classList.toggle("active", b.dataset.deger === v)); } };
  };

  // Tablo tipi (köşelerden tahmin edilir, elle seçilince sabitlenir)
  baslik("📐 Tablo Tipi");
  kontrol.tip = secimGrubu(Object.entries(K.tipler).map(([t, ad]) => [t, `${K.simgeler[t]} ${ad}`]), tip => {
    yerel.tip = Date.now(); D.oda.tip = tip; kontrol.tip.ayarla(tip); aksiyon("tip", { tablo_tipi: tip });
  }, true);
  aciklama("Köşelerin oranından tahmin edilir; elle seçince sabitlenir.");

  baslik("📷 Cam Yansıması");
  aciklama("Cam ayna gibi yansıtır: odanın kendisi, en belirgin olarak odadaki pencere, camda ters görünür. "
    + "Yansımanın ne kadar belirgin olacağını Yansıma Gücü ile ayarlayın.");
  anahtarKutusu("acik", "Cam yansıması açık");
  const [gAlt, gUst] = K.guc_araligi;
  kaydirici("guc", "Yansıma Gücü % (100 = otomatik)", gAlt, gUst, K.varsayilan.guc);

  p.append(el("div", { class: "alt-baslik" }, "Işığın Geldiği Taraf"));
  kontrol.taraf = secimGrubu(Object.entries(K.taraflar), taraf => {
    kontrol.taraf.ayarla(taraf); ayarGonder("taraf", taraf, true);
  });

  p.append(el("button", { type: "button", class: "btn btn-warning w-100 mt-3", onclick: () => aksiyon("cesit") },
    "🎲 Başka Bir Yansıma Dene"));
  aciklama("Aynanın açısını ve yansıyan odanın boyunu biraz değiştirir. Beğendiğiniz çeşit odayla kaydedilir.");
  p.append(el("div", { class: "alt-baslik" }, "Yansıma Ayarları"));
  aciklama("Yeni odada ve \"Otomatik Yansımaya Dön\" ile kayıtlı odalarınızdaki ayarlarınıza göre otomatik dolar "
    + "(ışığın tarafı ve tablonun çerçeveli / çerçevesiz olmasına göre). Kaydırıcıya çift tıklayınca sıfırlanır.");
  for (const [k, etiket, alt, ust, v] of K.yansima) kaydirici(k, etiket, alt, ust, v);

  p.append(el("button", { type: "button", class: "btn btn-outline-secondary w-100 mt-3", onclick: () => aksiyon("sifirla") },
    "⟲ Otomatik Yansımaya Dön"));

  baslik("🔷 Temperli Cam");
  aciklama("Camın kalınlığı, cilalı kenar, camın arkasından duvara düşen gölge, yüzey parlaklığı ve cam tonu. "
    + "Kaydırıcıya çift tıklayınca varsayılana döner.");
  anahtarKutusu("temperli", "Temperli cam görünümü açık");
  for (const [k, etiket, alt, ust, v] of K.temperli) kaydirici(k, etiket, alt, ust, v);
  p.append(el("button", { type: "button", class: "btn btn-outline-secondary w-100 mt-3",
    onclick: () => aksiyon("temperli_sifirla") }, "⟲ Temperli Ayarlarını Sıfırla"));
}

function odaGuncelle() {
  const o = D.oda;
  if (!o || !K) return;
  const dort = o.kose.length === 4;
  $("#o-bul").disabled = !o.oda_var;
  $("#o-duvar").disabled = !o.oda_var;
  $("#o-resim").disabled = !(o.oda_var && dort);
  $("#o-kaydet").disabled = !(o.oda_var && dort);
  $("#o-indir").disabled = !o.oda_var;
  $("#o-kaydet").textContent = o.aktif_oda_id != null ? `🗄️ 4. Odayı Kaydet (#${o.aktif_oda_id})` : "🗄️ 4. Odayı Kaydet";

  for (const [k, c] of Object.entries(kontrol)) {
    if (yerelMi(k)) continue;
    c.ayarla(k === "tip" ? o.tip : ayarDegeri(k));
  }
  if (!surukle && !yerelMi("kose")) kose = o.kose.map(p => [...p]);

  durumYaz("o-durum", o.bilgi || (o.oda_var ? null : { metin: "Lütfen başlamak için bir oda resmi yükleyin.", tur: "secondary" }));
  tuvalCiz();
}

// ---------- tuval: sunucunun çizdiği görüntü + üstünde köşe / kenar / yüzey tutamaçları ----------
const tuval = $("#o-tuval");
const ctx = tuval.getContext("2d");
let kose = [];                 // ekrandaki köşeler (oda resmi pikseli); sürüklerken yerel olarak değişir
let surukle = null;            // { parca, p0, baslangic }
let gorunum = { olcek: 1, x: 0, y: 0 };
const imlecler = {};           // diğer cihazların fare imleçleri: id -> { ad, x, y, t }

function tuvalBoyutla() {
  const r = $("#o-sahne").getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  tuval.width = Math.max(1, Math.round(r.width * dpr));
  tuval.height = Math.max(1, Math.round(r.height * dpr));
  tuvalCiz();
}
new ResizeObserver(tuvalBoyutla).observe($("#o-sahne"));

const ekrana = p => [gorunum.x + p[0] * gorunum.olcek, gorunum.y + p[1] * gorunum.olcek];

function tuvalCiz() {
  const o = D.oda;
  const dpr = window.devicePixelRatio || 1;
  const cw = tuval.width / dpr, ch = tuval.height / dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cw, ch);
  $("#o-bos").hidden = !!o?.oda_var;
  if (!o?.oda_var || !o.genislik) return;

  const W = o.genislik, H = o.yukseklik;
  const olcek = Math.min((cw - 8) / W, (ch - 8) / H);
  gorunum = { olcek, x: (cw - W * olcek) / 2, y: (ch - H * olcek) / 2 };

  // Yeni oda yüklenirken eski odanın görüntüsü yeni boyuta uzatılıp gösterilmesin
  if (odaResim && Math.abs(odaResim.width / odaResim.height - W / H) < 0.02) {
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(odaResim, gorunum.x, gorunum.y, W * olcek, H * olcek);
  }
  if (kose.length === 4) cerceveCiz(o);
  imlecleriCiz();
}

function cerceveCiz(o) {
  const pts = kose.map(ekrana);
  const yol = noktalar => { ctx.beginPath(); noktalar.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); };

  // Tablo yerleştirilmediyse ya da sürüklerken (görüntü birazdan güncellenir) tablo sınırı
  if (!o.tablo_var || surukle) {
    ctx.lineJoin = "round";
    if (o.tip === "yuvarlak") {
      ctx.strokeStyle = "rgba(0,255,0,.7)"; ctx.lineWidth = 1; yol(pts); ctx.stroke();
      ctx.lineWidth = 3; ctx.strokeStyle = "#00ff00"; yol(elipsNoktalari(kose).map(ekrana)); ctx.stroke();
    } else {
      ctx.strokeStyle = "#00ff00"; ctx.lineWidth = o.tablo_var ? 1.5 : 3;
      ctx.setLineDash(o.tablo_var ? [6, 4] : []); yol(pts); ctx.stroke(); ctx.setLineDash([]);
    }
  }

  // Kenar ortası (mavi kare), yüzey merkezi (artı), köşeler (kırmızı daire, numaralı)
  const r = 9;
  for (let i = 0; i < 4; i++) {
    const a = pts[i], b = pts[(i + 1) % 4];
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
    ctx.fillStyle = "#4361ee"; ctx.fillRect(mx - 6.5, my - 6.5, 13, 13);
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 1; ctx.strokeRect(mx - 6.5, my - 6.5, 13, 13);
  }
  const mx = pts.reduce((s, p) => s + p[0], 0) / 4, my = pts.reduce((s, p) => s + p[1], 0) / 4;
  for (const [renk, kal] of [["#fff", 5], ["#4361ee", 3]]) {
    ctx.strokeStyle = renk; ctx.lineWidth = kal; ctx.beginPath();
    ctx.moveTo(mx - 14, my); ctx.lineTo(mx + 14, my); ctx.moveTo(mx, my - 14); ctx.lineTo(mx, my + 14); ctx.stroke();
  }
  ctx.font = "bold 13px Nunito, system-ui, sans-serif";
  pts.forEach(([x, y], i) => {
    ctx.fillStyle = "#e7515a"; ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = "#ffff00"; ctx.strokeStyle = "#000"; ctx.lineWidth = 3;
    ctx.strokeText(String(i + 1), x - 4, y - r - 5); ctx.fillText(String(i + 1), x - 4, y - r - 5);
  });
}

function imlecleriCiz() {
  const simdi = Date.now();
  ctx.font = "12px Nunito, system-ui, sans-serif";
  for (const [id, m] of Object.entries(imlecler)) {
    if (simdi - m.t > 15000) { delete imlecler[id]; continue; }
    const [x, y] = ekrana([m.x, m.y]);
    const renk = renkKodu(id);
    ctx.fillStyle = renk; ctx.strokeStyle = "#000"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + 4, y + 15); ctx.lineTo(x + 7.5, y + 9.5); ctx.lineTo(x + 14, y + 8);
    ctx.closePath(); ctx.fill(); ctx.stroke();
    const ad = cihazAdi[id] || "Diğer cihaz";
    const w = ctx.measureText(ad).width + 10;
    ctx.fillRect(x + 12, y + 14, w, 18);
    ctx.fillStyle = "#000"; ctx.fillText(ad, x + 17, y + 27);
  }
}

function imlecGeldi(m) {
  if (m.x == null || m.gorunum !== "oda") delete imlecler[m.id];
  else imlecler[m.id] = { ad: m.ad, x: m.x, y: m.y, t: Date.now() };
  tuvalCiz();
}

// Köşeleri sırala: sol üst, sağ üst, sağ alt, sol alt (cekirdek.kose_noktalarini_sirala ile aynı)
function sirala(p) {
  const s = p.map(q => q[0] + q[1]), d = p.map(q => q[1] - q[0]);
  const enKucuk = a => a.indexOf(Math.min(...a)), enBuyuk = a => a.indexOf(Math.max(...a));
  return [p[enKucuk(s)], p[enKucuk(d)], p[enBuyuk(s)], p[enBuyuk(d)]];
}

// Birim kareden tablo dörtgenine perspektif dönüşümü (yuvarlak tablonun elipsini çizmek için)
function homografi(hedef) {
  const kaynak = [[0, 0], [1, 0], [1, 1], [0, 1]];
  const M = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = kaynak[i], [u, v] = hedef[i];
    M.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    M.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  }
  for (let c = 0; c < 8; c++) {   // Gauss eleme
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = c; k < 9; k++) M[r][k] -= f * M[c][k];
    }
  }
  return [...M.map((r, i) => r[8] / r[i]), 1];
}

function elipsNoktalari(k) {
  const h = homografi(sirala(k));
  const sonuc = [];
  for (let i = 0; i < 120; i++) {
    const t = (i / 120) * Math.PI * 2, x = 0.5 + 0.5 * Math.cos(t), y = 0.5 + 0.5 * Math.sin(t);
    const w = h[6] * x + h[7] * y + h[8];
    sonuc.push([(h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w]);
  }
  return sonuc;
}

function ekranToResim(e) {
  const r = tuval.getBoundingClientRect();
  return [(e.clientX - r.left - gorunum.x) / gorunum.olcek, (e.clientY - r.top - gorunum.y) / gorunum.olcek];
}

/** Fare noktasının altındaki parça: ["kose", i], ["kenar", i], ["yuzey"] ya da null. Öncelik köşe > kenar > yüzey. */
function tutulanParca(p) {
  if (kose.length !== 4) return null;
  const esik = 14 / gorunum.olcek;   // ekranda ~14 piksel
  const uzak = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  for (let i = 0; i < 4; i++) if (uzak(kose[i], p) < esik) return ["kose", i];
  for (let i = 0; i < 4; i++) {
    const a = kose[i], b = kose[(i + 1) % 4];
    const ab = [b[0] - a[0], b[1] - a[1]];
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / Math.max(ab[0] ** 2 + ab[1] ** 2, 1e-6)));
    if (uzak([a[0] + t * ab[0], a[1] + t * ab[1]], p) < esik * 0.8) return ["kenar", i];
  }
  // Nokta dörtgenin içinde mi (ışın yöntemi)
  let ic = false;
  for (let i = 0, j = 3; i < 4; j = i++) {
    const [xi, yi] = kose[i], [xj, yj] = kose[j];
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) ic = !ic;
  }
  return ic ? ["yuzey"] : null;
}

const koseGonder = kisitla(() => aksiyon("kose", { kose }), 60);
const imlecGonder = kisitla((x, y) => gonder({ tip: "imlec", gorunum: "oda", x, y }), 80);

tuval.addEventListener("contextmenu", e => e.preventDefault());
tuval.addEventListener("pointerdown", e => {
  if (!D.oda?.oda_var || kose.length !== 4) return;
  if (e.button !== 0) return;
  const p = ekranToResim(e);
  const parca = tutulanParca(p);
  if (!parca) return;
  surukle = { parca, p0: p, baslangic: kose.map(q => [...q]) };
  tuval.setPointerCapture(e.pointerId);
});

tuval.addEventListener("pointermove", e => {
  const p = ekranToResim(e);
  if (D.oda?.oda_var) imlecGonder(p[0], p[1]);
  if (surukle) {
    const [tur, i] = surukle.parca;
    const tasinacak = tur === "kose" ? [i] : tur === "kenar" ? [i, (i + 1) % 4] : [0, 1, 2, 3];
    // Taşınan noktaların hepsi resmin içinde kalacak şekilde hareketi sınırla
    const secili = tasinacak.map(j => surukle.baslangic[j]);
    const sinir = (d, eksen, ust) => Math.max(-Math.min(...secili.map(q => q[eksen])),
      Math.min(ust - Math.max(...secili.map(q => q[eksen])), d));
    const dx = sinir(p[0] - surukle.p0[0], 0, D.oda.genislik);
    const dy = sinir(p[1] - surukle.p0[1], 1, D.oda.yukseklik);
    kose = surukle.baslangic.map((q, j) => (tasinacak.includes(j) ? [q[0] + dx, q[1] + dy] : [...q]));
    yerel.kose = Date.now();
    tuvalCiz();
    koseGonder();
  } else {
    const parca = tutulanParca(p);
    tuval.style.cursor = { kose: "crosshair", kenar: "move", yuzey: "grab" }[parca?.[0]] || "default";
  }
});

function suruklemeBitti() {
  if (surukle) {
    surukle = null;
    koseGonder.iptal();
    yerel.kose = Date.now();
    aksiyon("kose", { kose, bitti: true });
    tuvalCiz();
  }
}
tuval.addEventListener("pointerup", suruklemeBitti);
tuval.addEventListener("pointercancel", suruklemeBitti);
tuval.addEventListener("pointerleave", () => { if (!surukle) { imlecGonder.iptal(); gonder({ tip: "imlec", x: null }); } });

// ---------- Oda Sunumları butonları ----------
$("#o-yukle").onclick = () => $("#o-yukle-dosya").click();
$("#o-yukle-dosya").onchange = async e => {
  const d = e.target.files[0];
  e.target.value = "";
  if (!d) return;
  mesgul("Oda resmi yükleniyor ve pencereler aranıyor…");
  try { await yukle("/api/oda/yukle", [d], "dosya"); }
  catch (h) { bildir(h.message, "danger"); }
  finally { mesgul(false); }
};
$("#o-bul").onclick = () => { mesgul("Tablo alanı aranıyor…"); aksiyon("otomatik_bul"); };
$("#o-duvar").onclick = () => { mesgul("Boş duvar aranıyor…"); aksiyon("bos_duvar"); };
$("#o-resim").onclick = () => $("#o-resim-dosya").click();
$("#o-resim-dosya").onchange = async e => {
  const d = e.target.files[0];
  e.target.value = "";
  if (!d) return;
  mesgul("Resim yerleştiriliyor…");
  try { await yukle("/api/oda/tablo-resmi", [d], "dosya"); }
  catch (h) { bildir(h.message, "danger"); }
  finally { mesgul(false); }
};
$("#o-kaydet").onclick = async () => {
  if (D.oda.aktif_oda_id != null) { aksiyon("kaydet"); return; }
  const ad = await sor("Odayı Kaydet", "Oda adı:", D.oda.ad || "oda");
  if (ad && ad.trim()) aksiyon("kaydet", { oda_adi: ad.trim() });
};
$("#o-indir").onclick = () => { bildir("Tam kalitede hazırlanıyor, indirme birazdan başlayacak…"); indir("/api/oda/sonuc"); };
$("#o-kayitlar").onclick = kayitliOdalarPenceresi;

function kayitliOdalarPenceresi() {
  let secili = null;
  const govde = el("tbody");
  const doldur = () => {
    if (secili != null && !D.odalar.some(o => o.id === secili)) secili = null;
    govde.replaceChildren(...(D.odalar.length ? D.odalar.map(o => el("tr", {
      class: o.id === secili ? "table-active secili" : "",
      onclick: () => { secili = o.id; doldur(); },
      ondblclick: () => ac(o.id),
    }, el("td", {}, el("img", { class: "oda-kucuk", src: `/api/panel/oda/${o.id}/kucuk.jpg?v=${encodeURIComponent(o.guncelleme)}`,
      loading: "lazy", alt: "" })),
    el("td", {}, `#${o.id}`), el("td", {}, tipEtiketi(o.tip)), el("td", {}, o.ad + (o.bos_duvar ? " (boş duvar)" : "")),
    el("td", { class: "text-muted small" }, o.guncelleme)))
      : [el("tr", {}, el("td", { colspan: 5, class: "text-muted" }, "Henüz kayıtlı oda yok."))]));
  };
  let kapat = null;
  const ac = id => { kapat(); Studyo.odayiAc(id); };
  doldur();
  odalarDinleyici = doldur;
  kapat = modalAc("📋 Kayıtlı Odalar", el("div", {},
    el("p", { class: "text-muted mb-2" }, "Açmak için çift tıklayın ya da seçip “Aç”a basın."),
    el("div", { class: "table-responsive kayitli-liste" }, el("table", { class: "table table-hover table-sm align-middle mb-0" },
      el("thead", {}, el("tr", {}, el("th", {}, ""), el("th", {}, "#"), el("th", {}, "Tip"), el("th", {}, "Oda"),
        el("th", {}, "Güncelleme"))), govde))), [
    { metin: "🗑 Sil", sinif: "btn-outline-danger me-auto", tikla: async () => {
      if (secili == null) return;
      const o = D.odalar.find(x => x.id === secili);
      if (await onayla(`Oda #${secili} ${o?.ad || ""} ve resmi silinsin mi? Bu odadaki ürün görselleri de mağazadan kalkar.`, "Sil")) {
        aksiyon("oda_sil", { id: secili });
      }
    } },
    { metin: "Kapat", tikla: k => k() },
    { metin: "📂 Aç", sinif: "btn-primary", ana: true, tikla: () => { if (secili != null) ac(secili); } },
  ], () => { odalarDinleyici = null; }, "lg");
}

// ======================================================================
// TABLO EKLE
// ======================================================================
let capa = null;          // Shift ile aralık seçimi için son tıklanan oda
let sagAnahtar = "";
let sonAktif = null;

function tipButonlariOlustur() {
  const k = $("#t-tipler");
  for (const tip of K.resim_tipleri) {
    k.append(el("button", { type: "button", class: "btn btn-outline-secondary", "data-tip": tip, disabled: true,
      onclick: () => aksiyon("eser_tipi", { resim_tipi: tip }) }, tipEtiketi(tip)));
  }
}

function eklenenSayisi(oda) {
  const ekli = new Set(D.atamalar[oda.id] || []);
  const rt = resimTipi(oda);
  return D.eserler.filter(e => ekli.has(e.id) && e.tip === rt).length;
}

function aktifEser() {
  const s = D.secim;
  if (!s) return null;
  const oda = D.odalar.find(o => o.id === s.secili[0]) || null;
  const sira = oda ? D.eserler.filter(e => e.tip === resimTipi(oda)) : [];
  const aktif = D.eserler.find(e => e.id === s.aktif_eser) || null;
  return { oda, sira, e: sira.length || !oda ? aktif : null };
}

function tabloGuncelle() {
  const s = D.secim;
  if (!s || !K) return;
  const secili = new Set(s.secili);

  // Oda listesi
  const govde = $("#t-odalar tbody");
  govde.replaceChildren(...D.odalar.map(o => {
    const n = eklenenSayisi(o);
    return el("tr", { class: `${secili.has(o.id) ? "table-active secili" : ""} ${n ? "ekli" : ""}`, "data-id": o.id },
      el("td", {}, `#${o.id}`), el("td", { title: K.tipler[o.tip] }, K.simgeler[o.tip]),
      el("td", {}, o.ad + (o.bos_duvar ? " (boş duvar)" : "")),
      el("td", {}, o.yansima != null ? `${o.yansima}%` : "—"), el("td", {}, n || ""));
  }));
  if (!D.odalar.length) govde.append(el("tr", {}, el("td", { colspan: 5, class: "text-muted" }, "Kayıtlı oda yok.")));

  const { oda, sira, e } = aktifEser();

  // Seçili resmin küçük önizlemesi
  const sr = $("#t-secili-resim");
  if (e) {
    if (sr.dataset.id !== String(e.id)) {
      sr.replaceChildren(el("img", { src: `/api/eser/${e.id}/o.jpg`, alt: e.ad, title: e.ad }));
      sr.dataset.id = e.id;
    }
  } else {
    sr.dataset.id = "";
    sr.replaceChildren(el("span", { class: "text-muted" }, D.eserler.length ? "Bu odanın tipinde resim yok" : "Tablo resmi yüklenmedi"));
  }
  for (const b of $("#t-tipler").children) {
    b.disabled = !e;
    b.classList.toggle("active", !!e && b.dataset.tip === e.tip);
  }

  // Sayaç ve odaya ekle / çıkar
  const ata = $("#t-ata"), sayac = $("#t-sayac");
  if (oda && e && sira.length) {
    const ekli = (D.atamalar[oda.id] || []).includes(e.id);
    sayac.textContent = `${sira.indexOf(e) + 1} / ${sira.length} ${K.tipler[resimTipi(oda)].toLowerCase()} resim  —  `
      + `${kisalt(e.ad, 60)}  —  ${ekli ? "✔ bu odaya eklendi" : "eklenmedi"}`;
    sayac.classList.toggle("ekli", ekli);
    const hedef = D.odalar.filter(o => secili.has(o.id) && resimTipi(o) === resimTipi(oda)).length;
    ata.disabled = false;
    ata.textContent = (ekli ? "➖ Odadan Çıkar" : "➕ Odaya Ekle") + (hedef > 1 ? ` (${hedef} oda)` : "");
    ata.className = `btn ${ekli ? "btn-danger" : "btn-success"}`;
  } else {
    sayac.textContent = "";
    ata.disabled = true;
    ata.textContent = "➕ Odaya Ekle";
    ata.className = "btn btn-success";
  }
  $("#t-onceki").disabled = $("#t-sonraki").disabled = sira.length < 2;
  $("#t-otomatik").disabled = !(D.eserler.length && D.odalar.length);
  $("#t-toplu").disabled = s.indirme_suruyor || !D.odalar.some(o => eklenenSayisi(o));
  $("#t-toplu").textContent = s.indirme_suruyor ? "⏳ Hazırlanıyor…" : "📦 Odalara Eklenen Resimleri İndir";
  $("#t-indir").disabled = !(oda && e && e.tip === resimTipi(oda));
  $("#t-urun").disabled = !e;
  $("#t-duzenle").disabled = !oda;
  $("#t-temizle").disabled = !D.eserler.length;
  if (!yerelMi("t-yansima")) $("#t-yansima").checked = s.yansima_acik;

  sagListeyiYaz(oda, sira, e);
  if (!ilerlemeVar) {
    durumYaz("t-durum", s.bilgi || {
      metin: D.odalar.length ? `${D.odalar.length} kayıtlı oda.` : "Kayıtlı oda yok. Önce 'Oda Sunumları' sayfasından oda ve koordinat kaydedin.",
      tur: "secondary",
    });
  }
  if (!oda) tabloResimYok();
}

/** Sağ kutu: önizlenen odanın tipindeki resimler; işaret kutusu = bu odaya ekli mi. */
function sagListeyiYaz(oda, sira, e) {
  const baslik = $("#t-sag-baslik"), liste = $("#t-sag-liste");
  if (!oda) {
    baslik.textContent = "Soldan bir oda seçin.";
    liste.replaceChildren();
    sagAnahtar = "";
    return;
  }
  const ekli = new Set(D.atamalar[oda.id] || []);
  const n = sira.filter(x => ekli.has(x.id)).length;
  baslik.replaceChildren(
    el("b", {}, `${tipEtiketi(resimTipi(oda))} resimler — #${oda.id} ${oda.ad}${oda.tip === "yuvarlak" ? " (yuvarlak oda)" : ""}`),
    el("span", { class: "text-muted" }, `${n} / ${sira.length} resim bu odaya eklendi`));

  const anahtar = `${oda.id}|${sira.map(x => x.id).join(",")}`;
  if (anahtar !== sagAnahtar) {
    sagAnahtar = anahtar;
    liste.replaceChildren(...(sira.length ? sira.map(x => el("div", { class: "sag-satir", "data-id": x.id },
      el("input", { type: "checkbox", class: "form-check-input m-0", title: "Bu odaya ekle" }),
      el("img", { src: `/api/eser/${x.id}/k.jpg`, loading: "lazy", alt: "" }),
      el("span", { class: "ad", title: x.ad }, x.ad)))
      : [el("p", { class: "text-muted" }, `${K.tipler[resimTipi(oda)]} tipinde yüklenmiş resim yok. Resim yükleyin ya da bir resmin tipini değiştirin.`)]));
  }
  for (const satir of liste.children) {
    const id = +satir.dataset.id;
    if (!id) continue;
    $("input", satir).checked = ekli.has(id);
    const aktif = !!e && e.id === id;
    satir.classList.toggle("aktif", aktif);
    if (aktif && sonAktif !== id) satir.scrollIntoView({ block: "nearest" });
  }
  sonAktif = e?.id ?? null;
}

function secimGonder(secili) {
  D.secim.secili = secili;   // hemen göster; sunucu da herkese yayınlar
  tabloGuncelle();
  $("#t-mesgul").hidden = false;
  aksiyon("sec", { secili });
}

$("#t-odalar tbody").addEventListener("click", e => {
  const tr = e.target.closest("tr[data-id]");
  if (!tr) return;
  const id = +tr.dataset.id;
  let secili = [...D.secim.secili];
  if (e.ctrlKey || e.metaKey) {
    secili = secili.includes(id) ? secili.filter(x => x !== id) : [...secili, id];
    if (!secili.length) secili = [id];
    // önizlenen oda tıklanan oda olsun
    if (secili.includes(id)) secili = [id, ...secili.filter(x => x !== id)];
    capa = id;
  } else if (e.shiftKey && capa != null) {
    const idler = D.odalar.map(o => o.id);
    const [a, b] = [idler.indexOf(capa), idler.indexOf(id)].sort((x, y) => x - y);
    secili = idler.slice(a, b + 1);
  } else {
    secili = [id];
    capa = id;
  }
  secimGonder(secili);
});
$("#t-odalar tbody").addEventListener("dblclick", e => {
  const tr = e.target.closest("tr[data-id]");
  if (tr) Studyo.odayiAc(+tr.dataset.id);
});

$("#t-sag-liste").addEventListener("click", e => {
  const satir = e.target.closest(".sag-satir");
  if (!satir) return;
  const id = +satir.dataset.id;
  if (e.target.matches("input")) aksiyon("kutu", { eser: id, ekle: e.target.checked });
  else { $("#t-mesgul").hidden = false; aksiyon("eser_goster", { id }); }
});

$("#t-yukle").onclick = () => $("#t-yukle-dosya").click();
$("#t-yukle-dosya").onchange = async e => {
  const dosyalar = [...e.target.files];
  e.target.value = "";
  if (!dosyalar.length) return;
  let eklenen = 0;
  const okunamayan = [];
  try {
    for (let i = 0; i < dosyalar.length; i += 8) {   // büyük seçimler parça parça yüklenir
      ilerlemeGoster(i, dosyalar.length, `Yükleniyor ${i}/${dosyalar.length}…`);
      const j = await yukle("/api/eserler", dosyalar.slice(i, i + 8), "dosyalar");
      eklenen += j.eklenen;
      okunamayan.push(...j.okunamayan);
    }
    bildir(`${eklenen} resim yüklendi${dosyalar.length - eklenen - okunamayan.length > 0 ? " (aynı olanlar atlandı)" : ""}.`, "success");
  } catch (h) {
    bildir(h.message, "danger");
  } finally {
    ilerlemeGizle();
  }
  if (okunamayan.length) bildir("Okunamayan resimler atlandı:\n" + okunamayan.join("\n"), "warning");
};
$("#t-temizle").onclick = async () => {
  if (await onayla("Yüklenen bütün tablo resimleri ve odalara eklemeler silinsin mi? (Mağazadaki ürünler etkilenmez.)", "Temizle")) {
    aksiyon("eserleri_temizle");
  }
};
$("#t-otomatik").onclick = () => aksiyon("otomatik_ata");
$("#t-indir").onclick = () => {
  const oda = D.secim.secili[0], eser = D.secim.aktif_eser;
  bildir("Tam kalitede hazırlanıyor, indirme birazdan başlayacak…");
  indir(`/api/tablo/indir?oda=${oda}&eser=${eser}`);
};
$("#t-urun").onclick = () => {
  const { e } = aktifEser() || {};
  if (e) Urunler.eserdenYeni(e.id);
};
$("#t-toplu").onclick = () => aksiyon("toplu_indir");
$("#t-yansima").onchange = e => { yerel["t-yansima"] = Date.now(); $("#t-mesgul").hidden = false; aksiyon("yansima", { acik: e.target.checked }); };
$("#t-yenile").onclick = () => aksiyon("listeyi_yenile");
$("#t-tumu").onclick = () => secimGonder(D.odalar.map(o => o.id));
$("#t-duzenle").onclick = () => { if (D.secim.secili.length) Studyo.odayiAc(D.secim.secili[0]); };
$("#t-onceki").onclick = () => { $("#t-mesgul").hidden = false; aksiyon("eser_degistir", { adim: -1 }); };
$("#t-sonraki").onclick = () => { $("#t-mesgul").hidden = false; aksiyon("eser_degistir", { adim: 1 }); };
$("#t-ata").onclick = () => aksiyon("ata_degistir");
$("#t-hepsi").onclick = () => aksiyon("hepsini", { ekle: true });
$("#t-hicbiri").onclick = () => aksiyon("hepsini", { ekle: false });

// Tablo Ekle klavyesi: ← / → resimler arası geçiş, Enter odaya ekle / çıkar
document.addEventListener("keydown", e => {
  if ($("#s-tablo").hidden || modalAcikMi()) return;
  if (e.target.matches("input[type=text], input[type=search], select, textarea")) return;
  if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    e.preventDefault();
    if (!$("#t-onceki").disabled) $("#t-mesgul").hidden = false;
    aksiyon("eser_degistir", { adim: e.key === "ArrowLeft" ? -1 : 1 });
  } else if (e.key === "Enter" && !e.target.matches("button, a")) {
    e.preventDefault();
    aksiyon("ata_degistir");
  }
});

// ======================================================================
// DIŞARIYA AÇIK: ürün formu buradan oda açar / oda listesini dinler
// ======================================================================
const Studyo = {
  dinleyiciler: [],
  /** Kayıtlı odayı "Oda Sunumları"nda düzenlemek için açar. */
  odayiAc(id) {
    Panel.git("#oda");
    mesgul("Oda açılıyor…");
    aksiyon("kayitli_ac", { id });
  },
  odalarDegisti() { for (const f of this.dinleyiciler) f(D.odalar); },
};

Panel.girince("oda", () => { requestAnimationFrame(tuvalBoyutla); aksiyon("sekme", { sekme: "oda" }); });
Panel.girince("tablo", () => aksiyon("sekme", { sekme: "tablo" }));

$("#takip").checked = depo.al("studyo_takip", "1") === "1";
$("#takip").onchange = e => { depo.yaz("studyo_takip", e.target.checked ? "1" : "0"); if (e.target.checked) takipEt(D.sekme); };

setInterval(tuvalCiz, 5000);   // uzun süre hareket etmeyen imleçler silinsin
baglan();
