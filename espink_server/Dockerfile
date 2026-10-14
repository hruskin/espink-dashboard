ARG BUILD_FROM
FROM ${BUILD_FROM}

# Obrázek kreslí Typst (šablona templates/dashboard.typ, fonty v app/static/fonts/typst);
# Python knihovny z apk (bez kompilace)
RUN apk add --no-cache \
      python3 py3-aiohttp py3-jinja2 py3-pillow py3-paho-mqtt typst tzdata

COPY app /app
WORKDIR /app

CMD ["python3", "-u", "/app/main.py"]
