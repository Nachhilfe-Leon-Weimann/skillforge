from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from .base import GeoBase


class PlzOrt(GeoBase):
    __tablename__ = "plz_ort"

    plz: Mapped[str] = mapped_column(String(5), primary_key=True)
    ort: Mapped[str] = mapped_column(String, primary_key=True)
    landkreis: Mapped[str] = mapped_column(String, nullable=False)
    bundesland: Mapped[str] = mapped_column(String, nullable=False)
