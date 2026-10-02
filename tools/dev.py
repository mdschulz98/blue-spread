"""Cross-platform developer helpers, invoked through poethepoet (``uv run poe <task>``)."""

import re
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def init_env() -> None:
    """Create ``.env`` from ``.env.example`` with freshly generated secrets (never overwrites)."""
    target = ROOT / ".env"
    if target.exists():
        print(".env already exists; leaving it unchanged.")
        return
    shutil.copyfile(ROOT / ".env.example", target)
    text = target.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^JWT_SECRET=.*$", f"JWT_SECRET={secrets.token_urlsafe(48)}", text)
    text = re.sub(
        r"(?m)^POSTGRES_PASSWORD=.*$", f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}", text
    )
    target.write_text(text, encoding="utf-8", newline="\n")
    print("Created .env with a generated JWT_SECRET and POSTGRES_PASSWORD.")
