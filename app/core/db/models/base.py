from typing import Any, cast

from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.schema import SchemaItem

type TableArgs = tuple[Any, ...] | dict[str, Any]


class Base(DeclarativeBase):
    __abstract__ = True

    @classmethod
    def extend_table_args(
        cls,
        *items: SchemaItem,
        **options: Any,
    ) -> TableArgs:
        inherited_items, inherited_options = cls._split_table_args(getattr(cls, "__table_args__", None))
        merged_items = (*inherited_items, *items)
        merged_options = {**inherited_options, **options}

        if merged_options:
            return (*merged_items, merged_options)

        return merged_items

    @staticmethod
    def _split_table_args(table_args: TableArgs | None) -> tuple[tuple[SchemaItem, ...], dict[str, Any]]:
        if table_args is None:
            return (), {}

        if isinstance(table_args, dict):
            return (), dict(table_args)

        if table_args and isinstance(table_args[-1], dict):
            return cast(tuple[SchemaItem, ...], table_args[:-1]), dict(table_args[-1])

        return cast(tuple[SchemaItem, ...], table_args), {}


__all__ = [
    "Base",
]
