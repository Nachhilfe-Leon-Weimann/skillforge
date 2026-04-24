from ..base import Base


class CoreBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "core"}
