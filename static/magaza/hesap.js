/* Mağazada üyenin sayfaları: Hesabım (siparişler ve takip, bilgiler, şifre) ve ödeme (sepetten sipariş).
   magaza.js'den sonra yüklenir; Sepet, Oturum, el, para, bildir oradan gelir. */
"use strict";

async function gonderJson(yol, yontem, veri) {
  const y = await fetch(yol, { method: yontem, headers: { "Content-Type": "application/json" }, body: JSON.stringify(veri ?? {}) });
  const j = await y.json().catch(() => ({}));
  if (y.status === 401) {
    location.href = `/giris?sonra=${encodeURIComponent(location.pathname)}`;
    throw new Error("Oturum kapalı.");
  }
  if (!y.ok) throw Object.assign(new Error(j.hata || "İşlem yapılamadı."), { veri: j, durum: y.status });
  return j;
}

const tarih = s => (s ? new Date(s.replace(" ", "T")).toLocaleString("tr-TR", { day: "numeric", month: "long",
  year: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const kisaTarih = s => (s ? new Date(s.replace(" ", "T")).toLocaleDateString("tr-TR", { day: "numeric", month: "short" }) : "");

// ======================================================================
// ÖDEME
// ======================================================================
const Odeme = {
  async basla() {
    const kisi = await Oturum.hazir;
    await Sepet.hazir;   // üyenin sepeti sunucudan gelsin
    if (!Sepet.liste.length) {
      $("#odeme-formu").hidden = true;
      $("#odeme-bos").hidden = false;
      return;
    }
    // Hesaptaki bilgilerle doldur
    const a = kisi.adres || {};
    $("#t-ad").value = a.ad || kisi.ad || "";
    $("#t-telefon").value = kisi.telefon || "";
    $("#t-il").value = a.il || "";
    $("#t-ilce").value = a.ilce || "";
    $("#t-adres").value = a.adres || "";
    this.ozet();
    for (const g of $$("#odeme-formu input, #odeme-formu textarea")) g.addEventListener("input", () => g.classList.remove("hatali"));
    $("#odeme-formu").addEventListener("submit", e => { e.preventDefault(); this.gonder(); });
    for (const r of $$("input[name=odeme]")) r.addEventListener("change", () => this.secimDegisti());
    this.kartAlanlari();
    // Bankanın 3D Secure sayfasından başarısız dönüş: /odeme?hata=...
    const hata = new URLSearchParams(location.search).get("hata");
    if (hata) {
      history.replaceState(null, "", location.pathname);
      bildir(hata, "danger");
    }
    try {
      const o = await getir("/api/magaza/odeme");
      $("#kart-secenegi").hidden = !o.kart;
      $("#kart-test").hidden = $("#kart-test-bilgi").hidden = !o.test;
      if (o.kart && hata) $("input[name=odeme][value=kart]").checked = true;   // tekrar denesin
    } catch { /* kartla ödeme gösterilmez */ }
    this.secimDegisti();
  },

  kartla() { return $("input[name=odeme]:checked")?.value === "kart"; },

  secimDegisti() {
    const kart = this.kartla();
    $("#kart-formu").hidden = !kart;
    const toplam = Sepet.liste.reduce((t, s) => t + s.fiyat * s.adet, 0);
    $("#siparis-ver").textContent = kart ? `${para(toplam)} Öde` : "Siparişi Onayla";
    if (kart && !$("#k-ad").value) $("#k-ad").value = $("#t-ad").value;
  },

  // Kart alanları: numara 4'erli gruplanır ve marka gösterilir, son kullanma AA/YY biçimine girer
  kartAlanlari() {
    const numara = $("#k-numara"), tarih = $("#k-tarih"), cvc = $("#k-cvc");
    numara.addEventListener("input", () => {
      const r = numara.value.replace(/\D/g, "").slice(0, 19);
      numara.value = r.replace(/(.{4})(?=.)/g, "$1 ");
      $("#k-marka").textContent = /^4/.test(r) ? "VISA" : /^(5[1-5]|2[2-7])/.test(r) ? "Mastercard"
        : /^9792/.test(r) ? "Troy" : /^3[47]/.test(r) ? "Amex" : "";
    });
    tarih.addEventListener("input", e => {
      let r = tarih.value.replace(/\D/g, "").slice(0, 4);
      if (r.length === 1 && r > "1") r = "0" + r;
      tarih.value = r.length > 2 || (r.length === 2 && e.inputType !== "deleteContentBackward") ? `${r.slice(0, 2)}/${r.slice(2)}` : r;
    });
    cvc.addEventListener("input", () => { cvc.value = cvc.value.replace(/\D/g, "").slice(0, 4); });
  },

  /** Kartı denetler; hatalıysa alanı işaretleyip null, değilse sunucuya gidecek kart. */
  kartBilgisi() {
    const isaretle = (s, mesaj) => { $(s).classList.add("hatali"); $(s).focus(); bildir(mesaj, "danger"); return null; };
    const ad = $("#k-ad").value.trim().replace(/\s+/g, " ");
    const numara = $("#k-numara").value.replace(/\D/g, "");
    const [ay, yil] = $("#k-tarih").value.split("/").map(x => parseInt(x, 10));
    const cvc = $("#k-cvc").value;
    if (ad.length < 3) return isaretle("#k-ad", "Kart üzerindeki adı yazın.");
    let toplam = 0;
    [...numara].reverse().forEach((c, i) => { let d = +c; if (i % 2) { d *= 2; if (d > 9) d -= 9; } toplam += d; });
    if (numara.length < 13 || toplam % 10) return isaretle("#k-numara", "Kart numarası geçersiz; lütfen kontrol edin.");
    const simdi = new Date();
    if (!(ay >= 1 && ay <= 12) || !(yil >= 0) || (2000 + yil) * 12 + ay < simdi.getFullYear() * 12 + simdi.getMonth() + 1) {
      return isaretle("#k-tarih", "Kartın son kullanma tarihi geçersiz.");
    }
    if (!/^\d{3,4}$/.test(cvc)) return isaretle("#k-cvc", "Kartın güvenlik kodunu (CVC) yazın.");
    return { ad, numara, ay, yil: 2000 + yil, cvc };
  },

  ozet() {
    $("#ozet-liste").replaceChildren(...Sepet.liste.map(s => el("div", { class: "ozet-satir" },
      el("img", { src: s.kapak, alt: "" }),
      el("div", {}, el("div", { class: "ad" }, s.ad), el("div", { class: "adet" }, `${s.adet} adet × ${para(s.fiyat)}`)),
      el("b", {}, para(s.fiyat * s.adet)))));
    $("#ozet-toplam").textContent = para(Sepet.liste.reduce((t, s) => t + s.fiyat * s.adet, 0));
  },

  async gonder() {
    const zorunlu = ["#t-ad", "#t-telefon", "#t-il", "#t-ilce", "#t-adres"];
    let ilk = null;
    for (const s of zorunlu) {
      const bos = !$(s).value.trim();
      $(s).classList.toggle("hatali", bos);
      if (bos && !ilk) ilk = $(s);
    }
    const tel = $("#t-telefon");
    if (!ilk && (tel.value.match(/\d/g) || []).length < 10) { tel.classList.add("hatali"); ilk = tel; }
    if (ilk) {
      ilk.focus();
      return bildir("Lütfen teslimat bilgilerini eksiksiz doldurun.", "danger");
    }
    const kart = this.kartla() ? this.kartBilgisi() : undefined;
    if (kart === null) return;
    const dugme = $("#siparis-ver");
    dugme.disabled = true;
    dugme.textContent = kart ? "Ödeme yapılıyor…" : "Sipariş veriliyor…";
    try {
      const j = await gonderJson("/api/hesap/siparisler", "POST", {
        kalemler: Sepet.liste.map(s => ({ id: s.id, adet: s.adet })),
        teslimat: { ad: $("#t-ad").value, telefon: tel.value, il: $("#t-il").value, ilce: $("#t-ilce").value,
          adres: $("#t-adres").value, not: $("#t-not").value },
        odeme: $("input[name=odeme]:checked").value,
        adresi_kaydet: $("#t-kaydet").checked,
        kart,
      });
      if (j.uc_boyut_html) {
        // 3D Secure: bankanın doğrulama sayfası (iyzico'nun gönderdiği form kendini gönderir). Ödeme alınınca
        // ürünler sepetten sunucuda çıkarılır; başarısızsa ödeme sayfasına sepetle geri dönülür.
        document.open();
        document.write(j.uc_boyut_html);
        document.close();
        return;
      }
      Sepet.liste = [];
      Sepet.kaydet();
      location.href = `/hesabim#siparis/${j.siparis.id}/yeni`;
    } catch (h) {
      if (h.durum === 409 && h.veri?.alinamayan) {
        // Satıştan kalkan ürünleri sepetten çıkar, özeti yenile
        Sepet.liste = Sepet.liste.filter(s => !h.veri.alinamayan.includes(s.id));
        Sepet.kaydet();
        if (!Sepet.liste.length) { $("#odeme-formu").hidden = true; $("#odeme-bos").hidden = false; }
        else this.ozet();
      }
      bildir(h.message, "danger");
      dugme.disabled = false;
      this.secimDegisti();
    }
  },
};

// ======================================================================
// HESABIM
// ======================================================================
const Hesabim = {
  bilgi: null,   // durum adları, aşamalar

  async basla() {
    const kisi = await Oturum.hazir;
    $("#hesap-selam").textContent = `Merhaba ${kisi.ad.split(" ")[0]}, siparişlerinizi takip edin ve bilgilerinizi güncelleyin.`;
    if (kisi.rol === "satici") { $("#hesap-panel").hidden = false; $("#hesap-panel").href = "/panel"; }
    this.bilgileriDoldur(kisi);
    $("#bilgi-formu").addEventListener("submit", e => { e.preventDefault(); this.bilgileriKaydet(); });
    $("#sifre-formu").addEventListener("submit", e => { e.preventDefault(); this.sifreDegistir(); });
    window.addEventListener("hashchange", () => this.goster());
    await this.siparisleriYukle();
    this.goster();
  },

  goster() {
    const [ad, id, yeni] = (location.hash.slice(1) || "siparisler").split("/");
    const sekme = ad === "siparis" ? "siparisler" : (["siparisler", "bilgiler", "sifre"].includes(ad) ? ad : "siparisler");
    for (const a of $$("#hesap-sekmeler a[data-sekme]")) a.classList.toggle("aktif", a.dataset.sekme === sekme);
    for (const b of $$("section[data-bolum]")) b.hidden = b.dataset.bolum !== sekme;
    if (ad === "siparis") {
      // Sipariş edilen ürünler sepetten sunucuda çıkarılır (kartla ödemede bankadan dönüş dahil)
      const kart = $(`#siparis-${id}`);
      $$(".siparis.vurgulu").forEach(k => k.classList.remove("vurgulu"));
      if (kart) {
        kart.classList.add("vurgulu");
        kart.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      $("#siparis-basari").replaceChildren(...(yeni === "yeni" && kart ? [el("div", { class: "basari-kutu" },
        `✓ Siparişiniz alındı! Sipariş numaranız ${kart.dataset.no}. Durumunu buradan adım adım takip edebilirsiniz.`)] : []));
      // "/yeni" adresten kalkar: sayfa yenilenince başarı mesajı tekrar görünmesin
      if (yeni === "yeni") history.replaceState(null, "", `${location.pathname}#siparis/${id}`);
    }
  },

  async siparisleriYukle() {
    let j;
    try { j = await getir("/api/hesap/siparisler"); } catch (h) { return bildir(h.message, "danger"); }
    this.bilgi = j;
    $("#siparis-listesi").replaceChildren(...(j.siparisler.length ? j.siparisler.map(s => this.siparisKarti(s))
      : [el("div", { class: "bos-sonuc" }, el("div", { class: "simge" }, "📦"), el("h3", {}, "Henüz siparişiniz yok"),
        el("p", {}, "Beğendiğiniz tabloları sepete ekleyip sipariş verebilirsiniz."),
        el("a", { class: "dugme dugme-acik", href: "/" }, "Alışverişe başla"))]));
  },

  /** İptal / iade talebinin durumu (siparişte talep varsa). */
  talepKutusu(s) {
    if (!s.talep) return null;
    const renk = { bekliyor: "uyari-kutu", onaylandi: "basari-kutu talep-onay", reddedildi: "uyari-kutu talep-red",
      tamamlandi: "basari-kutu", geri_cekildi: "uyari-kutu talep-soluk" }[s.talep_durum] || "uyari-kutu";
    const iade = s.talep === "iade";
    return el("div", { class: `talep-kutu ${renk}` },
      el("div", { class: "talep-baslik" }, el("b", {}, `↩️ ${s.talep_adi}`), el("span", { class: "talep-durum" }, s.talep_durum_adi)),
      el("div", { class: "small" }, `Sebep: ${s.talep_neden}`, s.talep_aciklama ? ` — ${s.talep_aciklama}` : ""),
      s.talep_cevap ? el("div", { class: "talep-cevap" },
        el("b", {}, s.talep_durum === "onaylandi" && iade ? "Satıcının iade talimatı: " : "Satıcının notu: "), s.talep_cevap) : null,
      s.talep_durum === "onaylandi" && iade ? el("div", { class: "small mt-1" },
        "Ürünü talimata göre geri gönderin; satıcı ürünü teslim alınca para iadeniz yapılır.") : null,
      s.talep_durum === "tamamlandi" && iade ? el("div", { class: "small mt-1" },
        s.odeme === "kart" ? "Ödemeniz kartınıza iade edildi (bankanıza göre hesabınıza yansıması birkaç gün sürebilir)."
          : `Ödemeniz ${s.talep_iban} IBAN'ına havaleyle iade edilir.`) : null,
      s.talep_durum === "bekliyor" ? el("button", { class: "dugme dugme-acik dugme-kucuk mt-2", type: "button",
        onclick: () => this.talebiGeriCek(s) }, "Talebi Geri Çek") : null);
  },

  /** Sipariş için müşterinin yapabileceği işlem düğmeleri. */
  islemler(s) {
    const d = [];
    if (s.iptal_edilebilir) d.push(el("button", { class: "dugme dugme-acik dugme-kucuk", type: "button",
      onclick: () => this.talepPenceresi(s, "iptal", true) }, "Siparişi İptal Et"));
    if (s.iptal_talep_edilebilir) d.push(el("button", { class: "dugme dugme-acik dugme-kucuk", type: "button",
      onclick: () => this.talepPenceresi(s, "iptal", false) }, "İptal Talebi Gönder"));
    if (s.iade_talep_edilebilir) d.push(el("button", { class: "dugme dugme-acik dugme-kucuk", type: "button",
      onclick: () => this.talepPenceresi(s, "iade", false) }, "İade Talebi Oluştur"),
    el("span", { class: "small text-muted" }, `İade için son gün: ${kisaTarih(s.iade_son)}`));
    if (s.durum === "kargoda") d.push(el("span", { class: "small text-muted" },
      "Siparişiniz kargoda; teslim aldıktan sonra iade talebi oluşturabilirsiniz."));
    return d.length ? el("div", { class: "siparis-islemler" }, ...d) : null;
  },

  /** İptal / iade formu (Bootstrap penceresi). hemen: hazırlanmamış sipariş doğrudan iptal edilir. */
  talepPenceresi(s, tur, hemen) {
    const nedenler = this.bilgi.talep_nedenleri[tur];
    const kartli = s.odeme === "kart";
    const neden = el("select", { class: "form-select", id: "tp-neden" }, el("option", { value: "" }, "Bir sebep seçin"),
      ...nedenler.map(n => el("option", { value: n }, n)));
    const aciklama = el("textarea", { class: "form-control", id: "tp-aciklama", rows: 3, maxlength: 500,
      placeholder: tur === "iade" ? "Örn. kutuyu açtığımda camın köşesi kırıktı" : "İsteğe bağlı" });
    const iban = el("input", { class: "form-control", id: "tp-iban", maxlength: 32, placeholder: "TR00 0000 0000 0000 0000 0000 00",
      autocomplete: "off", spellcheck: "false" });
    const baslik = hemen ? "Siparişi İptal Et" : tur === "iade" ? "İade Talebi" : "İptal Talebi";
    const bilgi = hemen ? (kartli ? "Siparişiniz hemen iptal edilir ve ödemeniz kartınıza iade edilir." : "Siparişiniz hemen iptal edilir.")
      : tur === "iptal" ? "Siparişiniz hazırlanıyor; talebiniz satıcıya iletilir. Onaylanırsa sipariş iptal edilir"
        + (kartli ? " ve ödemeniz kartınıza iade edilir." : ".")
        : `Teslimden sonraki ${this.bilgi.iade_suresi_gun} gün içinde iade talep edebilirsiniz. Satıcı onaylayınca ürünü nasıl geri göndereceğinizi yazar; ürün ulaşınca `
        + (kartli ? "ödemeniz kartınıza iade edilir." : "ödemeniz aşağıdaki IBAN'a havaleyle iade edilir.");
    const gonder = el("button", { type: "submit", class: "dugme dugme-koyu" }, hemen ? "İptal Et" : "Talebi Gönder");
    const form = el("form", { class: "modal-content" },
      el("div", { class: "modal-header" }, el("h5", { class: "modal-title" }, `${baslik} — ${s.no}`),
        el("button", { type: "button", class: "btn-close", "data-bs-dismiss": "modal", "aria-label": "Kapat" })),
      el("div", { class: "modal-body" },
        el("p", { class: "small" }, bilgi),
        el("div", { class: "form-alani" }, el("label", { for: "tp-neden" }, "Sebep"), neden),
        el("div", { class: "form-alani" }, el("label", { for: "tp-aciklama" }, "Açıklama"), aciklama),
        tur === "iade" && !kartli ? el("div", { class: "form-alani" }, el("label", { for: "tp-iban" }, "Para iadesi için IBAN"), iban) : null),
      el("div", { class: "modal-footer" },
        el("button", { type: "button", class: "dugme dugme-acik", "data-bs-dismiss": "modal" }, "Vazgeç"), gonder));
    const pencere = el("div", { class: "modal fade", tabindex: "-1" }, el("div", { class: "modal-dialog modal-dialog-centered" }, form));
    document.body.append(pencere);
    const bm = new bootstrap.Modal(pencere);
    pencere.addEventListener("hidden.bs.modal", () => { bm.dispose(); pencere.remove(); });
    form.addEventListener("submit", async e => {
      e.preventDefault();
      if (!neden.value) { neden.classList.add("hatali"); return bildir("Lütfen bir sebep seçin.", "danger"); }
      gonder.disabled = true;
      try {
        const yol = hemen ? `/api/hesap/siparisler/${s.id}/iptal` : `/api/hesap/siparisler/${s.id}/talep`;
        await gonderJson(yol, "POST", { tur, neden: neden.value, aciklama: aciklama.value, iban: iban.value });
        bm.hide();
        bildir(hemen ? "Siparişiniz iptal edildi." : "Talebiniz satıcıya iletildi.", "success");
        await this.siparisleriYukle();
        this.goster();
      } catch (h) {
        gonder.disabled = false;
        bildir(h.message, "danger");
      }
    });
    bm.show();
  },

  async talebiGeriCek(s) {
    if (!confirm(`${s.talep_adi} geri çekilsin mi?`)) return;
    try {
      await gonderJson(`/api/hesap/siparisler/${s.id}/talep/geri-cek`, "POST");
      bildir("Talebiniz geri çekildi.");
      await this.siparisleriYukle();
      this.goster();
    } catch (h) { bildir(h.message, "danger"); }
  },

  siparisKarti(s) {
    const { akis, durumlar } = this.bilgi;
    const iptal = s.durum === "iptal" || s.durum === "iade";
    // Her aşamanın gerçekleştiği zaman (geçmişten)
    const zamanlar = {};
    for (const g of s.gecmis) zamanlar[g.durum] ??= g.zaman;
    const sira = akis.indexOf(s.durum);
    const takip = iptal ? null : el("div", { class: "takip", "aria-label": "Sipariş durumu" }, ...akis.map((d, i) =>
      el("div", { class: `takip-adim ${i <= sira ? "tamam" : ""}` },
        el("span", { class: "nokta" }, i <= sira ? "✓" : String(i + 1)),
        durumlar[d], i <= sira && zamanlar[d] ? el("span", { class: "zaman" }, kisaTarih(zamanlar[d])) : null)));
    const t = s.teslimat;
    return el("article", { class: "siparis", id: `siparis-${s.id}`, "data-no": s.no },
      el("div", { class: "siparis-ust" },
        el("div", {}, el("span", { class: "etiket" }, "Sipariş no"), el("b", {}, s.no)),
        el("div", {}, el("span", { class: "etiket" }, "Tarih"), el("b", {}, tarih(s.olusturma))),
        el("div", {}, el("span", { class: "etiket" }, "Toplam"), el("b", {}, para(s.toplam))),
        el("div", {}, el("span", { class: "etiket" }, "Ödeme"), el("b", {}, s.odeme_adi)),
        el("span", { class: `durum-rozet durum-${s.durum}` }, s.durum_adi)),
      el("div", { class: "siparis-govde" },
        takip,
        s.durum === "iptal" ? el("div", { class: "uyari-kutu" }, "Bu sipariş iptal edildi.") : null,
        this.talepKutusu(s),
        s.durum === "kargoda" || (s.takip_no && s.durum === "teslim") ? el("div", { class: "kargo-bilgi" },
          "🚚 ", el("b", {}, s.kargo_firmasi), " — takip numarası: ", el("b", {}, s.takip_no)) : null,
        ...s.kalemler.map(k => el("div", { class: "siparis-kalem" },
          el("img", { src: k.gorsel, alt: "", loading: "lazy" }),
          el("div", {}, el("div", {}, k.ad), el("div", { class: "small text-muted" }, `${k.adet} adet × ${para(k.fiyat)}`),
            // Teslim alınan ürün değerlendirilebilir (ürün sayfasındaki değerlendirme bölümüne gider)
            s.durum === "teslim" ? el("a", { class: "degerlendir-baglanti", href: `/urun/${k.urun_id}#degerlendirmeler` },
              "★ Ürünü değerlendir") : null),
          el("b", {}, para(k.fiyat * k.adet)))),
        el("div", { class: "siparis-alt" },
          el("div", {}, el("b", {}, "Teslimat: "), `${t.ad}, ${t.ilce} / ${t.il}`),
          this.islemler(s)),
        el("details", { class: "mt-2" }, el("summary", { class: "small fw-bold" }, "Sipariş geçmişi"),
          el("ul", { class: "gecmis" }, ...s.gecmis.map(g => el("li", {},
            el("b", {}, durumlar[g.durum]), g.aciklama ? ` — ${g.aciklama}` : "",
            el("div", { class: "zaman" }, tarih(g.zaman))))))));
  },

  bilgileriDoldur(k) {
    $("#h-ad").value = k.ad;
    $("#h-kadi").value = k.kullanici_adi;
    $("#h-eposta").value = k.eposta || "";
    $("#h-eposta").required = k.rol === "musteri";   // satıcıda e-posta isteğe bağlı
    $("#h-telefon").value = k.telefon || "";
    const a = k.adres;
    $("#h-adres").replaceChildren(...(a ? [el("div", { class: "form-alani mb-0" }, el("label", {}, "Kayıtlı teslimat adresi"),
      el("div", { class: "uyari-kutu mb-0", style: "background:var(--m-zemin-2);border-color:var(--m-cizgi)" },
        `${a.ad} — ${a.adres}, ${a.ilce} / ${a.il}`,
        el("div", { class: "small text-muted mt-1" }, "Bir sonraki siparişinizde ödeme sayfasında değiştirebilirsiniz.")))] : []));
  },

  async bilgileriKaydet() {
    for (const g of ["#h-ad", "#h-kadi", "#h-eposta"]) $(g).classList.remove("hatali");
    if (!$("#h-ad").value.trim()) { $("#h-ad").classList.add("hatali"); return bildir("Ad soyad boş olamaz.", "danger"); }
    if (!/^[A-Za-z0-9._-]{3,30}$/.test($("#h-kadi").value.trim())) {
      $("#h-kadi").classList.add("hatali");
      return bildir("Kullanıcı adı 3-30 karakter olmalı: harf (Türkçe karakter olmadan), rakam, nokta, tire, alt çizgi.", "danger");
    }
    try {
      const k = await gonderJson("/api/hesap/bilgiler", "PUT", { ad: $("#h-ad").value, telefon: $("#h-telefon").value,
        kullanici_adi: $("#h-kadi").value.trim(), eposta: $("#h-eposta").value.trim() });
      Oturum.kisi = k;
      Oturum.ciz();
      this.bilgileriDoldur(k);
      bildir("Bilgileriniz kaydedildi.", "success");
    } catch (h) {
      // Alınmış kullanıcı adı / e-posta: ilgili alanı işaretle
      if (/kullanıcı adı/i.test(h.message)) $("#h-kadi").classList.add("hatali");
      if (/e-posta/i.test(h.message)) $("#h-eposta").classList.add("hatali");
      bildir(h.message, "danger");
    }
  },

  async sifreDegistir() {
    const eski = $("#s-eski").value, yeni = $("#s-yeni").value, yeni2 = $("#s-yeni2").value;
    if (!eski || !yeni) return bildir("Mevcut ve yeni şifreyi yazın.", "danger");
    if (yeni.length < 6) return bildir("Yeni şifre en az 6 karakter olmalı.", "danger");
    if (yeni !== yeni2) return bildir("Yeni şifreler birbirini tutmuyor.", "danger");
    try {
      await gonderJson("/api/sifre", "POST", { eski, yeni });
      $("#sifre-formu").reset();
      bildir("Şifreniz değiştirildi. Diğer cihazlardaki oturumlarınız kapatıldı.", "success");
    } catch (h) { bildir(h.message, "danger"); }
  },
};

if (document.body.dataset.sayfa === "odeme") Odeme.basla();
else if (document.body.dataset.sayfa === "hesap") Hesabim.basla();
