/* Sayfanın kendiliğinden yenilenmesi: proje dosyaları kaydedilince (static/ altındaki bir dosya ya da sunucunun
   kendini yenilemesi) açık sayfa yenilenir. Yazı yazılırken, pencere açıkken ya da panelde kaydedilmemiş form
   varken bekler; iş bitince yeniler. Sunucuda STUDYO_OTOMATIK=0 ise hiç çalışmaz. */
(() => {
  "use strict";
  let ilk = null;
  let bekleyen = false;

  function yenilemekGuvenliMi() {
    // Panelde kaydedilmemiş ürün formu (ortak.js)
    if (typeof Kaydedilmemis !== "undefined" && Kaydedilmemis.var) return false;
    // Bir kutuya yazı yazılıyor
    const a = document.activeElement;
    if (a && (a.isContentEditable || (a.matches("input, textarea, select") && !["checkbox", "radio", "button", "submit"].includes(a.type)))) {
      return false;
    }
    // Açık pencere (onay, sipariş detayı ...) ya da açılır menü / sepet paneli
    if (document.querySelector(".modal.show, .offcanvas.show, .dropdown-menu.show")) return false;
    return true;
  }

  async function kontrol() {
    try {
      const y = await fetch("/api/magaza/canli-surum", { cache: "no-store" });
      const j = await y.json();
      if (j.surum === null) return;          // kapalı: bir daha sorma
      if (ilk === null) ilk = j.surum;
      else if (j.surum !== ilk) bekleyen = true;
    } catch {
      /* sunucu yeniden başlıyor: birazdan tekrar sorulur */
    }
    if (bekleyen && yenilemekGuvenliMi()) {
      location.reload();
      return;
    }
    setTimeout(kontrol, document.hidden ? 4000 : 1500);
  }

  kontrol();
})();
