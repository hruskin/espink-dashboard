ARG BUILD_FROM
FROM ${BUILD_FROM}

# Vykreslovače (volba renderer): Chromium (původní), WeasyPrint + pdftoppm, Typst.
# Python knihovny z apk (bez kompilace); WeasyPrint z PyPI – v Alpine 3.21 je 63,
# ta neumí gap a šířky položek flexboxu (čistý Python, pango z apk).
RUN apk add --no-cache \
      python3 py3-aiohttp py3-jinja2 py3-pillow py3-paho-mqtt py3-pip py3-cffi \
      chromium font-dejavu tzdata pango poppler-utils typst \
 && pip install --no-cache-dir --break-system-packages "weasyprint==70.0" \
 && adduser -D -H -s /sbin/nologin espink-render

COPY app /app
WORKDIR /app

CMD ["python3", "-u", "/app/main.py"]
