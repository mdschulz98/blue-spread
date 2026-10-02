from sqlalchemy import Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        # Case-insensitive uniqueness. Emails are also stored lower-cased by the service layer.
        Index("uq_users_email_lower", func.lower(text("email")), unique=True),
    )

    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    is_superuser: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email!r}>"
