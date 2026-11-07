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

- Повтор с тем же `idempotency_key` не применяется повторно.
- Новый ключ + тот же barcode/id → patch (не-null поля обновляются, null не затирает).

## Ops

| op | Описание |
|----|----------|
| `ingredient.upsert` | Абстрактный ингредиент (Творог) |
| `product.upsert` | Конкретный товар (бренд + barcode) → ingredient |
| `dish.upsert` | Блюдо + состав из ингредиентов |
| `shopping.add` | В список покупок |
| `shopping.check` | Отметить купленным |
| `shopping.remove` | Удалить из списка |

Алиасы: `*.create` = `*.upsert`.

## Матчинг

- Продукт: `id` → `barcode` → `name`
- Ингредиент: `id` → `name`
- Блюдо: `id` → иначе `name` (только если записи ещё нет)

## Пример

См. `docs/ai_agent_rules.md` и пример сырников в плане.
