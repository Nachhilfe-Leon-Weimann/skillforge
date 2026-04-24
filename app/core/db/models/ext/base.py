from ..base import Base


class ExtBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "ext"}
