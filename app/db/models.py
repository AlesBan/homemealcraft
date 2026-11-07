from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    products: Mapped[list[Product]] = relationship(back_populates="ingredient")
    dish_links: Mapped[list[DishIngredient]] = relationship(back_populates="ingredient")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ingredient_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ingredients.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    fat_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    composition: Mapped[str | None] = mapped_column(Text, nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_scan_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    ingredient: Mapped[Ingredient | None] = relationship(back_populates="products")
    shopping_items: Mapped[list[ShoppingItem]] = relationship(back_populates="product")


class Dish(Base):
    __tablename__ = "dishes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cook_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    servings: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    freezable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    ingredients: Mapped[list[DishIngredient]] = relationship(
        back_populates="dish", cascade="all, delete-orphan"
    )
    versions: Mapped[list[DishVersion]] = relationship(
        back_populates="dish",
        cascade="all, delete-orphan",
        foreign_keys="DishVersion.dish_id",
        order_by="DishVersion.created_at.desc()",
    )


class DishIngredient(Base):
    __tablename__ = "dish_ingredients"
    __table_args__ = (UniqueConstraint("dish_id", "ingredient_id", name="uq_dish_ingredient"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dish_id: Mapped[str] = mapped_column(String(36), ForeignKey("dishes.id"), nullable=False)
    ingredient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ingredients.id"), nullable=False
    )
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(16), default="g")
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    dish: Mapped[Dish] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient] = relationship(back_populates="dish_links")


class DishVersion(Base):
    """One cook/recipe attempt for a dish. History is kept; cleanup is manual later."""

    __tablename__ = "dish_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dish_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dishes.id"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cook_time_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    servings: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    kcal: Mapped[float | None] = mapped_column(Float, nullable=True)
    protein_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    fat_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbs_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    dish: Mapped[Dish] = relationship(
        back_populates="versions", foreign_keys=[dish_id]
    )
    ingredients: Mapped[list[DishVersionIngredient]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class DishVersionIngredient(Base):
    __tablename__ = "dish_version_ingredients"
    __table_args__ = (
        UniqueConstraint("version_id", "ingredient_id", name="uq_dish_version_ingredient"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dish_versions.id"), nullable=False
    )
    ingredient_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ingredients.id"), nullable=False
    )
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(16), default="g")
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    version: Mapped[DishVersion] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient] = relationship()


class ShoppingItem(Base):
    __tablename__ = "shopping_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    product_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("products.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_bought: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    product: Mapped[Product | None] = relationship(back_populates="shopping_items")


class ImportEvent(Base):
    __tablename__ = "import_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="applied")
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
