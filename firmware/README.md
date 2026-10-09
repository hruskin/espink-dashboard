# Firmware ESPink v3.6 + GDEY075Z08 (Živý obraz s lokálním serverem)

Tento adresář obsahuje open-source firmware [Živý obraz](https://github.com/MultiTricker/zivyobraz-fw) (GPL-3.0) s jedinou úpravou. Zařízení se neptá cloudu `cdn.zivyobraz.eu`, ale add-onu **ESPink Server** v Home Assistantu, který mu po HTTP posílá hotový obrázek a říká, jak dlouho má spát.

- **Deska:** LaskaKit ESPink v3.6 (ESP32-S3-WROOM-1-N16R8). Firmware ji vede jako `ESPink_V35`. Piny se shodují: CS 10, DC 48, RST 45, BUSY 38, SPI CLK 12 / MOSI 11, napájení e-papíru GPIO47, baterie ADC GPIO9 ×1,769, tlačítko GPIO40.
- **Displej:** Good Display GDEY075Z08, 7,5", 800×480, černá/bílá/červená (`DISPLAY_TYPE=GDEY075Z08`, `COLOR_TYPE=3C`).
- **Upstream:** připnutý commit `3d7a5a518df8deec32d5f53bd784c099857deeb8` (FW 3.2, 14. 8. 2026).

## Obsah

| Soubor | Účel |
|---|---|
| `config.env` | **Jediné místo pro nastavení**: IP adresa a port serveru |
| `prepare.sh` | Stáhne upstream, aplikuje patche a přidá PlatformIO prostředí `espink_v36_gdey075z08` |
| `patches/01-local-server.patch` | Umožní nastavit host (`ZO_HOST`) a port (`ZO_PORT`) při sestavení. Bez nich firmware zůstane beze změny. |
| `zivyobraz-fw/` | Vygenerovaný zdrojový kód. Needituj ho, `prepare.sh` ho při každém spuštění vrátí do čistého stavu. |

## Nastavení serveru

V `config.env` nastav IP adresu Home Assistantu a port add-onu:

```sh
ZO_HOST=192.168.1.10
ZO_PORT=8099
```

Zadej IP adresu, ne `homeassistant.local`. Překlad mDNS po probuzení zdržuje, a tím zbytečně spotřebovává baterii. Doporučuju HA přidělit pevnou IP adresu v routeru.

Jednorázově se dá hodnota přebít i proměnnou prostředí: `ZO_HOST=192.168.1.20 ./prepare.sh`.

## Sestavení a nahrání

Potřebuješ PlatformIO (`pipx install platformio` nebo `pip install platformio`).

```sh
./prepare.sh                      # po každé změně config.env nebo patchů
cd zivyobraz-fw
pio run -e espink_v36_gdey075z08              # sestavení
pio run -e espink_v36_gdey075z08 -t upload    # nahrání přes USB-C
pio device monitor -b 115200                  # sériový log (volitelné)
```

Deska používá nativní USB ESP32-S3. Kdyby se nahrávání nechytilo, podrž **BOOT**, stiskni **RESET**, pusť BOOT a spusť upload znovu.

Ověřené sestavení: RAM 29,4 %, flash 15,2 % (~1 MB z 6,25 MB aplikačního oddílu, rozdělení `default_16MB.csv` se dvěma OTA sloty).

## První připojení k WiFi

Upstream firmware nabízí dva způsoby:

1. **WiFiManager (přístupový bod):** když zařízení nezná WiFi nebo se k ní nepřipojí, spustí vlastní AP s názvem `INK_<MAC bez dvojteček>` a heslem **`zivyobraz`**. Na displeji se zobrazí název AP, heslo a adresa konfiguračního portálu (`http://192.168.4.1`). Po připojení k AP v portálu vybereš síť a zadáš heslo.
2. **Improv přes sériovou linku:** když zařízení běží v režimu AP, poslouchá zároveň na USB sériové lince protokol Improv. Přihlašovací údaje se tak dají poslat i z prohlížeče přes web s Improv (Chrome/Edge).

Zařízení si navíc pamatuje kanál a BSSID poslední sítě, takže se po probuzení připojí rychleji. Pokud se připojit nepodaří, provede úplné vyhledání sítí.

## Tlačítko

Tlačítko na GPIO40 **nedokáže probudit desku z deep sleepu**, protože GPIO40 není RTC pin. Firmware proto buzení tlačítkem pro `ESPink_V35` výslovně vypíná. Probuzení zajišťuje jen časovač nebo tlačítko **RESET**.

Tlačítko GPIO40 se vyhodnocuje při startu, tedy když ho držíš během stisku RESET:

| Jak dlouho držet | Akce |
|---|---|
| < 2 s | restart |
| 2–6 s | vymaže displej (na uskladnění) a usne |
| > 6 s | smaže uložené WiFi údaje a restartuje (znovu se spustí AP) |

## Návrat na oficiální zivyobraz.eu

Buď nahraj oficiální firmware přes [webový instalátor](https://zivyobraz.eu), nebo sestav tento projekt bez `ZO_HOST`/`ZO_PORT` a bez `-D USE_CLIENT_HTTP`. Firmware pak použije výchozí `cdn.zivyobraz.eu:443` přes HTTPS.

## Protokol

Ověřeno ve zdrojovém kódu upstreamu (`src/http_client.cpp`, `src/main.cpp`, `src/image_handler.cpp`). Server musí odpovídat **přesně** takto:

**Požadavek** (zařízení → server):
- `POST /index.php?timestampCheck=1` při kontrole. Když jde o samostatné stažení obrázku, je to `timestampCheck=0`.
- Hlavičky: `Host: <ZO_HOST>`, `X-API-Key: <klíč uložený v NVS>`, `Content-Type: application/json`, `Connection: close`.
- Tělo je JSON:
  ```json
  {
    "fwVersion": "3.2", "apiVersion": "3.2", "buildDate": "...", "board": "ESPink_V35",
    "system":  { "cpuTemp": 41.0, "resetReason": "deepsleep", "vccVoltage": 3.98 },
    "network": { "ssid": "...", "rssi": -61, "mac": "AA:BB:CC:DD:EE:FF", "apRetries": 0,
                 "ipAddress": "192.168.1.50", "lastDownloadDuration": 812 },
    "display": { "type": "GDEY075Z08", "width": 800, "height": 480, "colorType": "3C",
                 "lastRefreshDuration": 17000 }
  }
  ```
  - `system.vccVoltage` je napětí baterie ve **voltech** (float).
  - `network.rssi` je síla signálu v **dBm** (int).
  - `lastDownloadDuration` a `lastRefreshDuration` jsou v **ms** a posílají se jen tehdy, když jsou větší než 0.

**Odpověď** (server → zařízení):
- Stavový řádek musí začínat přesně `HTTP/1.1 200 OK` nebo `HTTP/1.0 200 OK`. Cokoli jiného zařízení bere jako chybu a usne na **120 s**.
- Hlavičky se hledají podle začátku řádku s rozlišením velkých a malých písmen. Hodnota se čte od pevné pozice, proto je nutný přesně jeden znak mezera za dvojtečkou:
  - `Timestamp: <int>` (`substring(11)`). Když se shoduje s hodnotou uloženou v RTC paměti, zařízení **nepřekresluje** a usne.
  - `PreciseSleep: <sekundy>` (`substring(14)`). Délka spánku. Bez této hlavičky zařízení spí 120 s. Ze spánku se odečte doba stahování a překreslení, nejvýš 60 s.
  - `Rotate: <int>` (volitelné). Jakákoli hodnota vypne přímé streamování a otočí obraz o 180°. **Nepoužívat**, rotaci dělá server.
  - Volitelně `X-OTA-Update: <url>` (zařízení stáhne a nahraje firmware), `PartialRefresh` (3C panel to neumí), `ForceWifiFullScan`, `ShowNoWifiError: 0|1`.
- **Tělo musí následovat hned za hlavičkami už v odpovědi na `timestampCheck=1`.** Firmware nechává spojení otevřené a obrázek z něj rovnou streamuje. Když `Timestamp` beze změny, tělo zahodí.
- `Transfer-Encoding: chunked` **není podporované**, protože tělo se čte jako surové bajty. Server musí poslat `Content-Length`.
- Když přímé streamování nejde použít, zařízení si obrázek stáhne znovu přes `timestampCheck=0`, případně i několikrát (stránkovaný režim). Server proto musí stejný obrázek vracet opakovaně, ne generovat nový.
- Formát obrázku se pozná podle prvních 2 bajtů těla. Před nimi může být nejvýš ~1 kB „smetí“.
  - **Z2** (doporučený): ASCII `Z2` a za ním bajty `(barva << 6) | počet`, kde počet je 1–63. Barvy: `0` bílá, `1` černá, `2` červená. Pixely jdou po řádcích v nativní orientaci panelu, tedy **800×480 na šířku**. Běh může přecházet přes konec řádku.
  - PNG: barvy se mapují podle prahu. Červená je, když `r ≥ 128`, `r > g+80` a `r > b+80`. Jinak se počítá jas `(77r+150g+29b)>>8` a hodnota ≤ 160 znamená černou.
  - Dále Z1 (1 bajt barvy a 1 bajt počtu) a Z3 (3 bity barvy a 5 bitů počtu).
