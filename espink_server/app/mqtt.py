"""Panely jako zařízení v HA přes MQTT discovery.

Každý panel = zařízení se senzory (baterie, napětí, signál, doba překreslení…).
Konfigurace i stav jsou retained, takže entity i poslední hodnoty přežijí restart HA
(na rozdíl od stavu zapsaného přes REST API, který po restartu zmizí).
"""
import json
import os
import threading

PREFIX = "homeassistant"
BASE = "espink"
ORIGIN = {"name": "ESPink Server", "url": "https://github.com/hruskin/espink-dashboard"}

# klíč -> (název, konfigurace komponenty); hodnoty bere šablona z JSONu ve stavovém tématu
SENSORS = {
    "battery": ("Baterie", {"dev_cla": "battery", "unit_of_meas": "%", "stat_cla": "measurement"}),
    "voltage": ("Napětí baterie", {"dev_cla": "voltage", "unit_of_meas": "V", "stat_cla": "measurement",
                                   "sug_dsp_prc": 2, "ent_cat": "diagnostic"}),
    "rssi": ("Signál WiFi", {"dev_cla": "signal_strength", "unit_of_meas": "dBm", "stat_cla": "measurement",
                             "ent_cat": "diagnostic"}),
    "refresh_s": ("Doba překreslení", {"dev_cla": "duration", "unit_of_meas": "s", "stat_cla": "measurement",
                                       "sug_dsp_prc": 1, "ent_cat": "diagnostic"}),
    "cpu_temp": ("Teplota procesoru", {"dev_cla": "temperature", "unit_of_meas": "°C", "stat_cla": "measurement",
                                       "ent_cat": "diagnostic"}),
    "last_seen": ("Poslední kontakt", {"dev_cla": "timestamp", "ent_cat": "diagnostic"}),
    "next_wake": ("Další probuzení", {"dev_cla": "timestamp"}),
    "reset_reason": ("Důvod restartu", {"ent_cat": "diagnostic", "ic": "mdi:restart"}),
}


def node(mac: str) -> str:
    return "espink_" + mac.replace(":", "").lower()


def state_topic(mac: str) -> str:
    return f"{BASE}/{node(mac)}/state"


def config_topic(mac: str) -> str:
    return f"{PREFIX}/device/{node(mac)}/config"


def discovery(cfg: dict, fw: str | None) -> dict:
    """Konfigurace zařízení (device discovery, HA 2024.11+). Entity ID se odvodí z názvu
    zařízení („ESPink Kuchyň“ + „Baterie“ = sensor.espink_kuchyn_baterie); baterie
    dostane ID ze senzoru baterie panelu, pokud je vyplněný."""
    n, name = node(cfg["mac"]), cfg["name"]
    dev = {"ids": [n], "name": name if name.lower().startswith("espink") else f"ESPink {name}",
           "mf": "LaskaKit", "mdl": "ESPink", "cns": [["mac", cfg["mac"].lower()]]}
    if fw:
        dev["sw"] = fw
    cmps = {}
    for key, (title, extra) in SENSORS.items():
        c = {"p": "sensor", "uniq_id": f"{n}_{key}", "name": title, "val_tpl": f"{{{{ value_json.{key} }}}}", **extra}
        if key == "battery":
            c["json_attr_t"] = state_topic(cfg["mac"])
            c["json_attr_tpl"] = "{{ {'ip': value_json.ip, 'mac': value_json.mac, 'firmware': value_json.firmware} | tojson }}"
            if cfg.get("battery_sensor"):
                c["default_entity_id"] = cfg["battery_sensor"]
        cmps[key] = c
    return {"dev": dev, "o": ORIGIN, "stat_t": state_topic(cfg["mac"]), "cmps": cmps, "qos": 1}


class Mqtt:
    """Tenký obal nad paho-mqtt: vlastní vlákno, automatické znovupřipojení, publikování retained."""

    def __init__(self, host: str, port: int, user: str = "", password: str = "", on_connect=None):
        import paho.mqtt.client as paho  # jen když je MQTT nastavené
        self.connected = threading.Event()
        self.on_connect = on_connect
        try:
            self.client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id="espink_server")
        except AttributeError:  # paho-mqtt 1.x (balíček v Alpine 3.21)
            self.client = paho.Client(client_id="espink_server")
        if user:
            self.client.username_pw_set(user, password)
        self.client.on_connect = self._connected
        self.client.on_disconnect = self._disconnected
        self.client.reconnect_delay_set(1, 60)
        self.target = f"{host}:{port}"
        self.client.connect_async(host, port, keepalive=60)
        self.client.loop_start()

    def _connected(self, client, userdata, flags, reason, properties=None):
        # 2.x: ReasonCode, 1.x: číslo (0 = v pořádku)
        if (reason.is_failure if hasattr(reason, "is_failure") else reason != 0):
            print(f"[mqtt] připojení k {self.target} odmítnuto: {reason}")
            return
        print(f"[mqtt] připojeno k {self.target}")
        self.connected.set()
        if self.on_connect:
            self.on_connect()

    def _disconnected(self, client, userdata, *args):
        reason = args[1] if len(args) >= 2 else args[0]  # 2.x: (flags, reason, properties), 1.x: (rc,)
        if self.connected.is_set():
            print(f"[mqtt] odpojeno ({reason}), připojím se znovu")
        self.connected.clear()

    def publish(self, topic: str, payload, retain: bool = True) -> None:
        """QoS 1: při výpadku spojení paho zprávu podrží a pošle po znovupřipojení."""
        data = payload if isinstance(payload, (str, bytes)) else json.dumps(payload, ensure_ascii=False)
        self.client.publish(topic, data, qos=1, retain=retain)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


async def connect(ha, on_connect) -> Mqtt | None:
    """Broker z MQTT_HOST (vývoj), jinak od Supervisoru. Bez brokeru None = zápis přes REST."""
    cfg = None
    if os.environ.get("MQTT_HOST"):
        cfg = {"host": os.environ["MQTT_HOST"], "port": int(os.environ.get("MQTT_PORT", 1883)),
               "username": os.environ.get("MQTT_USER", ""), "password": os.environ.get("MQTT_PASSWORD", "")}
    elif ha:
        try:
            cfg = await ha.mqtt_service()
        except Exception as e:  # noqa: BLE001
            print(f"[mqtt] nelze zjistit broker: {e}")
    if not cfg:
        print("[mqtt] broker není k dispozici – stav panelů se zapisuje přes REST API")
        return None
    try:
        return Mqtt(cfg["host"], int(cfg.get("port") or 1883), cfg.get("username") or "", cfg.get("password") or "",
                    on_connect)
    except Exception as e:  # noqa: BLE001
        print(f"[mqtt] nelze spustit klienta: {e}")
        return None
