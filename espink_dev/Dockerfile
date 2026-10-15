ARG BUILD_FROM
FROM ${BUILD_FROM}

# Obrázek kreslí Typst (šablona templates/dashboard.typ, fonty v app/static/fonts/typst);
# Python knihovny z apk (bez kompilace). Verze zafixované na řadu z Alpine 3.21 (~ = jen opravy),
# aby nová verze balíčku nezměnila vykreslování nebo API bez povšimnutí; tzdata se aktualizuje
RUN apk add --no-cache \
      python3~3.12 py3-aiohttp~3.10 py3-jinja2~3.1 py3-pillow~11.0 py3-paho-mqtt~1.6 typst~0.12 tzdata

COPY app /app
WORKDIR /app

CMD ["python3", "-u", "/app/main.py"]
