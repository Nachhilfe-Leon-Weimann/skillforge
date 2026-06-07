from ..base import Base


class SystemBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "system"}
