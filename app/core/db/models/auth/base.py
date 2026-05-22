from ..base import Base


class AuthBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "auth"}
