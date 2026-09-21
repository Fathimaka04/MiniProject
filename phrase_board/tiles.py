"""
GazeAssist - Tile data model, categories, and default phrase set.

Each tile has text, translations in 4 Indian languages, category,
colour, and an emoji icon.  Navigation tiles link to sub-pages.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Category(Enum):
    CORE = auto()
    MORE = auto()          # category-picker page (Medical / Social / Emergency)
    MEDICAL = auto()
    SOCIAL = auto()
    EMERGENCY = auto()
    NAVIGATION = auto()   # virtual category for nav tiles on home page

@dataclass
class Tile:
    id: str
    text: str
    category: Category
    translations: dict  # {'hi': '...', 'ml': '...', 'ta': '...', 'te': '...'}
    color: str = "#4A90D9"
    emoji: str = "💬"
    is_navigation: bool = False
    target_category: Optional[Category] = None  # set for nav tiles
    is_emergency: bool = False  # selecting this tile fires the SOS alert system

def create_default_tiles() -> dict[Category, list[Tile]]:
    """
    Return the full tile set organised by category.

    Home page (CORE) shows FOOD / WATER / BATHROOM / HELP plus navigation
    tiles to MEDICAL, SOCIAL, EMERGENCY, and a PAIN SCALE entry point.
    """
    tiles: dict[Category, list[Tile]] = {c: [] for c in Category}

    # ── CORE (home page) ─────────────────────────────────────────────
    tiles[Category.CORE] = [
        Tile("food", "FOOD", Category.CORE,
             {"hi": "खाना", "ml": "ഭക്ഷണം", "ta": "உணவு", "te": "ఆహారం"},
             "#FF9800", "🍽️"),
        Tile("water", "WATER", Category.CORE,
             {"hi": "पानी", "ml": "വെള്ളം", "ta": "தண்ணீர்", "te": "నీళ్ళు"},
             "#2196F3", "💧"),
        Tile("bathroom", "BATHROOM", Category.CORE,
             {"hi": "बाथरूम", "ml": "ബാത്ത്‌റൂം", "ta": "கழிவறை", "te": "బాత్రూమ్"},
             "#9C27B0", "🚻"),
                Tile("help", "HELP", Category.CORE,
             {"hi": "मदद", "ml": "സഹായം", "ta": "உதவி", "te": "సహాయం"},
             "#F44336", "🆘"),
        # Navigation tiles — home page only holds Pain Scale + More (6 tiles total)
        Tile("nav_pain", "PAIN SCALE", Category.NAVIGATION,
             {"hi": "दर्द स्तर", "ml": "വേദന സ്കെയിൽ", "ta": "வலி அளவு", "te": "నొప్పి స్కేల్"},
             "#FF5722", "😣", is_navigation=True, target_category=None),  # special: triggers PAIN mode
        Tile("nav_more", "MORE", Category.NAVIGATION,
             {"hi": "और", "ml": "കൂടുതൽ", "ta": "மேலும்", "te": "మరిన్ని"},
             "#607D8B", "➕", is_navigation=True, target_category=Category.MORE),
    ]

    # ── MORE (category picker) ───────────────────────────────────────
    tiles[Category.MORE] = [
        Tile("nav_medical", "MEDICAL", Category.NAVIGATION,
             {"hi": "चिकित्सा", "ml": "മെഡിക്കൽ", "ta": "மருத்துவம்", "te": "వైద్యం"},
             "#E91E63", "🏥", is_navigation=True, target_category=Category.MEDICAL),
        Tile("nav_social", "SOCIAL", Category.NAVIGATION,
             {"hi": "सामाजिक", "ml": "സാമൂഹിക", "ta": "சமூக", "te": "సామాజిక"},
             "#00BCD4", "💬", is_navigation=True, target_category=Category.SOCIAL),
        Tile("nav_emergency", "EMERGENCY", Category.NAVIGATION,
             {"hi": "आपातकालीन", "ml": "അടിയന്തിരം", "ta": "அவசரம்", "te": "అత్యవసరం"},
             "#D32F2F", "🚨", is_navigation=True, target_category=Category.EMERGENCY),
    ]
    

    # ── MEDICAL ──────────────────────────────────────────────────────
    tiles[Category.MEDICAL] = [
        Tile("pain", "PAIN", Category.MEDICAL,
             {"hi": "दर्द", "ml": "വേദന", "ta": "வலி", "te": "నొప్పి"},
             "#E53935", "😖"),
        Tile("nausea", "NAUSEA", Category.MEDICAL,
             {"hi": "मतली", "ml": "ഓക്കാനം", "ta": "குமட்டல்", "te": "వికారం"},
             "#8BC34A", "🤢"),
        Tile("dizzy", "DIZZY", Category.MEDICAL,
             {"hi": "चक्कर", "ml": "തലചുറ്റൽ", "ta": "தலைசுற்றல்", "te": "తలతిరుగుడు"},
             "#FFC107", "😵"),
        Tile("call_nurse", "CALL NURSE", Category.MEDICAL,
             {"hi": "नर्स बुलाओ", "ml": "നഴ്‌സിനെ വിളിക്കൂ", "ta": "செவிலியரை அழையுங்கள்", "te": "నర్సును పిలవండి"},
             "#3F51B5", "👩‍⚕️"),
        Tile("medicine", "MEDICINE", Category.MEDICAL,
             {"hi": "दवाई", "ml": "മരുന്ന്", "ta": "மருந்து", "te": "మందు"},
             "#009688", "💊"),
    ]

    # ── SOCIAL ───────────────────────────────────────────────────────
    tiles[Category.SOCIAL] = [
        Tile("yes", "YES", Category.SOCIAL,
             {"hi": "हाँ", "ml": "അതെ", "ta": "ஆம்", "te": "అవును"},
             "#4CAF50", "✅"),
        Tile("no", "NO", Category.SOCIAL,
             {"hi": "नहीं", "ml": "ഇല്ല", "ta": "இல்லை", "te": "లేదు"},
             "#F44336", "❌"),
        Tile("thank_you", "THANK YOU", Category.SOCIAL,
             {"hi": "धन्यवाद", "ml": "നന്ദി", "ta": "நன்றி", "te": "ధన్యవాదాలు"},
             "#E91E63", "🙏"),
        Tile("call_family", "CALL FAMILY", Category.SOCIAL,
             {"hi": "परिवार बुलाओ", "ml": "കുടുംബത്തെ വിളിക്കൂ", "ta": "குடும்பத்தை அழையுங்கள்", "te": "కుటుంబాన్ని పిలవండి"},
             "#673AB7", "👨‍👩‍👧"),
        Tile("i_am_okay", "I AM OKAY", Category.SOCIAL,
             {"hi": "मैं ठीक हूँ", "ml": "ഞാൻ സുഖമാണ്", "ta": "நான் நலமாக இருக்கிறேன்", "te": "నేను బాగున్నాను"},
             "#8BC34A", "👍"),
    ]

    # ── EMERGENCY ────────────────────────────────────────────────────
    tiles[Category.EMERGENCY] = [
        Tile("call_doctor", "CALL DOCTOR", Category.EMERGENCY,
             {"hi": "डॉक्टर बुलाओ", "ml": "ഡോക്ടറെ വിളിക്കൂ", "ta": "மருத்துவரை அழையுங்கள்", "te": "డాక్టర్‌ను పిలవండి"},
             "#D32F2F", "👨‍⚕️", is_emergency=True),
        Tile("cannot_breathe", "I CANNOT BREATHE", Category.EMERGENCY,
             {"hi": "मैं साँस नहीं ले पा रहा", "ml": "എനിക്ക് ശ്വസിക്കാൻ കഴിയുന്നില്ല", "ta": "என்னால் மூச்சு விட முடியவில்லை", "te": "నాకు ఊపిరి ఆడటం లేదు"},
             "#B71C1C", "😤", is_emergency=True),
        Tile("falling", "I AM FALLING", Category.EMERGENCY,
             {"hi": "मैं गिर रहा हूँ", "ml": "ഞാൻ വീഴുകയാണ്", "ta": "நான் விழுகிறேன்", "te": "నేను పడిపోతున్నాను"},
             "#E65100", "⚠️", is_emergency=True),
    ]
    return tiles


def get_tile_text(tile: Tile, language: str) -> str:
    """Get the tile text in the specified language, fallback to English."""
    return tile.translations.get(language, tile.text)
