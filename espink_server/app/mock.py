"""Ukázková data pro vývoj bez Home Assistantu (MOCK=1)."""
from datetime import datetime, timedelta


def raw(opts: dict, now: datetime) -> dict:
    tz = now.tzinfo
    today = now.date()

    def at(days: int, h: int, m: int = 0) -> str:
        return (datetime.combine(today + timedelta(days=days), datetime.min.time(), tz)
                + timedelta(hours=h, minutes=m)).isoformat()

    def allday(days: int, length: int = 1) -> dict:
        d = today + timedelta(days=days)
        return {"start": {"date": d.isoformat()}, "end": {"date": (d + timedelta(days=length)).isoformat()}}

    deps = []
    for i, (route, head, plat, delay) in enumerate([
        ("339", "Praha,Budějovická", "D", 3), ("335", "Kamenice,kult.dům", "A", 4),
        ("337", "Praha,Budějovická", "D", 0), ("335", "Praha,Budějovická", "D", 0),
        ("339", "Praha,Budějovická", "D", 1), ("335", "Praha,Budějovická", "B", 0),
    ]):
        sched = now + timedelta(minutes=4 + i * 11)
        deps.append({
            "route": route, "headsign": head, "platform": plat, "delay_min": delay,
            "delay_available": True, "canceled": i == 4,
            "scheduled": sched.isoformat(), "predicted": (sched + timedelta(minutes=delay)).isoformat(),
        })

    states = {
        opts["meteo_temperature"]: {"state": "11.9", "attributes": {}},
        opts["meteo_humidity"]: {"state": "63", "attributes": {}},
        opts["meteo_rain_today"]: {"state": "2.4", "attributes": {}},
        opts["weather"]: {"state": "partlycloudy", "attributes": {"temperature": 10.6}},
        opts["departures"]: {"state": now.isoformat(), "attributes": {"stop_name": "Křížkový Újezdec", "departures": deps}},
        opts["disruptions"]: {"state": "1", "attributes": {"infotexts": [{"text": "Omezení provozu linky 335"}]}},
    }
    for cal, icon in [("calendar.rodina", "mdi:home-heart"), ("calendar.skolka", "mdi:school"),
                      ("calendar.plavani", "mdi:swim"), ("calendar.narozeniny_2", "mdi:cake-variant"),
                      ("calendar.svoz_popelnice", "mdi:trash-can"), ("calendar.martin_hruska", "mdi:account")]:
        states[cal] = {"state": "off", "attributes": {"icon": icon}}
    forecast = []
    for i, (cond, hi, lo, pr) in enumerate([("partlycloudy", 14, 4, 0), ("rainy", 12, 6, 4.2),
                                            ("cloudy", 11, 5, 0.3), ("sunny", 16, 3, 0), ("pouring", 9, 6, 12)]):
        forecast.append({"datetime": at(i, 12), "condition": cond, "temperature": hi, "templow": lo, "precipitation": pr})

    events = {
        "calendar.rodina": [
            {"summary": "Babička na návštěvě", **allday(0)},
            {"summary": "Večeře u Novákových", "start": {"dateTime": at(0, 18, 30)}, "end": {"dateTime": at(0, 21)}},
            {"summary": "Dovolená Šumava", **allday(3, 3)},
        ],
        "calendar.martin_hruska": [
            {"summary": "Servis auta – výměna pneumatik", "start": {"dateTime": at(1, 9)}, "end": {"dateTime": at(1, 10)}},
            {"summary": "Porada", "start": {"dateTime": at(2, 8, 30)}, "end": {"dateTime": at(2, 9)}},
        ],
        "calendar.skolka": [
            {"summary": "Fotografování", "start": {"dateTime": at(0, 23)}, "end": {"dateTime": at(0, 23, 30)}},
            {"summary": "Divadlo ve školce – přinést 50 Kč", "start": {"dateTime": at(2, 9, 30)}, "end": {"dateTime": at(2, 11)}},
        ],
        "calendar.plavani": [
            {"summary": "🏃 Atletika – závody 🏅", "start": {"dateTime": at(1, 16)}, "end": {"dateTime": at(1, 17)}},
        ],
        "calendar.narozeniny_2": [{"summary": "Narozeniny – Petr", **allday(2)}],
        "calendar.svoz_popelnice": [{"summary": "Svoz – papír", **allday(1)}],
        "calendar.jmenne_svatky_ceske": [{"summary": "Štefan", **allday(0)}],
        "calendar.ceske_statni_svatky": [{"summary": "Den vzniku samostatného čs. státu", **allday(4)}],
    }
    return {"states": states, "events": events, "forecast": forecast}
