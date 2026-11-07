from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

OP_ALIASES = {
    "ingredient.create": "ingredient.upsert",
    "product.create": "product.upsert",
    "dish.create": "dish.upsert",
    "dish_version.add": "dish_version.create",
}


class NutritionMixin(BaseModel):
    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None


class DishIngredientLine(BaseModel):
    ingredient_id: str | None = None
    ingredient_name: str | None = None
    amount: float | None = None
    unit: str = "g"
    note: str | None = None

    @model_validator(mode="after")
    def require_ref(self) -> DishIngredientLine:
        if not self.ingredient_id and not self.ingredient_name:
            raise ValueError("ingredient_id or ingredient_name is required")
        return self


class IngredientUpsert(NutritionMixin):
    op: Literal["ingredient.upsert"] = "ingredient.upsert"
    id: str | None = None
    name: str
    category: str | None = None
    comments: str | None = None
    clear_fields: list[str] = Field(default_factory=list)


class ProductUpsert(NutritionMixin):
    op: Literal["product.upsert"] = "product.upsert"
    id: str | None = None
    ingredient_id: str | None = None
    ingredient_name: str | None = None
    name: str
    brand: str | None = None
    barcode: str | None = None
    fat_percent: float | None = None
    weight_g: float | None = None
    unit_label: str | None = None
    composition: str | None = None
    manufacturer: str | None = None
    comments: str | None = None
    raw_scan_json: Any | None = None
    clear_fields: list[str] = Field(default_factory=list)


class DishUpsert(NutritionMixin):
    op: Literal["dish.upsert"] = "dish.upsert"
    id: str | None = None
    name: str
    category: str | None = None
    description: str | None = None
    cook_time_min: int | None = None
    servings: int | None = None
    instructions: str | None = None
    comments: str | None = None
    freezable: bool | None = None
    ingredients: list[DishIngredientLine] | None = None
    # If true (or use dish_version.create) — always append a new recipe version
    new_version: bool = False
    result_comment: str | None = None
    clear_fields: list[str] = Field(default_factory=list)


class DishVersionCreate(NutritionMixin):
    """New recipe attempt. User never sends version numbers — system appends history."""

    op: Literal["dish_version.create"] = "dish_version.create"
    id: str | None = None
    dish_id: str | None = None
    dish_name: str | None = None
    title: str | None = None
    description: str | None = None
    cook_time_min: int | None = None
    servings: int | None = None
    instructions: str | None = None
    result_comment: str | None = None
    ingredients: list[DishIngredientLine] = Field(default_factory=list)
    set_current: bool = True

    @model_validator(mode="after")
    def require_dish_ref(self) -> DishVersionCreate:
        if not self.dish_id and not self.dish_name:
            raise ValueError("dish_id or dish_name is required")
        return self


class DishVersionDelete(BaseModel):
    """Manual cleanup of one version later — never auto-purge."""

    op: Literal["dish_version.delete"] = "dish_version.delete"
    version_id: str | None = None
    dish_name: str | None = None
    # If version_id omitted: delete non-current versions matching result_comment contains?
    # Keep simple: version_id required OR dish_name + keep_current_only
    keep_current_only: bool = False

    @model_validator(mode="after")
    def require_target(self) -> DishVersionDelete:
        if self.version_id:
            return self
        if self.keep_current_only and self.dish_name:
            return self
        raise ValueError("version_id or (dish_name + keep_current_only) required")


class ShoppingAdd(BaseModel):
    op: Literal["shopping.add"] = "shopping.add"
    product_id: str | None = None
    name: str | None = None
    category: str | None = None

    @model_validator(mode="after")
    def require_name_or_product(self) -> ShoppingAdd:
        if not self.product_id and not self.name:
            raise ValueError("product_id or name is required")
        return self


class ShoppingCheck(BaseModel):
    op: Literal["shopping.check"] = "shopping.check"
    name: str | None = None
    product_id: str | None = None
    shopping_id: str | None = None


class ShoppingRemove(BaseModel):
    op: Literal["shopping.remove"] = "shopping.remove"
    name: str | None = None
    product_id: str | None = None
    shopping_id: str | None = None


Action = (
    IngredientUpsert
    | ProductUpsert
    | DishUpsert
    | DishVersionCreate
    | DishVersionDelete
    | ShoppingAdd
    | ShoppingCheck
    | ShoppingRemove
)

_ACTION_BY_OP: dict[str, type[BaseModel]] = {
    "ingredient.upsert": IngredientUpsert,
    "product.upsert": ProductUpsert,
    "dish.upsert": DishUpsert,
    "dish_version.create": DishVersionCreate,
    "dish_version.delete": DishVersionDelete,
    "shopping.add": ShoppingAdd,
    "shopping.check": ShoppingCheck,
    "shopping.remove": ShoppingRemove,
}


def parse_action(raw: dict[str, Any]) -> Action:
    op = raw.get("op")
    if not isinstance(op, str):
        raise ValueError("action.op is required")
    op = OP_ALIASES.get(op, op)
    model = _ACTION_BY_OP.get(op)
    if model is None:
        raise ValueError(f"unsupported op: {op}")
    data = {**raw, "op": op}
    return model.model_validate(data)  # type: ignore[return-value]


class ImportPacket(BaseModel):
    schema_version: Literal["1.0"]
    idempotency_key: str
    source: str | None = None
    actions: list[Action]

    @field_validator("idempotency_key")
    @classmethod
    def non_empty_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("idempotency_key must not be empty")
        return value

    @model_validator(mode="before")
    @classmethod
    def normalize_actions(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        actions = data.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions must not be empty")
        parsed: list[Action] = []
        for i, item in enumerate(actions):
            if not isinstance(item, dict):
                raise ValueError(f"actions[{i}] must be an object")
            try:
                parsed.append(parse_action(item))
            except Exception as exc:  # noqa: BLE001 — surface index in error
                raise ValueError(f"actions[{i}]: {exc}") from exc
        return {**data, "actions": parsed}


packet_adapter = TypeAdapter(ImportPacket)
