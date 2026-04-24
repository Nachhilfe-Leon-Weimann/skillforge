import pytest


@pytest.mark.db
async def test_base_table_args_extending():
    from sqlalchemy import Integer, UniqueConstraint
    from sqlalchemy.orm import Mapped, mapped_column

    from skillcore.db.models.base import Base

    class MyTestBase(Base):
        __abstract__ = True
        __table_args__ = {"schema": "test"}

    class MyTestModel(MyTestBase):
        __tablename__ = "tst"

        id: Mapped[int] = mapped_column(primary_key=True)
        value: Mapped[int] = mapped_column(Integer, nullable=False)

        __table_args__ = MyTestBase.extend_table_args(UniqueConstraint("value", name="uq_value"))

    model = MyTestModel(value=1)

    assert model.__table__.schema == "test"
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_value"
        and tuple(constraint.columns.keys()) == ("value",)
        for constraint in MyTestModel.__table__.constraints  # type: ignore
    )
