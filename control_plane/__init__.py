"""Control-plane package. Load .env before any submodule reads configuration."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from . import agent  # noqa: E402

__all__ = ["agent"]
