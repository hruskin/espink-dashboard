# ESPink Server

> **Vývojová verze (DEV).** Běží vedle stabilního add-onu a desky neobsluhuje
> (port 8099 je ve výchozím stavu vypnutý). Rozvržení, panely i klíče desek má
> vlastní, nezávislé na stabilní verzi.

Lokální náhrada serveru [Živý obraz](https://zivyobraz.eu) pro e-paper ESPink
(7,5" GDEY075Z08, černá/bílá/červená). Add-on sbírá data z Home Assistantu
(kalendáře, odjezdy, meteostanice, předpověď, libovolné entity), vykreslí z nich
dashboard 480×800 a předá ho deskám protokolem Živého obrazu. Desky běží na baterii
a mezi probuzeními spí.

## Instalace

1. Zkopírujte složku add-onu do `/addons/` na HA OS (doplněk Samba nebo SSH).
2. **Nastavení → Doplňky → Obchod s doplňky → ⋮ → Zkontrolovat aktualizace.**
3. V sekci *Místní doplňky* nainstalujte add-on a spusťte ho.
4. Otevřete panel **ESPink** v postranním panelu HA.
5. Na kartě **Instalace** nahrajte firmware do desky (viz níže). Deska se pak objeví
   v **Panelech** pod *Nové desky*; úplně první deska se povolí sama.

Desky se připojují na `http://<IP Home Assistantu>:8099/index.php`.

## Webové rozhraní

### Rozvržení (editor)

Displej je mřížka 12 sloupců po 40 px, svisle po 4 px. Bloky se přetahují přímo
v náhledu a roztahují za úchyty; přesné souřadnice jdou zadat v nastavení bloku.
Překryvy editor nedovolí, volná místa jsou šrafovaná a kliknutím do nich jde přidat
blok. Obsah se přizpůsobí velikosti bloku (Události a Odjezdy podle výšky spočítají,
kolik řádků se vejde). Náhled vykresluje server stejně jako pro desku.

- **Uložit, Zpět, Znovu a Zahodit** jsou vpravo v horní liště; tečka a „Neuloženo“
  ukazují rozepsané změny. Ctrl+S uloží, Ctrl+Z / Ctrl+Shift+Z vrací.
- Klávesnice v náhledu: šipky posouvají vybraný blok, Shift+šipky mění velikost,
  Enter otevře nastavení, Delete smaže, Esc zruší výběr.
- Bloky: Záhlaví (s počasím nebo bez), Počasí teď, Odjezdy, Události, Předpověď,
  Jídelníček, Večerníček, Patička (i s entitami), Hodnota entity (stav nebo atribut, ikona, červeně pod/nad
  mezí, desetinná místa), Text a Šablona (Jinja v sandboxu; `states()`,
  `state_attr()`, `is_state()`, `now`, filtr `|num(1)`).
- Záhlaví ukazuje za měsícem **číslo týdne** („října · 43. týden“, když není místo,
  „října · 43. t.“); jde vypnout. V Patičce je číslo týdne pro nová rozvržení vypnuté.
- V Záhlaví jde vybrat **Výstrahy ČHMÚ** (`binary_sensor.…_vystrahy_chmu` z integrace
  ČHMÚ Meteogram). Platná výstraha, nebo výstraha začínající do 24 h, se ukáže červeně
  s ikonou v řádku upozornění, např. „Silný vítr · Bouřky, do zítra 6:00“, a má
  přednost před červenými kalendáři.
- **Jídelníček** (ve školce) bere senzor s atributy `pondeli` … `patek` (text
  „🥐 **Přesnídávka:** …\n\n🍲 **Oběd:** polévka\nhlavní jídlo …“), `obdobi_od`
  (pondělí týdne) a `je_aktualni`. Do zvolené hodiny (výchozí 14) ukazuje dnešek,
  potom další den; o víkendu pondělí. Přesnídávka a svačina jdou vypnout. Když je
  jídelníček neaktuální nebo z jiného týdne, blok napíše „Zatím není k dispozici“.
  Nadpis bloku („Jídelníček“) jde přepsat.
- **Večerníček** bere senzor z integrace ha-vecernicek (stav = název epizody, atributy
  `serial`, `datum`, `cas`) a ukáže v jednom řádku ikonu (výchozí
  `espink:paper-hat` – papírová čepice; další varianty `espink:paper-hat-3`,
  `espink:paper-hat-outline`, nakloněné `espink:paper-hat-tilt-left`, `-tilt-right`,
  `espink:paper-hat-outline-tilt-left` a `-outline-tilt-right` jsou ve výběru ikon nahoře a jdou použít v kterémkoli bloku), „Seriál: Epizoda“ a vpravo kdy se vysílá („dnes 18:45“;
  v úzkém bloku jen čas). Dlouhý název se zkrátí po celých slovech.
- U kalendáře v Událostech jde zapnout **Celý text** – názvy se nezkracují, ale
  zalomí (nejvýš 8 řádků).
- Rozvržení může být víc (nové prázdné nebo kopie, přejmenovat, smazat), dále export
  a import JSON, historie posledních 15 uložených verzí a ochrana proti přepsání
  z jiné karty prohlížeče.

### Panely

Každá deska (podle MAC) je panel s názvem, přiřazeným rozvržením, otočením
(normálně / vzhůru nohama), senzorem baterie a vlastním **rozvrhem probouzení**.
Víc panelů může sdílet jedno rozvržení. Volba **Na noc** vybere rozvržení pro poslední
obraz před dlouhým spánkem (aspoň 3 h podle rozvrhu). Ten se sestaví k času ranního
probuzení, takže přes noc a ráno panel ukazuje datum, jmeniny, události a předpověď
nového dne. Na širší obrazovce jsou karty vedle sebe.

Rozvrh je pruh přes celý den po čtvrthodinách; barva udává interval (5–30 min,
1× za hodinu v :00, nebo nebudit). Hranice úseků se posouvají tažením nebo šipkami
(PageUp/PageDown po hodině), klepnutím na úsek jde změnit interval, čas od–do,
úsek rozdělit nebo smazat (smazání i zánik úseku jde vrátit). Probuzení jsou
zarovnaná na násobky intervalu (:00, :05, …). Rozvrhy se dny v týdnu nebo
s minutami mimo čtvrthodiny jdou upravit v režimu **Pokročilé (pravidla)**:
`[dny ]HH:MM-HH:MM=sekundy`, dny 1 = Po … 7 = Ne, platí první vyhovující pravidlo,
mimo všechna okna deska spí. Panel bez vlastního rozvrhu používá výchozí `schedule`
z konfigurace add-onu.

### Instalace firmwaru

Firmware (patchovaný Živý obraz) se nahraje přímo z prohlížeče (Chrome nebo Edge
na počítači, deska připojená přes USB-C) pomocí ESP Web Tools; po nahrání instalátor
nabídne připojení k Wi-Fi. Adresa serveru, port a heslo konfiguračního AP desky se
do firmwaru vloží při stažení. Podporované desky: ESPink v3.6 (ESP32-S3) a ESPink 2.x
(ESP32). Panel HA (iframe) přístup k USB nepovoluje – otevřete stránku v samostatném
okně přes HTTPS.

### Stav

Přehled panelů: poslední kontakt, další probuzení, **poslední překreslení**, kolikrát
se panel dnes probudil a kolikrát se z toho překreslil, nebo jen usnul beze změny.
Překreslení potvrzuje deska až při dalším probuzení (pošle dobu posledního
překreslení), proto jsou počty o jedno probuzení pozadu. Když deska dostala nový
obsah a nepřekreslila se, ukáže se nahoře varování. **Proč se překreslila:** při každém probuzení
s novým obsahem server porovná obsah na displeji s novým a zapíše, co se změnilo
(„záhlaví: teplota“, „odjezdy“, „patička: baterie“…). Stav ukazuje počty za dnešek
a poslední důvod; v HA je to senzor *Důvod překreslení* (počty za den v atributech). Dále baterie a **odhad výdrže**, Wi-Fi, doba překreslení, důvod restartu a náhled. Tlačítko *Překreslit obsah teď* přepočítá obsah; desky si ho
vyzvednou při příštím probuzení.

## Jak to funguje

- Každých `render_interval` s add-on stáhne data a sestaví *model zobrazení*.
  Obrázek se znovu vykreslí (Typst, ~0,05 s) jen tehdy, když se model změní.
- Hlavička `Timestamp` se mění jen při změně obsahu. Když se nic nezměnilo, deska
  nepřekresluje a hned usne – to je hlavní úspora baterie.
- Číselné hodnoty se zaokrouhlují **s hysterezí**: nová hodnota se ukáže, až se
  skutečná vzdálí o ¾ kroku (z 11 °C na 12 °C až od 11,75 °C), takže hodnota na
  hranici nepřeskakuje a nevynucuje překreslení. **Teplota** (°C) se navíc změní až
  o 0,5 °C – s desetinami by se jinak panel překresloval skoro při každém probuzení.
- Hlavička `PreciseSleep` určuje dobu spánku podle rozvrhu panelu. Firmware ji omezí
  na 1 min – 24 h.
- **Stav panelů v HA přes MQTT discovery** (potřebuje broker, např. Mosquitto;
  přihlašovací údaje add-on vezme od Supervisoru). Každý panel je zařízení
  *ESPink <název>* se senzory Baterie, Napětí baterie, Signál WiFi, Doba překreslení,
  Teplota procesoru, Poslední kontakt, Další probuzení, Důvod restartu, Poslední
  překreslení, Překreslení dnes, Probuzení dnes, Nepřekresleno dnes (počty se
  o půlnoci nulují), Důvod překreslení a Výdrž baterie. Zprávy jsou
  retained. Bez brokeru se stav zapisuje přes REST API do senzoru baterie panelu.
- **Výdrž baterie:** add-on si ukládá napětí z probuzení (nejvýš po 30 min, 7 dní
  zpět, `/data/battery.json`) a proloží procenta přímkou. Výdrž = dny do vybití při
  dosavadním poklesu. Odhad se ukáže až po dni dat; skok napětí o víc než 0,06 V
  (nabíjení, odpojení USB, výměna baterie) historii smaže a odhad začne znovu.
- **Upozornění na slabou baterii:** pod 15 % nebo pod 7 dní výdrže pošle add-on do HA
  jedno upozornění (Oznámení), případně i na službu z volby `battery_notify`
  (např. `notify.mobile_app_telefon`). Znovu až po nabití.
- Emoji z tabulky `app/emoji.py` (👟, ♻️, ⚠️, 🎂…) se kreslí jako plné ikony MDI.

## Bezpečnost

- **Povolené desky:** port 8099 obsluhuje jen povolené desky (MAC) se známým klíčem
  z hlavičky `X-API-Key` (náhodný klíč uložený v desce, `/data/security.json`, práva
  0600). Nová deska čeká v Panelech na povolení; u čekající desky je vidět IP a konec
  klíče, ať jde poznat ta pravá. Desky povolené před zavedením klíčů se ho naučí jen
  během 14 dnů po aktualizaci. Po smazání flash má deska nový klíč – stačí ji znovu
  povolit.
- **Omezení pokusů:** po opakovaných chybných klíčích se MAC nebo IP na 15 minut
  zablokuje; čekajících desek je nejvýš 10 (3 z jedné IP).
- **Vstupy od desky:** tělo nejvýš 16 kB (i chunked), nevalidní JSON se ignoruje,
  texty se zkracují, číselné hodnoty se kontrolují.
- **Šablony** běží v sandboxu Jinja v odděleném procesu s limitem paměti a času (2 s).
- **Typst** dostává text jen jako data, bez sítě a bez přístupu k souborům.
- **Webové rozhraní** je dostupné jen přes Ingress (administrátoři HA); ukládání
  vyžaduje `application/json` (ochrana proti CSRF). ID entit se kontrolují.
- **Instalátor** má ESP Web Tools přibalené, nic se nenačítá z CDN.
- **Firmware** ignoruje pokyn k OTA ze serveru a konfigurační AP má vlastní heslo.

## Konfigurace add-onu

Obsah dashboardu se nastavuje v editoru. V *Konfiguraci* add-onu zůstávají
technické volby a výchozí hodnoty:

| Volba | Význam |
|---|---|
| `rotate` | Výchozí otočení pro nové panely (90/270). |
| `image_format` | `z2` (kompaktní, výchozí) nebo `png`. |
| `render_interval` | Jak často add-on kontroluje data (s). |
| `schedule` | Výchozí rozvrh probouzení pro panely bez vlastního. |
| `battery_notify` | Služba pro upozornění na slabou baterii navíc k Oznámení v HA, např. `notify.mobile_app_telefon`. |
| `allowed_devices` | MAC adresy povolených desek (spravuje karta Panely). |
| ostatní (`meteo_*`, `weather`, `departures`, `calendars`…) | Výchozí obsah, dokud se rozvržení neuloží z editoru. |

## Lokální vývoj

```bash
./dev.sh          # skutečná data z HA → http://localhost:8098 (jen z tohoto počítače)
./dev.sh mock     # ukázková data, bez HA
./dev.sh once x.png
```

První spuštění vytvoří `.venv` a `.env.dev`; do `.env.dev` doplňte `HA_TOKEN`
od uživatele **bez** administrátorských práv. UI odpovídá jen na `localhost`
(ochrana proti DNS rebindingu). Soubory `.env.dev`, `dev-*.json`, `security.json`
a `.venv` se necommitují.
