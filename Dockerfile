FROM python:3.12-slim

WORKDIR /uygulama
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cekirdek.py degerlendirme.py destek.py hesaplar.py magaza.py odeme.py sepet.py siparisler.py sunucu.py ./
COPY static ./static
COPY pencere_ornekleri ./pencere_ornekleri

# Hesaplar, her kullanıcının odaları, resimleri ve çalışma durumu bu klasörde kalıcı tutulur (volume bağlayın)
ENV STUDYO_VERI=/veri
VOLUME ["/veri"]
EXPOSE 8000

CMD ["uvicorn", "sunucu:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
