/* Panelde ürün değerlendirmeleri: müşterilerin yıldız ve yorumları, satıcının cevabı, uygunsuz yorumu gizleme. */
"use strict";

const PanelDegerlendirme = {
  veri: null,
  filtre: "",

  async yukle() {
    try { this.veri = await api("/api/panel/degerlendirmeler"); } catch (h) { return bildir(h.message, "danger"); }
    const r = $("#menu-degerlendirme-rozet");
    r.hidden = !this.veri.cevapsiz;
    r.textContent = this.veri.cevapsiz;
    r.title = `${this.veri.cevapsiz} değerlendirme cevap bekliyor`;
    if (Panel.sayfa === "degerlendirmeler") this.ciz();
  },

  /** Mağazada yeni değerlendirme (WebSocket). */
  degisti() { this.yukle(); },

  ciz() {
    const { liste, sayi, ortalama } = this.veri;
    $("#dg-toplam").textContent = sayi;
    $("#dg-ortalama").textContent = sayi ? `Ortalama ★ ${ortalama.toLocaleString("tr-TR")}` : "";
    for (const b of $$("[data-dg-filtre]")) b.classList.toggle("active", b.dataset.dgFiltre === this.filtre);
    const uyan = { "": () => true, cevapsiz: d => !d.satici_cevap, dusuk: d => d.puan <= 2, gizli: d => !d.gorunur }[this.filtre];
    const gosterilen = liste.filter(uyan);
    $("#dg-bos").hidden = gosterilen.length > 0;
    $("#dg-panel-liste").replaceChildren(...gosterilen.map(d => this.kart(d)));
  },

  kart(d) {
    const cevap = el("textarea", { class: "form-control form-control-sm", rows: 2, maxlength: 600,
      placeholder: "Müşteriye cevabınız (ürün sayfasında herkese görünür)" });
    cevap.value = d.satici_cevap;
    const yildiz = "★".repeat(d.puan) + "☆".repeat(5 - d.puan);
    return el("div", { class: `dg-panel-kart ${d.gorunur ? "" : "gizli"}` },
      el("a", { href: d.urun.adres, target: "_blank", class: "dg-panel-urun" },
        el("img", { src: d.urun.kapak, alt: "", loading: "lazy" })),
      el("div", { class: "dg-panel-govde" },
        el("div", { class: "d-flex flex-wrap align-items-center gap-2" },
          el("span", { class: `dg-panel-yildiz puan-${d.puan}`, title: `${d.puan} yıldız` }, yildiz),
          el("b", {}, d.musteri_adi),
          el("span", { class: "text-muted small" }, `· ${tarihYaz(d.olusturma)}`),
          d.guncelleme !== d.olusturma ? el("span", { class: "text-muted small" }, "(düzenlendi)") : null,
          d.gorunur ? null : el("span", { class: "badge badge-light-danger" }, "Gizlendi"),
          el("a", { href: d.urun.adres, target: "_blank", class: "ms-auto small" }, `${kisalt(d.urun.ad, 48)} ↗`)),
        d.yorum ? el("p", { class: "dg-panel-yorum" }, d.yorum) : el("p", { class: "text-muted small fst-italic mb-2" }, "Yorum yazılmamış, sadece puan verildi."),
        cevap,
        el("div", { class: "d-flex gap-2 mt-2" },
          el("button", { class: "btn btn-sm btn-primary", onclick: async () => {
            try {
              await api(`/api/panel/degerlendirmeler/${d.id}/cevap`, { yontem: "POST", veri: { cevap: cevap.value } });
              bildir(cevap.value.trim() ? "Cevabınız yayınlandı." : "Cevap kaldırıldı.", "success");
              this.yukle();
            } catch (h) { bildir(h.message, "danger"); }
          } }, d.satici_cevap ? "Cevabı Güncelle" : "Cevapla"),
          el("button", { class: `btn btn-sm ${d.gorunur ? "btn-outline-danger" : "btn-outline-success"}`, onclick: async () => {
            if (d.gorunur && !(await onayla("Bu değerlendirme mağazada gizlensin mi? Puan ortalamasına da girmez.", "Gizle"))) return;
            try {
              await api(`/api/panel/degerlendirmeler/${d.id}`, { yontem: "PATCH", veri: { gorunur: !d.gorunur } });
              bildir(d.gorunur ? "Değerlendirme gizlendi." : "Değerlendirme yeniden görünür.", "success");
              this.yukle();
            } catch (h) { bildir(h.message, "danger"); }
          } }, d.gorunur ? "Gizle" : "Göster"))));
  },
};

for (const b of $$("[data-dg-filtre]")) {
  b.onclick = () => { PanelDegerlendirme.filtre = b.dataset.dgFiltre; PanelDegerlendirme.ciz(); };
}
Panel.girince("degerlendirmeler", () => PanelDegerlendirme.yukle());
PanelDegerlendirme.yukle();   // menüdeki "cevap bekliyor" rozeti
