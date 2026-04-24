from ..base import Base


class GeoBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "geo"}
