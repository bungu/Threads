from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSettings:
    name: str
    thinking_level: str = "low"
    max_output_tokens: int = 2048


# line_fruit_bot の返信生成設定と同じモデル・思考量。
PRO_MODEL = ModelSettings("gemini-3.1-pro-preview")
FLASH_MODEL = ModelSettings("gemini-3.6-flash")
GEMINI_TIMEOUT_SECONDS = 60
THREADS_API_VERSION = "v1.0"
THREADS_TIMEOUT_SECONDS = 20
STATUS_CHECK_ATTEMPTS = 6
STATUS_CHECK_INTERVAL_SECONDS = 2
MAX_POST_CHARACTERS = 140

