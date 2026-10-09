import json
import unittest
from types import SimpleNamespace

from threads_bot.gemini import GenerationError, generate_post, validate_post
from threads_bot.settings import FLASH_MODEL, PRO_MODEL


class FakeInteractions:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(output_text=value)


class FakeClient:
    def __init__(self, *outputs):
        self.interactions = FakeInteractions(outputs)


class GeminiTest(unittest.TestCase):
    def test_pro_output_is_only_body_and_prompt_is_passed(self):
        pro = FakeClient(" 朝の散歩は気持ちいい。\n")
        flash = FakeClient("unused")
        result = generate_post(pro, flash, "散歩について書いて")
        self.assertEqual(result, "朝の散歩は気持ちいい。")
        self.assertEqual(len(flash.interactions.calls), 0)
        call = pro.interactions.calls[0]
        self.assertEqual(call["model"], PRO_MODEL.name)
        self.assertEqual(json.loads(call["input"])["post_prompt"], "散歩について書いて")
        self.assertIn("X・Threads", call["system_instruction"])
        self.assertIn("投稿本文だけ", call["system_instruction"])
        self.assertEqual(call["generation_config"]["thinking_level"], "low")

    def test_fallback_on_api_failure(self):
        pro = FakeClient(RuntimeError("quota"))
        flash = FakeClient("本文")
        self.assertEqual(generate_post(pro, flash, "お題"), "本文")
        self.assertEqual(flash.interactions.calls[0]["model"], FLASH_MODEL.name)

    def test_fallback_on_invalid_pro_body(self):
        pro = FakeClient("投稿文: 本文")
        flash = FakeClient("本文")
        self.assertEqual(generate_post(pro, flash, "お題"), "本文")

    def test_both_fail(self):
        with self.assertRaises(GenerationError):
            generate_post(FakeClient(""), FakeClient("x" * 501), "お題")

    def test_empty_prompt_is_rejected_without_api_call(self):
        pro = FakeClient("unused")
        with self.assertRaises(GenerationError):
            generate_post(pro, FakeClient("unused"), "  ")
        self.assertEqual(pro.interactions.calls, [])

    def test_post_validation_boundaries(self):
        self.assertEqual(validate_post("あ" * 140), "あ" * 140)
        for value in (None, " ", "あ" * 141, "```\n本文\n```", "投稿文：本文"):
            with self.subTest(value=repr(value)[:20]), self.assertRaises(ValueError):
                validate_post(value)


if __name__ == "__main__":
    unittest.main()
