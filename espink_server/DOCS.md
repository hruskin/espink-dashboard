# ESPink Server

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
- Hlavička `PreciseSleep` určuje dobu spánku podle rozvrhu `schedule`.
- Napětí baterie a signál WiFi ze zařízení se zapisují do `battery_sensor`
  (výchozí `sensor.espink_baterie`). Senzor má atributy `voltage`, `rssi`,
  `last_seen` a `next_wake`. Po restartu HA zmizí, dokud se zařízení znovu neozve.

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
| `calendars` | Seznam kalendářů: `entity`, volitelně `label` (štítek u události) a `red: true` (zvýraznit červeně). |
| `nameday_calendar`, `holiday_calendar` | Svátek a státní svátky v hlavičce dne. |
| `event_days` | Kolik dní dopředu ukazovat události. |
| `schedule` | Rozvrh probouzení, viz níže. |

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

## Vývoj bez HA

```bash
cd app
MOCK=1 OPTIONS_PATH=options.example.json python main.py --once nahled.png   # jen obrázek
DEV=1 MOCK=1 OPTIONS_PATH=options.example.json python main.py               # server na :8099
DEV=1 HA_URL=http://ha:8123 HA_TOKEN=... python main.py             # se skutečnými daty
```

Panel s náhledem (`/`, `/preview.png`, `/view.json`) je dostupný jen přes Ingress.
Na portu 8099 je z LAN dostupné pouze `/index.php` pro zařízení.
