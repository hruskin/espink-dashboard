"""Emoji v textech (názvy událostí, popisky…) -> plné ikony Material Design Icons.

Černobílý font emoji (Noto Emoji) kreslí řadu znaků jen obrysem (👟, ♻️, ⚠️…), na
e-paperu to vypadá nejednotně. Emoji z tabulky se proto při vykreslení nahradí znakem
ikony MDI (soukromá oblast Unicode); šablona má font MDI v záložních fontech, takže
ikona má velikost a barvu okolního textu. Emoji mimo tabulku zůstanou z Noto Emoji.
Text v kalendáři se nemění. Tabulku jde libovolně doplnit (název ikony = mdi-…).
"""
import re

# emoji (bez variačního selektoru FE0F a odstínu pleti) -> název ikony MDI
TABLE = {
 # sport a pohyb
 "👟": "shoe-sneaker", "🏃": "run", "🚶": "walk", "🤸": "gymnastics", "🏊": "swim", "🚴": "bike", "🚲": "bicycle",
 "⚽": "soccer", "🏀": "basketball", "🏐": "volleyball", "🎾": "tennis", "🏓": "table-tennis", "🏸": "badminton",
 "🏒": "hockey-sticks", "⛸": "skate", "⛷": "ski", "🎿": "ski", "🏂": "snowboard", "🧘": "yoga", "🏋": "weight-lifter",
 "🤾": "handball", "🥋": "karate", "🏇": "horse-human", "🧗": "carabiner", "🛹": "skateboard", "🏆": "trophy",
 "🏅": "medal", "🥇": "podium-gold", "🎯": "bullseye-arrow", "⛳": "golf", "🎳": "bowling", "🛶": "rowing",
 # škola, kultura, zájmy
 "🎓": "school", "🏫": "school-outline", "📚": "bookshelf", "📖": "book-open-variant", "✏": "pencil", "📝": "note-edit",
 "🖍": "draw", "🎨": "palette", "🎭": "drama-masks", "🎬": "movie-open", "🎤": "microphone-variant", "🎵": "music",
 "🎶": "music", "🎹": "piano", "🎸": "guitar-acoustic", "🎻": "violin", "🎺": "trumpet", "🎮": "gamepad-variant",
 "🧩": "puzzle", "📷": "camera", "📸": "camera", "🔬": "microscope", "🧪": "flask", "💻": "laptop", "📱": "cellphone",
 "📞": "phone", "☎": "phone-classic", "✉": "email", "📧": "email", "📦": "package-variant-closed", "📮": "mailbox",
 # rodina, oslavy
 "🎂": "cake-variant", "🍰": "cake", "🎁": "gift", "🎉": "party-popper", "🎊": "party-popper", "🎈": "balloon",
 "🥳": "party-popper", "❤": "heart", "♥": "heart", "💕": "heart-multiple", "💍": "ring", "👶": "baby-face", "🍼": "baby-bottle",
 "👪": "human-male-female-child", "🧸": "teddy-bear", "🎄": "pine-tree", "🐣": "egg-easter", "🥚": "egg",
 "🎃": "halloween", "🕯": "candle", "👻": "ghost", "⛪": "church", "💐": "flower", "🌹": "flower-tulip",
 # zdraví
 "🦷": "tooth", "💉": "needle", "💊": "pill", "🩺": "stethoscope", "🏥": "hospital-building", "🚑": "ambulance",
 "🤒": "emoticon-sick", "👓": "glasses", "💇": "hair-dryer",
 # domácnost, práce
 "🏠": "home", "🏡": "home-heart", "🧹": "broom", "🧺": "basket", "🛒": "cart", "🗑": "delete", "♻": "recycle",
 "💡": "lightbulb", "🔧": "wrench", "🔨": "hammer", "🛠": "tools", "🔑": "key", "🔒": "lock", "🧾": "receipt",
 "💰": "cash", "💶": "cash", "💳": "credit-card", "🏦": "bank", "💼": "briefcase", "🖨": "printer", "🧳": "bag-suitcase",
 "👕": "tshirt-crew", "👗": "hanger", "🧥": "coat-rack", "✂": "content-cut", "🧼": "hand-wash", "🚿": "shower", "🛁": "bathtub",
 "🛏": "bed", "🐶": "dog", "🐕": "dog", "🐱": "cat", "🐈": "cat", "🐴": "horse", "🐟": "fish", "🐦": "bird",
 # jídlo
 "🍕": "pizza", "🍔": "hamburger", "🍝": "pasta", "🍲": "pot-steam", "🥗": "food-apple", "🍎": "food-apple",
 "🍞": "bread-slice", "☕": "coffee", "🍷": "glass-wine", "🍺": "glass-mug-variant", "🍻": "glass-mug-variant",
 "🍴": "silverware-fork-knife", "🍽": "silverware-fork-knife", "🥂": "glass-cocktail", "🍦": "ice-cream", "🍪": "cookie",
 # doprava, cesty
 "🚗": "car", "🚙": "car-estate", "🚌": "bus", "🚎": "bus-electric", "🚆": "train", "🚂": "train", "🚇": "subway-variant",
 "🚊": "tram", "✈": "airplane", "🛫": "airplane-takeoff", "🛬": "airplane-landing", "🚢": "ferry", "⛵": "sail-boat",
 "⛽": "gas-station", "🅿": "parking", "🚕": "taxi", "🛵": "moped", "🏍": "motorbike", "🗺": "map", "📍": "map-marker",
 "🏖": "beach", "🏝": "island", "⛺": "tent", "🏕": "campfire", "⛰": "image-filter-hdr", "🏔": "image-filter-hdr",
 "🏰": "castle", "🎡": "ferris-wheel",
 # počasí, příroda
 "☀": "weather-sunny", "🌞": "weather-sunny", "⛅": "weather-partly-cloudy", "☁": "weather-cloudy", "🌧": "weather-pouring",
 "⛈": "weather-lightning-rainy", "❄": "snowflake", "☃": "snowman", "⛄": "snowman", "🌈": "looks", "🌙": "weather-night",
 "⭐": "star", "🌟": "star", "✨": "creation", "🌲": "pine-tree", "🌳": "tree", "🌸": "flower", "🌷": "flower-tulip",
 "🍂": "leaf", "🍁": "leaf-maple", "🌱": "sprout", "💧": "water", "🔥": "fire", "⚡": "flash",
 # značky a symboly
 "⚠": "alert", "❗": "exclamation-thick", "❕": "exclamation", "‼": "exclamation-thick", "❓": "help", "❔": "help",
 "✅": "check-bold", "✔": "check-bold", "☑": "checkbox-marked", "❌": "close-thick", "✖": "close-thick", "⛔": "minus-circle",
 "🚫": "cancel", "📌": "pin", "📎": "paperclip", "📅": "calendar", "📆": "calendar", "🗓": "calendar", "⏰": "alarm",
 "⏱": "timer", "⌛": "timer-sand", "⏳": "timer-sand", "🕐": "clock-outline", "🔔": "bell", "🔕": "bell-off",
 "📣": "bullhorn", "📢": "bullhorn", "ℹ": "information", "🆕": "new-box", "🆗": "check-bold", "🔴": "circle", "⚫": "circle",
 "🟢": "circle", "🔵": "circle", "🟡": "circle", "⭕": "circle-outline", "➡": "arrow-right-bold", "⬅": "arrow-left-bold",
 "⬆": "arrow-up-bold", "⬇": "arrow-down-bold", "👍": "thumb-up", "👎": "thumb-down", "👋": "hand-wave", "🙏": "hands-pray",
 "💪": "arm-flex", "👀": "eye", "🎫": "ticket", "🎟": "ticket", "🏛": "bank", "🏢": "office-building", "🏭": "factory",
 "🗳": "vote", "⚖": "scale-balance", "👮": "police-badge", "🚒": "fire-truck", "🛡": "shield",
}


# emoji + modifikátory (FE0E/FE0F, odstín pleti) + případné spojení ZWJ (🏃‍♀️ = 🏃)
_MODS = "\ufe0e\ufe0f\U0001F3FB-\U0001F3FF"
EMOJI_RE = re.compile(rf"([^\s\w])([{_MODS}]*(?:\u200d[^\s][{_MODS}]*)*)")
SKIP_KEYS = {"type", "id", "size", "align", "side", "html"}


def icon_chars(codes: dict[str, int]) -> dict[str, str]:
    """Emoji -> znak ikony podle kódů z CSS MDI (neznámé názvy se vynechají)."""
    return {e: chr(codes[n]) for e, n in TABLE.items() if n in codes}


def replace(text: str, chars: dict[str, str]) -> str:
    return EMOJI_RE.sub(lambda m: chars.get(m.group(1), m.group(0)), text)


def apply(obj, chars: dict[str, str]):
    """Kopie modelu zobrazení s nahrazenými emoji (kromě názvů ikon a technických klíčů)."""
    if isinstance(obj, dict):
        return {k: v if k in SKIP_KEYS or k.endswith("icon") else apply(v, chars) for k, v in obj.items()}
    if isinstance(obj, list):
        return [apply(x, chars) for x in obj]
    if isinstance(obj, str):
        return replace(obj, chars)
    return obj
