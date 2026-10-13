# ESPink Server

> **Vývojová verze (DEV).** Běží vedle stabilního add-onu ESPink Server a desku
> neobsluhuje (port 8099 je ve výchozím stavu vypnutý). Slouží k vývoji editoru
> rozvržení: obsah dashboardu se skládá z bloků (`/data/layouts.json`). Dokud
> rozvržení není uložené z editoru, odvozuje se z konfigurace níže, takže dashboard
> vypadá stejně jako ve stabilní verzi.
>
> **Editor** (panel ESPink DEV → Rozvržení): displej je mřížka 12 sloupců po 40 px,
> svisle po 4 px. Bloky se tahají přímo v náhledu a roztahují za úchyty (šipky posouvají
> vybraný blok, Shift+šipky mění velikost); přesné souřadnice jdou zadat v panelu.
> Překryvy editor nedovolí, volná místa jsou šrafovaná. Obsah se přizpůsobí velikosti
> bloku – Události a Odjezdy podle výšky spočítají, kolik řádků se vejde, úzké bloky
> skryjí méně důležité detaily. „Seřadit pod sebe“ srovná bloky na celou šířku.
> Náhled vykresluje server stejně jako pro desku. Po prvním uložení platí pro obsah jen
> editor (entity v Systém → nastavení se pak už nepoužijí). Baterii v náhledu bere DEV
> verze ze senzoru, který plní stabilní add-on. Starší rozvržení (bloky pod sebou) se
> převede automaticky.
>
> Bloky: Záhlaví (s počasím nebo bez), Počasí teď, Odjezdy, Události, Předpověď,
> Patička (i s libovolnými entitami), Hodnota entity (stav nebo atribut, textové stavy
přeložené, ikona, červeně pod/nad mezí), Text a Šablona (Jinja
> v sandboxu + HTML; `states()`, `state_attr()`, `is_state()`, `now`, filtr `|num(1)`).
> Dál: prohození stejně širokých pásů přetažením, dvojklik na úchyt = roztáhnout
k sousedovi, duplikace, náhled „nejhorší případ“, mřížka sloupců, výběr ikon, export/import
> JSON, historie posledních 15 uložených verzí, ochrana proti přepsání z jiné karty.
>
> **Více panelů:** rozvržení může být víc (výběr nahoře v editoru: nové – prázdné
> nebo kopie, přejmenovat, smazat). Na kartě **Panely** má každá deska (podle MAC)
> název, přiřazené rozvržení, vlastní rozvrh probouzení (prázdné = výchozí ze Systému)
> a senzor baterie (`sensor.espink_<název>_baterie`). Víc panelů může sdílet jedno
> rozvržení. Nová deska, která se ozve, se objeví v Panelech pod *Nové desky* a po
> „Povolit“ se zapíše do `allowed_devices`. Odebrání panelu desku zase zakáže.
> Dosavadní `layout.json` se převezme jako rozvržení „Hlavní“ a povolené desky jako
> panely (první s dosavadním `battery_sensor`). Panely jsou v `/data/panels.json`.
>
> API (jen přes Ingress, `?id=` = rozvržení): `GET layout.json`, `POST layout/preview`
> (PNG), `POST layout` (uložení), `GET layout/history.json`, `POST layouts/create|rename|delete`,
> `GET panels.json`, `POST panels/save|approve|delete`, `GET preview.png?panel=MAC`.

Lokální náhrada serveru [Živý obraz](https://zivyobraz.eu) pro e-paper ESPink.
Add-on sbírá data z Home Assistantu (kalendáře, odjezdy, meteostanice, předpověď),
vykreslí z nich dashboard 480×800 ve třech barvách a předá ho zařízení protokolem
Živého obrazu. Zařízení běží na baterii a mezi probuzeními spí.

## Instalace (lokální add-on)

1. Zkopírujte složku `espink_server` do `/addons/` na HA OS (doplněk Samba nebo SSH).
2. **Nastavení → Doplňky → Obchod s doplňky → ⋮ → Zkontrolovat aktualizace.**
3. V sekci *Místní doplňky* nainstalujte **ESPink Server** a spusťte ho.
4. V konfiguraci zkontrolujte entity (hlavně `meteo_temperature`/`meteo_humidity`).
5. Náhled najdete v postranním panelu pod položkou **ESPink**.

Zařízení se připojuje na `http://<IP Home Assistantu>:8099/index.php`.
Firmware s touto adresou připravíte podle `firmware/README.md`.

## Jak to funguje

- Každých `render_interval` s add-on stáhne data a sestaví *model zobrazení*.
  Obrázek se znovu vykreslí jen tehdy, když se model změní.
- Hlavička `Timestamp` se mění jen při změně obsahu. Když se nic nezměnilo,
  zařízení nepřekresluje a hned usne. To je hlavní úspora baterie.
- Hlavička `PreciseSleep` určuje dobu spánku podle rozvrhu panelu (jinak `schedule`).
- **Stav panelů v HA přes MQTT discovery** (potřebuje broker, např. doplněk Mosquitto
  a integraci MQTT; add-on si přihlašovací údaje vezme od Supervisoru). Každý panel je
  v HA zařízení *ESPink <název>* se senzory Baterie, Napětí baterie, Signál WiFi,
  Doba překreslení, Teplota procesoru, Poslední kontakt, Další probuzení a Důvod
  restartu. Zprávy jsou retained, takže entity i hodnoty přežijí restart HA.
  Entity ID baterie = senzor baterie panelu (první panel `sensor.espink_baterie`);
  starý stav zapsaný přes REST se před prvním ohlášením smaže, aby ID zůstalo stejné.
  Přejmenování panelu přejmenuje zařízení, odebrání panelu zařízení z HA smaže.
  Témata: `homeassistant/device/espink_<mac>/config`, `espink/espink_<mac>/state`.
- Bez brokeru se stav zapisuje postaru přes REST API do senzoru baterie panelu
  (atributy `voltage`, `rssi`, `last_seen`, `next_wake`, `refresh_ms`…); po restartu
  HA zmizí, dokud se zařízení znovu neozve.

## Bezpečnost

- **Povolená zařízení:** port 8099 obsluhuje jen desky ze seznamu `allowed_devices`
  (MAC adresy). Je-li seznam prázdný, první deska, která se ozve, se zapíše sama;
  ostatní dostanou `403`. Odmítnuté desky jsou vidět v Panelech (*Nové desky*),
  kde je jde jedním tlačítkem povolit.
- **Omezení rychlosti:** deska vynutí nové vykreslení nejvýš jednou za 30 s a zápis
  do HA nejvýš jednou za minutu.
- **Webové rozhraní** je dostupné jen přes Ingress (administrátoři HA); ukládání
  vyžaduje `application/json` (ochrana proti CSRF).
- **ID entit** se kontrolují (`doména.objekt`), takže se jimi nedá zavolat jiné API HA.
- **Chromium** běží pod neprivilegovaným uživatelem, bez sítě a stránka má
  Content-Security-Policy, která blokuje skripty.
- **Firmware** ignoruje pokyn k OTA aktualizaci ze serveru a konfigurační AP má
  vlastní heslo (viz `firmware/README.md`).

## Konfigurace

Nejpohodlnější je stránka **Nastavení** ve webovém rozhraní add-onu (odkaz pod náhledem).
U entit nabízí našeptávač z Home Assistantu a pod polem ukáže název entity, nebo
upozorní, že entita neexistuje. Uložení se zapíše do konfigurace add-onu a projeví
se hned, bez restartu. Stejné volby jsou i na záložce *Konfigurace* add-onu:

| Volba | Význam |
|---|---|
| `rotate` | Otočení obrazu pro panel (90/270). Je-li obraz vzhůru nohama, přepněte 90 ↔ 270. |
| `image_format` | `z2` (kompaktní, výchozí) nebo `png`. |
| `render_interval` | Jak často add-on kontroluje data (s). |
| `meteo_*`, `indoor_temperature` | Senzory meteostanice. Prázdná hodnota = nezobrazovat. |
| `weather` | Zdroj počasí: entita `weather.*` pro ikonu a předpověď (denní, nebo dopočtená z hodinové). |
| `departures`, `disruptions` | Senzory integrace [ha-pid-odjezdy](https://github.com/hruskin/ha-pid-odjezdy). Linky a nástupiště filtrujte přímo v integraci. |
| `departures_count` | Počet zobrazených odjezdů (0 = sekci skrýt). |
| `walk_minutes` | Nezobrazovat spoje, které už nestihnete. |
| `calendars` | Seznam kalendářů: `entity`, volitelně `label` (štítek u události), `icon` (např. `mdi:soccer`; jinak ikona entity z HA) a `red: true` (zvýraznit červeně). |
| `nameday_calendar`, `holiday_calendar` | Svátek a státní svátky v hlavičce dne. |
| `event_days` | Kolik dní dopředu ukazovat události vždy; pokud zbývá místo, přidají se další dny (až 31). |
| `schedule` | Výchozí rozvrh probouzení pro panely bez vlastního, viz níže. |
| `allowed_devices` | MAC adresy povolených desek (viz Bezpečnost). |

### Rozvrh probouzení

Formát pravidla je `[dny ]HH:MM-HH:MM=sekundy`, kde dny jsou 1 = Po … 7 = Ne.
Platí první pravidlo, které vyhovuje. Mimo všechna okna zařízení spí až do začátku
nejbližšího okna.

```yaml
schedule:
  - "1-5 05:45-08:30=300"   # pracovní dny ráno každých 5 min (autobusy)
  - "06:00-22:30=1200"      # jinak přes den každých 20 min
                            # v noci spí do 5:45 / 6:00
```

## Lokální náhled (vývoj)

```bash
./dev.sh          # skutečná data z HA → http://localhost:8099 (jen z tohoto počítače)
./dev.sh mock     # ukázková data, bez HA
./dev.sh once x.png
```

První spuštění vytvoří `.venv` a `.env.dev`. Do `.env.dev` doplňte `HA_TOKEN`
(HA → Profil → Zabezpečení → Dlouhodobé přístupové tokeny). Konfigurace náhledu
je v `dev-options.json` a dá se měnit i přes stránku Nastavení. Úpravy šablony
se v prohlížeči projeví hned po uložení, změny v Pythonu vyžadují restart skriptu.
Soubory `.env.dev`, `dev-options.json` a `.venv` se necommitují.

Panel s náhledem (`/`, `/preview.png`, `/view.json`) je dostupný jen přes Ingress.
Na portu 8099 je z LAN dostupné pouze `/index.php` pro zařízení.
