"""Suggested ingredient (product type) names and grocery categories.

Unknown types are still allowed: the import engine creates a new ingredient.
This list is a hint for the AI agent and for auto-filling `category` when possible.
"""

from __future__ import annotations

# High-level shelves (ingredient.category / shopping.category)
INGREDIENT_CATEGORIES: tuple[str, ...] = (
    "dairy",
    "meat",
    "fish",
    "eggs",
    "vegetables",
    "fruit",
    "grocery",
    "bakery",
    "frozen",
    "drinks",
    "sauce",
    "spice",
    "oil",
    "sweet",
    "snack",
    "household",
    "other",
)

# Abstract product types → category. Name is what appears as «Тип» on a product card.
TYPE_TO_CATEGORY: dict[str, str] = {
    # dairy
    "Молоко": "dairy",
    "Сливки": "dairy",
    "Сметана": "dairy",
    "Кефир": "dairy",
    "Ряженка": "dairy",
    "Йогурт": "dairy",
    "Творог": "dairy",
    "Творожок": "dairy",
    "Сырок": "dairy",
    "Сыр": "dairy",
    "Масло сливочное": "dairy",
    "Маргарин": "dairy",
    # eggs
    "Яйцо": "eggs",
    # meat
    "Курица": "meat",
    "Индейка": "meat",
    "Говядина": "meat",
    "Свинина": "meat",
    "Фарш": "meat",
    "Бекон": "meat",
    "Колбаса": "meat",
    "Сосиски": "meat",
    "Ветчина": "meat",
    # fish
    "Рыба": "fish",
    "Лосось": "fish",
    "Сельдь": "fish",
    "Тунец": "fish",
    "Креветки": "fish",
    # vegetables
    "Овощи": "vegetables",
    "Картофель": "vegetables",
    "Лук": "vegetables",
    "Чеснок": "vegetables",
    "Морковь": "vegetables",
    "Капуста": "vegetables",
    "Помидор": "vegetables",
    "Огурец": "vegetables",
    "Перец": "vegetables",
    "Салат": "vegetables",
    "Зелень": "vegetables",
    "Грибы": "vegetables",
    # fruit
    "Фрукты": "fruit",
    "Яблоко": "fruit",
    "Банан": "fruit",
    "Ягоды": "fruit",
    "Лимон": "fruit",
    # grocery / dry
    "Мука": "grocery",
    "Сахар": "grocery",
    "Соль": "grocery",
    "Рис": "grocery",
    "Гречка": "grocery",
    "Овсянка": "grocery",
    "Паста": "grocery",
    "Макароны": "grocery",
    "Крупа": "grocery",
    "Бобовые": "grocery",
    "Чечевица": "grocery",
    "Нут": "grocery",
    "Консервы": "grocery",
    "Томатная паста": "grocery",
    # bakery
    "Хлеб": "bakery",
    "Лаваш": "bakery",
    "Тортилья": "bakery",
    "Выпечка": "bakery",
    # frozen / ready
    "Заморозка": "frozen",
    "Пельмени": "frozen",
    "Вареники": "frozen",
    "Мороженое": "frozen",
    "Полуфабрикат": "frozen",
    # drinks
    "Вода": "drinks",
    "Сок": "drinks",
    "Чай": "drinks",
    "Кофе": "drinks",
    "Напиток": "drinks",
    # sauce / oil / spice
    "Соус": "sauce",
    "Майонез": "sauce",
    "Кетчуп": "sauce",
    "Горчица": "sauce",
    "Масло растительное": "oil",
    "Оливковое масло": "oil",
    "Специи": "spice",
    "Перец чёрный": "spice",
    "Паприка": "spice",
    "Корица": "spice",
    # sweet / snack
    "Шоколад": "sweet",
    "Печенье": "sweet",
    "Конфеты": "sweet",
    "Мёд": "sweet",
    "Варенье": "sweet",
    "Орехи": "snack",
    "Чипсы": "snack",
    "Перекус": "snack",
    "Намазка": "snack",
    "Каша": "grocery",
    "Кашка": "grocery",
    "Филе в панировке": "frozen",
}


def normalize_type_name(name: str) -> str:
    return " ".join(name.split()).strip()


def suggest_category(type_name: str) -> str:
    key = normalize_type_name(type_name)
    if key in TYPE_TO_CATEGORY:
        return TYPE_TO_CATEGORY[key]
    # case-insensitive lookup
    lower = {k.lower(): v for k, v in TYPE_TO_CATEGORY.items()}
    return lower.get(key.lower(), "other")


def canonical_type_name(name: str) -> str | None:
    """Return catalog spelling if this type is known (any case), else None."""
    key = normalize_type_name(name)
    for known in TYPE_TO_CATEGORY:
        if known.lower() == key.lower():
            return known
    return None
