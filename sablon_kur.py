"""
Panel şablonunu (Equation) kurar.

Panel, ücretli Equation (ThemeForest) Bootstrap 5 şablonunu kullanır. Şablonun lisansı
yeniden dağıtıma izin vermediği için dosyaları bu depoda yoktur. Satın aldığınız kopyadan
kurmak için:

    python sablon_kur.py "C:/yol/equation-v2.2.0"

Betik yalnızca panelin kullandığı dosyaları static/panel/sablon/ altına kopyalar ve
loader.js içinde panel adını ve logo yollarını Tablo Stüdyo'ya göre ayarlar.
"""
import shutil
import sys
from pathlib import Path

HEDEF = Path(__file__).resolve().parent / "static" / "panel" / "sablon"

DOSYALAR = [
    "layouts/vertical-dark-menu/app.js",
    "layouts/vertical-dark-menu/css/dark/loader.css",
    "layouts/vertical-dark-menu/css/dark/plugins.css",
    "layouts/vertical-dark-menu/css/dark/structure-mod.css",
    "layouts/vertical-dark-menu/css/dark/structure.css",
    "layouts/vertical-dark-menu/css/light/loader.css",
    "layouts/vertical-dark-menu/css/light/plugins.css",
    "layouts/vertical-dark-menu/css/light/structure-mod.css",
    "layouts/vertical-dark-menu/css/light/structure.css",
    "layouts/vertical-dark-menu/loader.js",
    "src/assets/css/dark/apps/blog-create.css",
    "src/assets/css/dark/apps/blog-post.css",
    "src/assets/css/dark/apps/chat.css",
    "src/assets/css/dark/apps/contacts.css",
    "src/assets/css/dark/apps/ecommerce-create.css",
    "src/assets/css/dark/apps/ecommerce-details.css",
    "src/assets/css/dark/apps/invoice-add.css",
    "src/assets/css/dark/apps/invoice-edit.css",
    "src/assets/css/dark/apps/invoice-list.css",
    "src/assets/css/dark/apps/invoice-preview.css",
    "src/assets/css/dark/apps/mailbox.css",
    "src/assets/css/dark/apps/notes.css",
    "src/assets/css/dark/apps/scrumboard.css",
    "src/assets/css/dark/apps/todolist.css",
    "src/assets/css/dark/authentication/auth-boxed.css",
    "src/assets/css/dark/authentication/auth-cover.css",
    "src/assets/css/dark/components/accordions.css",
    "src/assets/css/dark/components/carousel.css",
    "src/assets/css/dark/components/flags.css",
    "src/assets/css/dark/components/font-icons.css",
    "src/assets/css/dark/components/list-group.css",
    "src/assets/css/dark/components/media_object.css",
    "src/assets/css/dark/components/modal.css",
    "src/assets/css/dark/components/tabs.css",
    "src/assets/css/dark/components/timeline.css",
    "src/assets/css/dark/custom.css",
    "src/assets/css/dark/dashboard/dash_1.css",
    "src/assets/css/dark/dashboard/dash_2.css",
    "src/assets/css/dark/dashboard/dash_3.css",
    "src/assets/css/dark/elements/alert.css",
    "src/assets/css/dark/elements/color_library.css",
    "src/assets/css/dark/elements/custom-pagination.css",
    "src/assets/css/dark/elements/custom-tree_view.css",
    "src/assets/css/dark/elements/custom-typography.css",
    "src/assets/css/dark/elements/infobox.css",
    "src/assets/css/dark/elements/popover.css",
    "src/assets/css/dark/elements/search.css",
    "src/assets/css/dark/elements/tooltip.css",
    "src/assets/css/dark/forms/bootstrap-form.css",
    "src/assets/css/dark/forms/switches.css",
    "src/assets/css/dark/main.css",
    "src/assets/css/dark/pages/contact_us.css",
    "src/assets/css/dark/pages/error/error.css",
    "src/assets/css/dark/pages/error/style-maintanence.css",
    "src/assets/css/dark/pages/faq.css",
    "src/assets/css/dark/pages/knowledge_base.css",
    "src/assets/css/dark/scrollspyNav.css",
    "src/assets/css/dark/users/account-setting.css",
    "src/assets/css/dark/users/user-profile.css",
    "src/assets/css/dark/widgets/modules-widgets-vlm.css",
    "src/assets/css/dark/widgets/modules-widgets.css",
    "src/assets/css/light/apps/blog-create.css",
    "src/assets/css/light/apps/blog-post.css",
    "src/assets/css/light/apps/chat.css",
    "src/assets/css/light/apps/contacts.css",
    "src/assets/css/light/apps/ecommerce-create.css",
    "src/assets/css/light/apps/ecommerce-details.css",
    "src/assets/css/light/apps/invoice-add.css",
    "src/assets/css/light/apps/invoice-edit.css",
    "src/assets/css/light/apps/invoice-list.css",
    "src/assets/css/light/apps/invoice-preview.css",
    "src/assets/css/light/apps/mailbox.css",
    "src/assets/css/light/apps/notes.css",
    "src/assets/css/light/apps/scrumboard.css",
    "src/assets/css/light/apps/todolist.css",
    "src/assets/css/light/authentication/auth-boxed.css",
    "src/assets/css/light/authentication/auth-cover.css",
    "src/assets/css/light/components/accordions.css",
    "src/assets/css/light/components/carousel.css",
    "src/assets/css/light/components/flags.css",
    "src/assets/css/light/components/font-icons.css",
    "src/assets/css/light/components/list-group.css",
    "src/assets/css/light/components/media_object.css",
    "src/assets/css/light/components/modal.css",
    "src/assets/css/light/components/tabs.css",
    "src/assets/css/light/components/timeline.css",
    "src/assets/css/light/custom.css",
    "src/assets/css/light/dashboard/dash_1.css",
    "src/assets/css/light/dashboard/dash_2.css",
    "src/assets/css/light/dashboard/dash_3.css",
    "src/assets/css/light/elements/alert.css",
    "src/assets/css/light/elements/color_library.css",
    "src/assets/css/light/elements/custom-pagination.css",
    "src/assets/css/light/elements/custom-tree_view.css",
    "src/assets/css/light/elements/custom-typography.css",
    "src/assets/css/light/elements/infobox.css",
    "src/assets/css/light/elements/popover.css",
    "src/assets/css/light/elements/search.css",
    "src/assets/css/light/elements/tooltip.css",
    "src/assets/css/light/forms/bootstrap-form.css",
    "src/assets/css/light/forms/switches.css",
    "src/assets/css/light/main.css",
    "src/assets/css/light/pages/contact_us.css",
    "src/assets/css/light/pages/error/error.css",
    "src/assets/css/light/pages/error/style-maintanence.css",
    "src/assets/css/light/pages/faq.css",
    "src/assets/css/light/pages/knowledge_base.css",
    "src/assets/css/light/scrollspyNav.css",
    "src/assets/css/light/users/account-setting.css",
    "src/assets/css/light/users/user-profile.css",
    "src/assets/css/light/widgets/modules-widgets-vlm.css",
    "src/assets/css/light/widgets/modules-widgets.css",
    "src/assets/img/arrow-down.png",
    "src/assets/img/logo-3.svg",
    "src/assets/img/logo.svg",
    "src/bootstrap/css/bootstrap.min-old.css",
    "src/bootstrap/css/bootstrap.min.css",
    "src/bootstrap/js/bootstrap.bundle.min-old.js",
    "src/bootstrap/js/bootstrap.bundle.min.js",
    "src/plugins/css/dark/perfect-scrollbar/perfect-scrollbar.css",
    "src/plugins/css/light/perfect-scrollbar/perfect-scrollbar.css",
    "src/plugins/src/highlight/styles/monokai-sublime.css",
    "src/plugins/src/mousetrap/mousetrap.min.js",
    "src/plugins/src/perfect-scrollbar/perfect-scrollbar.min.js",
    "src/plugins/src/waves/waves.min.css",
    "src/plugins/src/waves/waves.min.js",
]

# Equation'ın loader.js dosyasında Tablo Stüdyo için yapılan değişiklikler
LOADER_DEGISIKLIKLERI = [
    ("var layoutName = 'Vertical Dark Menu';", "var layoutName = 'Tablo Studyo Panel';"),
    ("darkLogo: '../src/assets/img/logo.svg'", "darkLogo: '/static/panel/logo.svg'"),
    ("lightLogo: '../src/assets/img/logo-3.svg'", "lightLogo: '/static/panel/logo.svg'"),
    ("setAttribute('src', '../../src/assets/img/logo.svg')", "setAttribute('src', '/static/panel/logo.svg')"),
    ("setAttribute('src', '../../src/assets/img/logo2.svg')", "setAttribute('src', '/static/panel/logo.svg')"),
]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    kok = Path(sys.argv[1]).expanduser().resolve()
    kaynak = kok / "html" if (kok / "html").is_dir() else kok
    if not (kaynak / "src").is_dir() or not (kaynak / "layouts").is_dir():
        print(f"Equation klasörü bulunamadı: {kok}\n(İçinde html/src ve html/layouts olmalı.)")
        return 1

    eksik = []
    for yol in DOSYALAR:
        k = kaynak / yol
        if not k.is_file():
            eksik.append(yol)
            continue
        h = HEDEF / yol
        h.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(k, h)

    loader = HEDEF / "layouts" / "vertical-dark-menu" / "loader.js"
    if loader.is_file():
        metin = loader.read_text(encoding="utf-8")
        for eski, yeni in LOADER_DEGISIKLIKLERI:
            metin = metin.replace(eski, yeni)
        loader.write_text(metin, encoding="utf-8")

    print(f"{len(DOSYALAR) - len(eksik)} dosya kopyalandı: {HEDEF}")
    if eksik:
        print(f"Bulunamayan {len(eksik)} dosya (farklı bir Equation sürümü olabilir):")
        for yol in eksik:
            print("  -", yol)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
