/* Mağazada sağ alttaki canlı destek: sohbet başlatma, anlık mesajlaşma (WebSocket), sohbet bitince puanlama.
   Sohbet sunucuda tutulur ve tarayıcının çerezine bağlıdır; sayfalar arasında gezerken kaybolmaz.
   magaza.js'den sonra yüklenir (el, $, bildir oradan gelir). */
"use strict";

const Sohbet = {
  s: null,          // ziyaretçinin sohbeti (açık ya da en son kapanan)
  kisi: null,       // giriş yapmış üye (ad, e-posta)
  ws: null,
  acik: false,      // pencere açık mı
  okunmamis: 0,
  yaziyorZam: null,
  sonYaziyor: 0,
  puan: 0,

  async basla() {
    this.dugme = el("button", { class: "destek-dugme", type: "button", "aria-label": "Canlı destek", onclick: () => this.ac(!this.acik) },
      el("span", { class: "destek-simge", "aria-hidden": "true" }, "💬"), el("span", { class: "destek-yazi" }, "Canlı Destek"),
      this.rozet = el("span", { class: "destek-rozet", hidden: true }));
    this.kutu = el("section", { class: "destek-kutu", role: "dialog", "aria-label": "Canlı destek", hidden: true },
      el("header", { class: "destek-ust" },
        el("div", {}, el("b", {}, "Canlı Destek"), this.altYazi = el("div", { class: "destek-alt-yazi" }, "Sorularınızı yazın, destek ekibimiz yanıtlasın.")),
        this.bitirDugme = el("button", { class: "destek-bitir", type: "button", hidden: true, onclick: () => this.bitir() }, "Sohbeti bitir"),
        el("button", { class: "destek-kapat", type: "button", "aria-label": "Küçült", onclick: () => this.ac(false) }, "✕")),
      this.govde = el("div", { class: "destek-govde" }));
    document.body.append(this.kutu, this.dugme);

    try {
      const j = await getir("/api/magaza/destek");
      this.s = j.sohbet;
      this.kisi = j.kisi;
    } catch { /* bağlantı yoksa düğme yine çalışır, açılınca form gösterilir */ }
    this.ciz();
    if (this.s?.durum === "acik") this.baglan();
    let acikti = false;
    try { acikti = sessionStorage.getItem("destek_acik") === "1"; } catch { /* yok say */ }
    if (acikti) this.ac(true);
  },

  ac(acik) {
    this.acik = acik;
    this.kutu.hidden = !acik;
    this.dugme.classList.toggle("acik", acik);
    try { sessionStorage.setItem("destek_acik", acik ? "1" : "0"); } catch { /* yok say */ }
    if (acik) {
      this.okunmamis = 0;
      this.rozetYaz();
      this.asagi();
      setTimeout(() => this.govde.querySelector("textarea, input")?.focus(), 50);
    }
  },

  rozetYaz() {
    this.rozet.hidden = !this.okunmamis;
    this.rozet.textContent = this.okunmamis;
  },

  asagi() {
    const liste = this.govde.querySelector(".destek-mesajlar");
    if (liste) liste.scrollTop = liste.scrollHeight;
  },

  // ---------- görünüm ----------
  ciz() {
    const s = this.s;
    this.bitirDugme.hidden = s?.durum !== "acik";
    if (!s || this.yeniBaslat) return this.baslangicFormu();
    this.altYazi.textContent = s.durum === "acik"
      ? (s.satici_ad ? `${s.satici_ad} size yardımcı oluyor` : "Destek ekibimize bağlanıyorsunuz…")
      : "Sohbet sona erdi";
    const mesajlar = el("div", { class: "destek-mesajlar", "aria-live": "polite" }, ...s.mesajlar.map(m => this.balon(m)));
    this.yaziyorEl = el("div", { class: "destek-yaziyor", hidden: true });
    const alt = s.durum === "acik" ? this.yazmaAlani() : this.oylama();
    this.govde.replaceChildren(mesajlar, this.yaziyorEl, alt);
    this.asagi();
  },

  balon(m) {
    if (m.gonderen === "sistem") return el("div", { class: "destek-sistem" }, m.metin);
    const benim = m.gonderen === "musteri";
    return el("div", { class: `destek-balon ${benim ? "benim" : "onlarin"}` },
      benim ? null : el("div", { class: "destek-kim" }, `${m.ad} · Tablo Stüdyo`),
      el("div", { class: "destek-metin" }, m.metin),
      el("div", { class: "destek-saat" }, m.zaman.slice(11, 16)));
  },

  baslangicFormu() {
    this.altYazi.textContent = "Sorularınızı yazın, destek ekibimiz yanıtlasın.";
    this.bitirDugme.hidden = true;
    const ad = el("input", { class: "destek-girdi", maxlength: 60, autocomplete: "name", placeholder: "Adınız", value: this.kisi?.ad || "" });
    const eposta = el("input", { class: "destek-girdi", type: "email", maxlength: 100, autocomplete: "email",
      placeholder: "E-posta (isteğe bağlı)", value: this.kisi?.eposta || "" });
    const metin = el("textarea", { class: "destek-girdi", rows: 4, maxlength: 1000, placeholder: "Size nasıl yardımcı olabiliriz?" });
    const gonder = el("button", { class: "dugme dugme-koyu dugme-tam", type: "submit" }, "Sohbeti Başlat");
    const form = el("form", { class: "destek-form" },
      el("p", { class: "destek-hosgeldin" }, "Merhaba! 👋 Tablolar, sipariş ya da kargo hakkında sorunuzu yazın."),
      this.kisi ? null : ad, this.kisi ? null : eposta, metin, gonder);
    form.onsubmit = async e => {
      e.preventDefault();
      if (!this.kisi && !ad.value.trim()) { ad.focus(); return bildir("Lütfen adınızı yazın.", "danger"); }
      if (!metin.value.trim()) { metin.focus(); return bildir("Lütfen mesajınızı yazın.", "danger"); }
      gonder.disabled = true;
      try {
        const y = await fetch("/api/magaza/destek", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ad: ad.value, eposta: eposta.value, metin: metin.value, sayfa: location.pathname }) });
        const j = await y.json();
        if (!y.ok) throw new Error(j.hata || "Sohbet başlatılamadı.");
        this.s = j.sohbet;
        this.yeniBaslat = false;
        this.ciz();
        this.baglan();
      } catch (h) {
        bildir(h.message, "danger");
        gonder.disabled = false;
      }
    };
    metin.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
    this.govde.replaceChildren(form);
  },

  yazmaAlani() {
    const girdi = el("textarea", { class: "destek-girdi", rows: 1, maxlength: 1000, placeholder: "Mesajınızı yazın…", "aria-label": "Mesaj" });
    const gonder = () => {
      const metin = girdi.value.trim();
      if (!metin) return;
      if (this.ws?.readyState !== WebSocket.OPEN) return bildir("Bağlantı kuruluyor, lütfen birkaç saniye sonra tekrar deneyin.", "danger");
      this.ws.send(JSON.stringify({ tip: "mesaj", metin }));
      girdi.value = "";
      girdi.style.height = "";
    };
    girdi.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); gonder(); } });
    girdi.addEventListener("input", () => {
      girdi.style.height = "";
      girdi.style.height = Math.min(110, girdi.scrollHeight) + "px";
      if (Date.now() - this.sonYaziyor > 2500 && this.ws?.readyState === WebSocket.OPEN) {
        this.sonYaziyor = Date.now();
        this.ws.send(JSON.stringify({ tip: "yaziyor" }));
      }
    });
    return el("div", { class: "destek-yaz" }, girdi,
      el("button", { class: "destek-gonder", type: "button", "aria-label": "Gönder", onclick: gonder }, "➤"));
  },

  oylama() {
    const s = this.s;
    if (s.puan != null) {
      return el("div", { class: "destek-oy" },
        el("div", { class: "destek-oy-baslik" }, "Değerlendirmeniz için teşekkürler!"),
        el("div", { class: "destek-yildizlar dolu", "aria-label": `${s.puan} / 5` }, "★".repeat(s.puan) + "☆".repeat(5 - s.puan)),
        el("button", { class: "dugme dugme-acik dugme-tam mt-2", type: "button",
          onclick: () => { this.yeniBaslat = true; this.ciz(); } }, "Yeni Sohbet Başlat"));
    }
    this.puan = 0;
    const yildizlar = el("div", { class: "destek-yildizlar", role: "radiogroup", "aria-label": "Puan" });
    const yaz = n => [...yildizlar.children].forEach((y, i) => {
      y.classList.toggle("secili", i < n);
      y.setAttribute("aria-checked", String(i + 1 === this.puan));
    });
    for (let i = 1; i <= 5; i++) {
      yildizlar.append(el("button", { type: "button", role: "radio", "aria-label": `${i} yıldız`,
        onclick: () => { this.puan = i; yaz(i); }, onmouseenter: () => yaz(i), onmouseleave: () => yaz(this.puan) }, "★"));
    }
    const yorum = el("textarea", { class: "destek-girdi", rows: 2, maxlength: 500, placeholder: "Eklemek istedikleriniz (isteğe bağlı)" });
    return el("div", { class: "destek-oy" },
      el("div", { class: "destek-oy-baslik" }, "Destek deneyiminizi puanlayın"),
      yildizlar, yorum,
      el("button", { class: "dugme dugme-koyu dugme-tam", type: "button", onclick: async () => {
        if (!this.puan) return bildir("Lütfen 1-5 arası bir puan seçin.", "danger");
        try {
          const y = await fetch("/api/magaza/destek/oy", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ puan: this.puan, yorum: yorum.value }) });
          const j = await y.json();
          if (!y.ok) throw new Error(j.hata || "Gönderilemedi.");
          this.s = j.sohbet;
          this.ciz();
        } catch (h) { bildir(h.message, "danger"); }
      } }, "Gönder"),
      el("button", { class: "destek-atla", type: "button", onclick: () => { this.yeniBaslat = true; this.ciz(); } },
        "Puanlamadan yeni sohbet başlat"));
  },

  // ---------- bağlantı ----------
  baglan() {
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/destek`);
    this.ws = ws;
    ws.onmessage = e => this.mesajAl(JSON.parse(e.data));
    ws.onclose = e => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (e.code !== 4404 && this.s?.durum === "acik") setTimeout(() => this.baglan(), 2000);
    };
  },

  mesajAl(m) {
    if (m.tip === "mesaj") {
      if (this.s.mesajlar.some(x => x.id === m.mesaj.id)) return;   // birden fazla sekmede aynı mesaj
      this.s.mesajlar.push(m.mesaj);
      if (m.mesaj.gonderen === "satici") {
        this.s.satici_ad ||= m.mesaj.ad;
        this.altYazi.textContent = `${this.s.satici_ad} size yardımcı oluyor`;
        if (!this.acik) { this.okunmamis++; this.rozetYaz(); }
      }
      this.yaziyorEl.hidden = true;
      this.govde.querySelector(".destek-mesajlar").append(this.balon(m.mesaj));
      this.asagi();
    } else if (m.tip === "yaziyor") {
      this.yaziyorEl.textContent = `${m.ad} yazıyor…`;
      this.yaziyorEl.hidden = false;
      clearTimeout(this.yaziyorZam);
      this.yaziyorZam = setTimeout(() => { this.yaziyorEl.hidden = true; }, 4000);
    } else if (m.tip === "kapandi") {
      if (this.s.durum === "kapali") return;   // bu sekme zaten kapattı
      this.s.durum = "kapali";
      this.s.kapanis = m.neden;
      if (!this.s.mesajlar.some(x => x.id === m.mesaj.id)) this.s.mesajlar.push(m.mesaj);
      this.ws?.close();
      this.ciz();
      if (!this.acik) { this.okunmamis++; this.rozetYaz(); }
      if (m.neden === "zaman_asimi") bildir("10 dakikadır mesaj gelmediği için canlı destek sohbeti kapatıldı.");
    } else if (m.tip === "hata") {
      bildir(m.metin, "danger");
    }
  },

  async bitir() {
    if (!confirm("Sohbet sonlandırılsın mı?")) return;
    try {
      const y = await fetch("/api/magaza/destek/kapat", { method: "POST" });
      const j = await y.json();
      if (!y.ok) throw new Error(j.hata || "Sohbet sonlandırılamadı.");
      this.s = j.sohbet;
      const ws = this.ws;
      this.ws = null;
      ws?.close();
      this.ciz();
    } catch (h) { bildir(h.message, "danger"); }
  },
};

Sohbet.basla();
