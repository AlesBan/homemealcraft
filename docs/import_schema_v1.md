# HomeMealCraft import schema v1.0

Пакет действий для бота. БЖУ всегда **на 100 г**.  
Блюда ссылаются только на **ингредиенты**, не на конкретные продукты.

## Конверт

```json
{
  "schema_version": "1.0",
  "idempotency_key": "unique-key-for-this-packet",
  "source": "deepseek",
  "actions": []
}
```

- Повтор с тем же `idempotency_key` и тем же содержимым → `already_applied`.
- Тот же ключ, **другое** содержимое → `conflict` (нужен новый ключ).
- Новый ключ + тот же barcode/id → patch (не-null поля обновляются, null не затирает; `clear_fields` затирает явно).

## Ops

| op | Описание |
|----|----------|
| `ingredient.upsert` | Абстрактный ингредиент (Творог) |
| `product.upsert` | Конкретный товар (бренд + barcode) → ingredient |
| `dish.upsert` | Блюдо; `ingredients` всегда создаёт **новую версию** рецепта |
| `dish_version.create` | Явная новая версия |
| `dish_version.delete` | Удаление версии |
| `delivery.upsert` | Доставка: ссылка / где что заказывать + комментарий |
| `stock.upsert` | Кладовая: что есть дома (вид / имя) |
| `stock.remove` | Убрать из кладовой |
| `shopping.add` | В список покупок |
| `shopping.check` | Отметить купленным |
| `shopping.remove` | Удалить из списка |

Алиасы: `*.create` = `*.upsert`.

## Матчинг

- Продукт: `id` → `barcode` → `name`
- Ингредиент: `id` → `name`
- Блюдо: `id` → иначе `name` (имя уникально; повтор + состав = новая версия)
- Доставка: `id` → иначе `name`
- Заготовка: то же что блюдо с `category: "prep"`

## Пример

См. `docs/ai_agent_rules.md` и пример сырников в плане.
