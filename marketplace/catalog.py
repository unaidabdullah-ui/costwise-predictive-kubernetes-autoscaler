"""The COSTWISE MARKETPLACE product catalogue (original demo data, stored locally).

48 products in 8 categories, prices in Indian rupees. All names and brands are
invented for the demo; product artwork is drawn by the storefront in SVG from
the ``art`` field, so no third-party images or logos are used and no internet
access is needed. The catalogue is deterministic (seeded), so every pod serves
exactly the same data.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

CATEGORIES: list[dict[str, str]] = [
    {"id": "electronics", "name": "Electronics", "icon": "chip", "hue": "212"},
    {"id": "mobiles", "name": "Mobiles & Tablets", "icon": "phone", "hue": "250"},
    {"id": "home", "name": "Home & Kitchen", "icon": "home", "hue": "28"},
    {"id": "fashion", "name": "Fashion", "icon": "shirt", "hue": "330"},
    {"id": "books", "name": "Books", "icon": "book", "hue": "160"},
    {"id": "sports", "name": "Sports & Fitness", "icon": "ball", "hue": "100"},
    {"id": "beauty", "name": "Beauty & Care", "icon": "drop", "hue": "300"},
    {"id": "grocery", "name": "Grocery", "icon": "leaf", "hue": "130"},
]

# (name, brand, base price ₹, art shape, short description)
_PRODUCTS: dict[str, list[tuple[str, str, int, str, str]]] = {
    "electronics": [
        ("Aurora Wireless Earbuds", "Sonique", 2499, "earbuds", "Active noise cancelling, 30 h battery with case."),
        ("Nimbus 27\" QHD Monitor", "Pixelcraft", 18999, "monitor", "IPS panel, 75 Hz, slim bezels, height adjustable."),
        ("Quill Mechanical Keyboard", "Keyforge", 3999, "keyboard", "Hot-swappable switches with warm-white backlight."),
        ("Orbit Smart Speaker", "Echoform", 2999, "speaker", "360° sound, voice control, works offline for alarms."),
        ("Volt 20000 mAh Power Bank", "Ampwise", 1599, "battery", "Fast charging for phones and laptops, USB-C PD."),
        ("Lumen 4K Action Camera", "Vistaro", 12499, "camera", "Waterproof to 10 m, electronic stabilisation."),
    ],
    "mobiles": [
        ("Zephyr 5G Smartphone", "Novaline", 21999, "phone", "6.5\" AMOLED, 50 MP camera, 5000 mAh battery."),
        ("Pebble 11\" Tablet", "Slatewise", 16499, "tablet", "Great for study and streaming, 8 GB RAM."),
        ("Halo Smartwatch", "Pulsetrack", 4999, "watch", "Heart-rate, sleep and SpO2 tracking, 10-day battery."),
        ("Snap Phone Case", "Armorly", 499, "case", "Shock-absorbing corners, matte finish."),
        ("Flux 65 W Charger", "Ampwise", 1299, "charger", "GaN charger for phone, tablet and laptop."),
        ("Echo Neckband", "Sonique", 1199, "earbuds", "Magnetic earbuds, 20 h playback, quick charge."),
    ],
    "home": [
        ("Brewmaster Coffee Maker", "Kettlewise", 5499, "kettle", "12-cup drip machine with keep-warm plate."),
        ("Crisp Air Fryer 4 L", "Hearthly", 6999, "pot", "Fries, bakes and roasts with little oil."),
        ("Cloud Cotton Bedsheet Set", "Loomhaus", 1899, "sheet", "King size, 300 thread count, two pillow covers."),
        ("Steel Cookware Trio", "Hearthly", 3299, "pot", "Tri-ply stainless steel, induction friendly."),
        ("Glow Desk Lamp", "Lumio", 1499, "lamp", "Adjustable colour temperature, USB charging port."),
        ("Pure Water Bottle Set", "Hydra", 799, "bottle", "Three insulated bottles, keeps cold for 24 h."),
    ],
    "fashion": [
        ("Trailrunner Sneakers", "Stridewell", 3499, "shoe", "Breathable knit upper, cushioned sole."),
        ("Everyday Denim Jacket", "Bluestitch", 2799, "shirt", "Classic fit, washed indigo."),
        ("Linen Summer Shirt", "Breezewear", 1599, "shirt", "Relaxed fit, pure linen."),
        ("Voyager Backpack 28 L", "Packwell", 2299, "bag", "Laptop sleeve, rain cover, lifetime zips."),
        ("Classic Analog Watch", "Timbre", 3999, "watch", "Sapphire glass, leather strap."),
        ("Wool Blend Scarf", "Loomhaus", 899, "scarf", "Soft, warm, three colours."),
    ],
    "books": [
        ("Systems at Scale", "Brightpage Press", 899, "book", "A practical guide to distributed systems."),
        ("The Forecasting Mind", "Brightpage Press", 649, "book", "Time-series thinking for engineers."),
        ("Cooking Without Fuss", "Leaf & Ink", 499, "book", "120 everyday Indian recipes."),
        ("Midnight Express Mysteries", "Nightowl Books", 399, "book", "Five short detective stories."),
        ("Kids' Atlas of Space", "Starbright", 799, "book", "Illustrated tour of the solar system."),
        ("Mindful Mornings Journal", "Leaf & Ink", 349, "book", "Guided journal for daily planning."),
    ],
    "sports": [
        ("Flex Yoga Mat 6 mm", "Balancia", 1199, "mat", "Non-slip, with carry strap."),
        ("Pro Badminton Racket", "Shuttlex", 2499, "racket", "Carbon frame, pre-strung."),
        ("Adjustable Dumbbells 10 kg", "Ironwise", 3999, "dumbbell", "Quick-lock plates, pair."),
        ("Stride Running Shorts", "Stridewell", 799, "shorts", "Quick-dry fabric, zip pocket."),
        ("Summit Trekking Poles", "Peakline", 1899, "pole", "Aluminium, anti-shock, pair."),
        ("Match Football Size 5", "Kickstar", 999, "ball", "Hand-stitched, all-weather."),
    ],
    "beauty": [
        ("Daily Sunscreen SPF 50", "Dermaleaf", 449, "drop", "Lightweight gel, no white cast."),
        ("Vitamin C Serum 30 ml", "Dermaleaf", 699, "drop", "Brightening serum with niacinamide."),
        ("Herbal Shampoo 400 ml", "Rootcare", 349, "bottle", "Sulphate-free, for daily use."),
        ("Beard Trimmer Pro", "Edgeline", 1899, "trimmer", "40 length settings, 90 min runtime."),
        ("Aloe Face Wash", "Rootcare", 249, "drop", "Gentle foaming cleanser."),
        ("Fragrance Mist", "Petalnote", 599, "bottle", "Fresh citrus and jasmine."),
    ],
    "grocery": [
        ("Assam Tea 1 kg", "Hillgarden", 549, "leaf", "Strong CTC tea, rich colour."),
        ("Cold-Pressed Groundnut Oil 1 L", "Purepress", 329, "bottle", "Wood-pressed, unrefined."),
        ("Basmati Rice 5 kg", "Fieldgold", 899, "sack", "Aged long-grain rice."),
        ("Roasted Almonds 500 g", "Nutfield", 649, "jar", "Lightly salted California almonds."),
        ("Organic Honey 500 g", "Beewise", 399, "jar", "Raw forest honey."),
        ("Instant Oats 1 kg", "Fieldgold", 249, "sack", "Whole-grain rolled oats."),
    ],
}


@dataclass(frozen=True)
class Product:
    id: int
    slug: str
    name: str
    brand: str
    category: str
    price: int
    mrp: int
    discount_pct: int
    rating: float
    reviews: int
    stock: int
    art: str
    hue: int
    description: str
    features: tuple[str, ...] = field(default_factory=tuple)
    deal: bool = False
    bestseller: bool = False

    def card(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("features")
        data.pop("description")
        return data

    def detail(self) -> dict[str, Any]:
        data = asdict(self)
        data["features"] = list(self.features)
        return data


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-").replace("--", "-")


@lru_cache(maxsize=1)
def products() -> tuple[Product, ...]:
    rng = random.Random(2026)
    out: list[Product] = []
    pid = 1001
    for cat in CATEGORIES:
        for name, brand, price, art, desc in _PRODUCTS[cat["id"]]:
            discount = rng.choice([0, 0, 5, 10, 15, 20, 25, 30, 40])
            mrp = int(round(price / (1 - discount / 100.0), -1)) if discount else price
            rating = round(rng.uniform(3.6, 4.9), 1)
            reviews = int(rng.lognormvariate(6.0, 1.0))
            features = (desc, f"Brand: {brand}", f"{rng.choice([1, 1, 2])}-year warranty",
                        rng.choice(["Free delivery", "Delivery in 2 days", "Cash on delivery available"]))
            out.append(Product(pid, _slug(name), name, brand, cat["id"], price, mrp, discount, rating, reviews,
                               rng.randint(3, 250), art, int(cat["hue"]) + rng.randint(-14, 14), desc, features,
                               deal=discount >= 25, bestseller=reviews > 900))
            pid += 1
    return tuple(out)


@lru_cache(maxsize=1)
def by_id() -> dict[int, Product]:
    return {p.id: p for p in products()}


def search(query: str, limit: int = 24) -> list[Product]:
    """Case-insensitive token search over name, brand, category and description (ranked)."""
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        return []
    scored = []
    for p in products():
        hay_name = p.name.lower()
        hay = f"{hay_name} {p.brand.lower()} {p.category} {p.description.lower()}"
        score = sum((3 if t in hay_name else 1) for t in tokens if t in hay)
        if score:
            scored.append((score, p.rating, p))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2].id))
    return [s[2] for s in scored[:limit]]


SEARCH_TERMS: tuple[str, ...] = ("wireless", "phone", "coffee", "shoes", "book", "yoga", "serum", "tea",
                                 "watch", "backpack", "charger", "rice", "lamp", "football", "tablet", "honey")
