import json
from typing import Any

from .config import GeminiCredentials
from .settings import (
    FLASH_MODEL,
    GEMINI_TIMEOUT_SECONDS,
    MAX_POST_CHARACTERS,
    PRO_MODEL,
    ModelSettings,
)


SYSTEM_INSTRUCTION = (
    "あなたはユーザーの投稿プロンプトに従って、X・Threads用のテキスト投稿を1件作成します。"
    "投稿プロンプトの内容、文体、言語に従ってください。"
    "出力は投稿本文だけにしてください。前置き、説明、見出し、番号、引用符、"
    "Markdownのコードフェンス、JSONは付けません。"
    "1件の本文を140文字以内で作成してください。"
)


class GenerationError(RuntimeError):
    pass


def validate_post(text: Any) -> str:
    if not isinstance(text, str):
        raise ValueError("Geminiの出力が文字列ではありません。")
    result = text.strip()
    if not result:
        raise ValueError("Geminiの出力が空です。")
    if len(result) > MAX_POST_CHARACTERS:
        raise ValueError(f"本文が{MAX_POST_CHARACTERS}文字を超えています。")
    if result.startswith(("```", "投稿文:", "投稿文：")):
        raise ValueError("本文以外の説明が含まれています。")
    return result


def generate_once(client: Any, model: ModelSettings, prompt: str) -> str:
    interaction = client.interactions.create(
        model=model.name,
        input=json.dumps({"post_prompt": prompt}, ensure_ascii=False),
        system_instruction=SYSTEM_INSTRUCTION,
        generation_config={
            "thinking_level": model.thinking_level,
            "max_output_tokens": model.max_output_tokens,
        },
    )
    return validate_post(interaction.output_text)


def generate_post(pro_client: Any, flash_client: Any, prompt: str) -> str:
    if not prompt.strip():
        raise GenerationError("投稿プロンプトが空です。")
    try:
        return generate_once(pro_client, PRO_MODEL, prompt)
    except Exception:
        # 既存Botと同じく、Proが失敗したときだけFlashへ切り替える。
        try:
            return generate_once(flash_client, FLASH_MODEL, prompt)
        except Exception as exc:
            raise GenerationError("ProとFlashの両方で投稿本文を生成できませんでした。") from exc


def create_clients(credentials: GeminiCredentials) -> tuple[Any, Any]:
    from google import genai

    options = {"timeout": GEMINI_TIMEOUT_SECONDS * 1000, "retry_options": {"attempts": 1}}
    return (
        genai.Client(api_key=credentials.pro_key, http_options=options),
        genai.Client(api_key=credentials.flash_key, http_options=options),
    )
