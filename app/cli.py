"""Management commands.

    python -m app.cli create-superuser [--email E] [--display-name N] [--password P]

Missing values are prompted for (the password without echo); ``SUPERUSER_PASSWORD`` is also
honoured for non-interactive use. An existing user with that email is promoted to superuser.
"""

import argparse
import asyncio
import getpass
import os
import sys

from pydantic import TypeAdapter, ValidationError

from app.core.config import get_settings
from app.db.session import create_engine, create_sessionmaker
from app.schemas.user import UserRegister
from app.services import users as user_service


def _prompt(label: str, value: str | None) -> str:
    if value:
        return value
    if not sys.stdin.isatty():
        raise SystemExit(f"error: --{label.lower().replace(' ', '-')} is required")
    return input(f"{label}: ").strip()


def _prompt_password(value: str | None) -> str:
    if value:
        return value
    if not sys.stdin.isatty():
        raise SystemExit("error: --password or SUPERUSER_PASSWORD is required")
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("error: passwords do not match")
    return password


async def create_superuser(email: str, display_name: str, password: str) -> str:
    engine = create_engine(get_settings())
    try:
        async with create_sessionmaker(engine)() as session:
            existing = await user_service.get_user_by_email(session, email)
            if existing is not None:
                existing.is_superuser = True
                existing.is_active = True
                await session.commit()
                return f"Promoted existing user {existing.email} to superuser"
            user = await user_service.create_user(
                session,
                email=email,
                display_name=display_name,
                password=password,
                is_superuser=True,
            )
            return f"Created superuser {user.email} ({user.id})"
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    su = commands.add_parser("create-superuser", help="Create or promote a superuser")
    su.add_argument("--email")
    su.add_argument("--display-name")
    su.add_argument("--password", help="Prefer SUPERUSER_PASSWORD or the interactive prompt")
    args = parser.parse_args(argv)

    if args.command == "create-superuser":
        email = _prompt("Email", args.email)
        display_name = _prompt("Display name", args.display_name)
        password = _prompt_password(args.password or os.environ.get("SUPERUSER_PASSWORD"))
        try:
            TypeAdapter(UserRegister).validate_python(
                {"email": email, "display_name": display_name, "password": password}
            )
        except ValidationError as exc:
            for err in exc.errors():
                print(f"error: {'.'.join(map(str, err['loc']))}: {err['msg']}", file=sys.stderr)
            return 2
        print(asyncio.run(create_superuser(email, display_name, password)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
