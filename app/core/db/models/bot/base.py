from ..base import Base


class BotBase(Base):
    __abstract__ = True
    __table_args__ = {"schema": "bot"}
