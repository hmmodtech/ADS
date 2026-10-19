"""GCC market knowledge: countries, currencies, dialect guidance, FX (pegged currencies)."""
from __future__ import annotations

# Units of local currency per 1 USD. GCC currencies except KWD are hard-pegged;
# KWD floats against a basket, so treat it as approximate and set explicit prices.
USD_RATES: dict[str, float] = {
    "USD": 1.0, "SAR": 3.75, "AED": 3.6725, "QAR": 3.64,
    "BHD": 0.376, "OMR": 0.3845, "KWD": 0.307, "JOD": 0.709,
}

CURRENCY_AR = {
    "SAR": "ر.س", "AED": "د.إ", "QAR": "ر.ق", "KWD": "د.ك",
    "BHD": "د.ب", "OMR": "ر.ع", "JOD": "د.أ", "USD": "$",
}
DECIMALS = {"KWD": 3, "BHD": 3, "OMR": 3}  # others use 2

COUNTRIES: dict[str, dict] = {
    "SA": {
        "ar": "السعودية", "en": "Saudi Arabia", "currency": "SAR",
        "dialect": "اللهجة السعودية البيضاء المفهومة (نجدية/حجازية خفيفة) مثل: وش تنتظر، يا هلا، لا يفوتك",
        "openers": ["يا هلا!", "وش تنتظر؟", "لا يفوتك"],
        "cta": "اطلبه الآن",
    },
    "AE": {
        "ar": "الإمارات", "en": "United Arab Emirates", "currency": "AED",
        "dialect": "لهجة إماراتية عصرية وراقية مثل: حياك الله، سيّد الجمل، لا تفوّتها",
        "openers": ["حيّاك الله!", "جديدنا لك", "لا تفوّتها"],
        "cta": "اطلبه الحين",
    },
    "KW": {
        "ar": "الكويت", "en": "Kuwait", "currency": "KWD",
        "dialect": "لهجة كويتية خفيفة ومحببة مثل: شنو ناطر، وايد حلو، يهمّك",
        "openers": ["هلا والله!", "شنو ناطر؟", "يهمّك"],
        "cta": "اطلبه الحين",
    },
    "QA": {
        "ar": "قطر", "en": "Qatar", "currency": "QAR",
        "dialect": "لهجة قطرية خليجية راقية مثل: حيّاكم الله، شو تنتظر",
        "openers": ["حيّاكم الله!", "شو تنتظر؟", "جديدنا لكم"],
        "cta": "اطلبه الحين",
    },
    "BH": {
        "ar": "البحرين", "en": "Bahrain", "currency": "BHD",
        "dialect": "لهجة بحرينية خليجية بسيطة وودودة",
        "openers": ["هلا وغلا!", "جديدنا لك", "لا يفوتك"],
        "cta": "اطلبه الحين",
    },
    "OM": {
        "ar": "عُمان", "en": "Oman", "currency": "OMR",
        "dialect": "لهجة عُمانية/خليجية هادئة ومهذبة",
        "openers": ["مرحباً بك!", "جديدنا لك", "لا يفوتك"],
        "cta": "اطلبه الآن",
    },
}

GCC = list(COUNTRIES)


def country(code: str) -> dict:
    return COUNTRIES[code.upper()]


def to_usd(amount: float, currency: str) -> float:
    return amount / USD_RATES.get(currency.upper(), 1.0)


def from_usd(amount_usd: float, currency: str) -> float:
    return amount_usd * USD_RATES.get(currency.upper(), 1.0)


def convert(amount: float, src: str, dst: str) -> float:
    return from_usd(to_usd(amount, src), dst)


def round_price(amount: float, currency: str) -> float:
    """Retail-style rounding: whole units for 2-decimal currencies, 0.5 for 3-decimal ones."""
    if currency.upper() in DECIMALS:
        return round(amount * 2) / 2
    return float(round(amount))


def format_price(amount: float, currency: str, arabic: bool = True) -> str:
    cur = CURRENCY_AR.get(currency.upper(), currency) if arabic else currency
    dec = DECIMALS.get(currency.upper(), 2)
    txt = f"{amount:,.{dec}f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return f"{txt} {cur}"
