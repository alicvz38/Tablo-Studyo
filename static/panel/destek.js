/* Panelde canlı destek: ziyaretçi sohbetlerinin listesi, seçili sohbette mesajlaşma, sohbeti kapatma, verilen puanlar.
   Yeni sohbet / mesaj / yazıyor / kapanış haberleri panelin WebSocket bağlantısından gelir (studyo.js -> olay). */
"use strict";

const saat = z => z.slice(11, 16);
const gunSaat = z => {
  const bugun = new Date().toISOString().slice(0, 10);
  return z.slice(0, 10) === bugun ? saat(z) : `${z.slice(8, 10)}.${z.slice(5, 7)} ${saat(z)}`;
};

const CanliDestek = {
  liste: [],
  sayilar: null,
  sekme: "acik",
  secili: null,       // seçili sohbetin id'si
  sohbet: null,       // seçili sohbetin ayrıntısı (mesajlarla)
  yaziyorZam: null,
  sonYaziyor: 0,

  async listeyiYukle() {
    let j;
    try { j = await api(`/api/panel/destek?durum=${this.sekme}`); } catch (h) { return bildir(h.message, "danger"); }
    this.liste = j.sohbetler;
    this.sayilar = j.sayilar;
    this.rozet();
    if (Panel.sayfa === "destek") this.listeyiCiz();
  },
  listeyiBirazSonraYukle: null,

  rozet() {
    const n = this.sayilar.okunmamis;
    const r = $("#menu-destek-rozet");
    r.hidden = !n;
    r.textContent = n;
    r.title = `${n} okunmamış mesaj`;
    $("#dk-acik-sekme").textContent = `Açık (${this.sayilar.acik})`;
    $("#dk-puan").textContent = this.sayilar.oy_sayisi
      ? `Ortalama puan: ★ ${this.sayilar.ortalama_puan.toLocaleString("tr-TR")} (${this.sayilar.oy_sayisi} oy)` : "";
  },

  listeyiCiz() {
    const ara = $("#dk-ara").value.trim().toLocaleLowerCase("tr-TR");
    const liste = this.liste.filter(s => !ara || `${s.ad} ${s.eposta} ${s.son?.metin || ""}`.toLocaleLowerCase("tr-TR").includes(ara));
    $("#dk-liste").replaceChildren(...(liste.length ? liste.map(s => el("button", {
      type: "button", class: `dk-oge ${s.id === this.secili ? "secili" : ""} ${s.okunmamis && s.durum === "acik" ? "okunmamis" : ""}`,
      onclick: () => Panel.git(`#destek/${s.id}`) },
    el("span", { class: "dk-avatar" }, (s.ad || "?").charAt(0).toLocaleUpperCase("tr-TR"),
      s.durum === "acik" ? el("i", { class: "dk-cevrimici" }) : null),
    el("span", { class: "dk-oge-govde" },
      el("span", { class: "dk-oge-ust" }, el("b", {}, s.ad), el("span", { class: "dk-zaman" }, gunSaat(s.son?.zaman || s.olusturma))),
      el("span", { class: "dk-oge-alt" },
        el("span", { class: "dk-son" }, s.son ? `${s.son.gonderen === "satici" ? "Siz: " : ""}${s.son.metin}` : ""),
        s.okunmamis && s.durum === "acik" ? el("span", { class: "badge badge-danger" }, s.okunmamis) : null,
        s.durum === "kapali" && s.puan ? el("span", { class: "dk-puan-kucuk" }, `★${s.puan}`) : null))))
      : [el("div", { class: "text-muted small text-center py-4" }, this.sekme === "acik" ? "Açık sohbet yok." : "Kapanmış sohbet yok.")]));
  },

  async sec(id) {
    this.secili = id;
    this.listeyiCiz();
    if (!id) {
      $("#dk-bos").hidden = false;
      $("#dk-sohbet").hidden = true;
      return;
    }
    let s;
    try { s = await api(`/api/panel/destek/${id}`); } catch (h) { bildir(h.message, "danger"); return Panel.git("#destek"); }
    if (this.secili !== id) return;
    this.sohbet = s;
    // Kapanmış bir sohbete adresle gelindiyse doğru sekmeyi göster
    if ((s.durum === "acik") !== (this.sekme === "acik")) this.sekmeSec(s.durum, false);
    this.sohbetiCiz();
    this.listeyiYukle();   // okunmamış sayıları düşsün
  },

  sohbetiCiz() {
    const s = this.sohbet;
    $("#dk-bos").hidden = true;
    $("#dk-sohbet").hidden = false;
    const acik = s.durum === "acik";
    $("#dk-ust").replaceChildren(
      el("span", { class: "dk-avatar buyuk" }, (s.ad || "?").charAt(0).toLocaleUpperCase("tr-TR")),
      el("div", { class: "flex-fill" },
        el("div", { class: "fw-bold" }, s.ad, s.musteri_id ? el("span", { class: "badge badge-light-info ms-2" }, "Üye") : null),
        el("div", { class: "small text-muted" }, [s.eposta, `Başlangıç: ${gunSaat(s.olusturma)}`, s.sayfa ? `Sayfa: ${s.sayfa}` : ""]
          .filter(Boolean).join(" • "))),
      el("span", { class: `badge ${acik ? "badge-light-success" : "badge-light-secondary"}` }, acik ? "Açık" : "Kapandı"),
      acik ? el("button", { class: "btn btn-sm btn-outline-danger ms-2", type: "button", onclick: () => this.kapat() }, "Sohbeti Kapat")
        : el("button", { class: "btn btn-sm btn-outline-danger ms-2", type: "button", onclick: () => this.sil() }, "🗑 Sohbeti Sil"));
    $("#dk-mesajlar").replaceChildren(...s.mesajlar.map(m => this.balon(m)));
    $("#dk-yaziyor").hidden = true;
    $("#dk-alt").replaceChildren(acik ? this.yazmaAlani() : this.kapanisBilgisi());
    this.asagi();
  },

  balon(m) {
    if (m.gonderen === "sistem") return el("div", { class: "dk-sistem" }, m.metin, el("span", {}, ` · ${saat(m.zaman)}`));
    const biz = m.gonderen === "satici";
    return el("div", { class: `dk-balon ${biz ? "biz" : "onlar"}` },
      el("div", { class: "dk-kim" }, biz ? `${m.ad} (destek)` : m.ad),
      el("div", { class: "dk-metin" }, m.metin),
      el("div", { class: "dk-saat" }, saat(m.zaman)));
  },

  asagi() {
    const k = $("#dk-mesajlar");
    k.scrollTop = k.scrollHeight;
  },

  yazmaAlani() {
    const girdi = el("textarea", { class: "form-control", rows: 2, maxlength: 1000, placeholder: "Cevabınızı yazın… (Enter: gönder, Shift+Enter: yeni satır)" });
    const gonder = async () => {
      const metin = girdi.value.trim();
      if (!metin) return;
      girdi.value = "";
      try {
        await api(`/api/panel/destek/${this.secili}/mesaj`, { yontem: "POST", veri: { metin } });
      } catch (h) { girdi.value = metin; bildir(h.message, "danger"); }
      girdi.focus();
    };
    girdi.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); gonder(); } });
    girdi.addEventListener("input", () => {
      if (Date.now() - this.sonYaziyor > 2500) {
        this.sonYaziyor = Date.now();
        api(`/api/panel/destek/${this.secili}/yaziyor`, { yontem: "POST" }).catch(() => {});
      }
    });
    setTimeout(() => girdi.focus(), 50);
    return el("div", { class: "d-flex gap-2 align-items-end" }, girdi,
      el("button", { class: "btn btn-primary", type: "button", onclick: gonder }, "Gönder"));
  },

  kapanisBilgisi() {
    const s = this.sohbet;
    const neden = { musteri: "Ziyaretçi sohbeti bitirdi.", satici: "Sohbet panelden kapatıldı.",
      zaman_asimi: "10 dakika mesaj gelmediği için otomatik kapandı." }[s.kapanis] || "Sohbet kapandı.";
    return el("div", { class: "dk-kapanis" },
      el("div", {}, el("b", {}, "Sohbet kapandı. "), neden, s.kapanma ? ` (${gunSaat(s.kapanma)})` : ""),
      s.puan ? el("div", { class: "mt-1" }, el("span", { class: "dk-yildiz" }, "★".repeat(s.puan) + "☆".repeat(5 - s.puan)),
        ` ${s.puan}/5`, s.yorum ? el("div", { class: "dk-yorum" }, `“${s.yorum}”`) : null)
        : el("div", { class: "small text-muted mt-1" }, "Ziyaretçi henüz puan vermedi."));
  },

  async kapat() {
    if (!(await onayla(`${this.sohbet.ad} ile sohbet kapatılsın mı? Ziyaretçiden puan istenecek.`, "Sohbeti Kapat"))) return;
    try { await api(`/api/panel/destek/${this.secili}/kapat`, { yontem: "POST" }); } catch (h) { bildir(h.message, "danger"); }
  },

  async sil() {
    const s = this.sohbet;
    if (!(await onayla(`${s.ad} ile yapılan sohbet bütün mesajlarıyla silinsin mi? Bu işlem geri alınamaz.`, "Sil"))) return;
    try {
      await api(`/api/panel/destek/${s.id}`, { yontem: "DELETE" });
    } catch (h) { return bildir(h.message, "danger"); }
    bildir("Sohbet silindi.", "success");
    Panel.git("#destek");
  },

  sekmeSec(sekme, yukle = true) {
    this.sekme = sekme;
    for (const b of $$("[data-dk-sekme]")) b.classList.toggle("active", b.dataset.dkSekme === sekme);
    if (yukle) this.listeyiYukle();
  },

  /** WebSocket'ten gelen destek haberi. */
  olay(m) {
    this.listeyiBirazSonraYukle();
    const buSohbet = m.sohbet_id === this.secili && this.sohbet;
    if (m.olay === "mesaj") {
      if (buSohbet && !this.sohbet.mesajlar.some(x => x.id === m.mesaj.id)) {
        this.sohbet.mesajlar.push(m.mesaj);
        $("#dk-mesajlar").append(this.balon(m.mesaj));
        $("#dk-yaziyor").hidden = true;
        this.asagi();
        if (m.mesaj.gonderen === "musteri" && Panel.sayfa === "destek") api(`/api/panel/destek/${m.sohbet_id}`).catch(() => {});  // okundu
      }
      // Başka sayfadayken ya da başka sohbetteyken ziyaretçi yazdıysa haber ver
      if (m.mesaj.gonderen === "musteri" && !(buSohbet && Panel.sayfa === "destek")) {
        bildir(`💬 ${m.mesaj.ad}: ${kisalt(m.mesaj.metin, 80)}`, "info");
      }
    } else if (m.olay === "yaziyor" && buSohbet) {
      $("#dk-yaziyor").textContent = `${this.sohbet.ad} yazıyor…`;
      $("#dk-yaziyor").hidden = false;
      clearTimeout(this.yaziyorZam);
      this.yaziyorZam = setTimeout(() => { $("#dk-yaziyor").hidden = true; }, 4000);
    } else if ((m.olay === "kapandi" || m.olay === "oy") && (m.sohbet_id === this.secili || m.sohbet?.id === this.secili)) {
      this.sec(this.secili);
    } else if (m.olay === "silindi" && m.sohbet_id === this.secili) {
      // Başka bir satıcı açık olan sohbeti sildi
      this.sohbet = null;
      if (Panel.sayfa === "destek") Panel.git("#destek");
      else this.secili = null;
    }
  },
};
CanliDestek.listeyiBirazSonraYukle = bekletip(() => CanliDestek.listeyiYukle(), 300);

for (const b of $$("[data-dk-sekme]")) b.onclick = () => CanliDestek.sekmeSec(b.dataset.dkSekme);
$("#dk-ara").addEventListener("input", bekletip(() => CanliDestek.listeyiCiz(), 150));

Panel.girince("destek", param => {
  CanliDestek.listeyiYukle();
  CanliDestek.sec(param ? +param : null);
});

// Menüdeki okunmamış rozeti panel açılınca da görünsün
CanliDestek.listeyiYukle();
