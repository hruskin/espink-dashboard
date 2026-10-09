ARG BUILD_FROM
FROM ${BUILD_FROM}

# Chromium jen jako vykreslovač HTML -> PNG; Python knihovny z apk (bez kompilace)
RUN apk add --no-cache \
      python3 py3-aiohttp py3-jinja2 py3-pillow \
      chromium font-dejavu tzdata \
 && adduser -D -H -s /sbin/nologin espink-render

COPY app /app
WORKDIR /app

CMD ["python3", "-u", "/app/main.py"]
