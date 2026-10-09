import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class GeminiCredentials:
    pro_key: str
    flash_key: str


@dataclass(frozen=True)
class ThreadsCredentials:
    access_token: str
    user_id: str


def load_environment() -> None:
    load_dotenv(PROJECT_DIR / ".env", override=False)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"環境変数 {name} を設定してください。")
    return value


def gemini_credentials() -> GeminiCredentials:
    return GeminiCredentials(_required("GEMINI_PRO_API_KEY"), _required("GEMINI_API_KEY"))


def threads_credentials() -> ThreadsCredentials:
    user_id = _required("THREADS_USER_ID")
    if not user_id.isascii() or not user_id.isdecimal():
        raise ConfigurationError("THREADS_USER_ID には数字のユーザーIDを指定してください。")
    return ThreadsCredentials(_required("THREADS_ACCESS_TOKEN"), user_id)

