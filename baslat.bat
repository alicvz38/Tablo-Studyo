@echo off
cd /d "%~dp0"
rem Magazadan uye olanlar musteri olur; yeni saticiyi saticilar panelden ekler.
rem Bir kez acmaniz yeterli: proje dosyalari (.py) kaydedilince sunucu kendini kapatip yeniden acar.
rem Otomatik yeniden baslatmayi kapatmak icin alttaki satirin basindaki "rem " yazisini silin:
rem set STUDYO_OTOMATIK=0
title Tablo Studyo Sunucusu
python -m pip install --quiet -r requirements.txt
python sunucu.py
rem Hata olursa pencere kapanmasin, mesaj okunabilsin
if errorlevel 1 pause
