/* Mağaza ve giriş / kayıt sayfalarında koyu / açık tema. Seçim tarayıcıda saklanır; seçim yoksa cihazın ayarı kullanılır.
   <head>'de yüklenir: sayfa çizilmeden <html data-tema> konur, önce yanlış renk görünmez. Üst çubukta (.ust-ikonlar)
   ay / güneş düğmesi kendiliğinden eklenir. Bootstrap 5.3 bileşenleri (pencere, yan panel, açılır menü) data-bs-theme
   ile koyulaşır. */
(() => {
  const ANAHTAR = "magaza_tema";
  const sistemKoyu = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : null;
  const kayitli = () => { try { return localStorage.getItem(ANAHTAR); } catch { return null; } };
  const sistem = () => (sistemKoyu && sistemKoyu.matches ? "koyu" : "acik");

  function uygula(tema) {
    const h = document.documentElement;
    h.dataset.tema = tema;
    h.dataset.bsTheme = tema === "koyu" ? "dark" : "light";
    const d = document.getElementById("tema-dugme");
    if (d) {
      const sonraki = tema === "koyu" ? "Açık temaya geç" : "Koyu temaya geç";
      d.setAttribute("aria-label", sonraki);
      d.title = sonraki;
    }
  }
  /** Düğmeyle değişirken yumuşak geçiş: destekleyen tarayıcıda eski ve yeni sayfa arasında çapraz geçiş (View
      Transitions), desteklemeyende renkler CSS geçişiyle kayar (html.tema-gecisi, magaza.css). */
  function yumusakGecis(degistir) {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return degistir();
    if (document.startViewTransition) { document.startViewTransition(degistir); return; }
    const h = document.documentElement;
    h.classList.add("tema-gecisi");
    degistir();
    clearTimeout(yumusakGecis.zaman);
    yumusakGecis.zaman = setTimeout(() => h.classList.remove("tema-gecisi"), 450);
  }

  uygula(kayitli() || sistem());
  sistemKoyu?.addEventListener?.("change", () => { if (!kayitli()) yumusakGecis(() => uygula(sistem())); });

  function dugmeEkle() {
    const yer = document.querySelector(".ust-ikonlar");
    if (!yer || document.getElementById("tema-dugme")) return;
    const d = document.createElement("button");
    d.type = "button";
    d.id = "tema-dugme";
    d.className = "ikon-dugme tema-dugme";
    // Koyu temada güneş, açık temada ay görünür (magaza.css)
    d.innerHTML = '<svg class="tema-ay" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'
      + '<svg class="tema-gunes" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4.5"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
    d.addEventListener("click", () => {
      const yeni = document.documentElement.dataset.tema === "koyu" ? "acik" : "koyu";
      try { localStorage.setItem(ANAHTAR, yeni); } catch { /* gizli pencere: sadece bu sayfada */ }
      yumusakGecis(() => uygula(yeni));
    });
    const hesap = yer.querySelector("#hesap-alani");
    if (hesap) hesap.after(d); else yer.prepend(d);
    uygula(document.documentElement.dataset.tema);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", dugmeEkle);
  else dugmeEkle();
})();
