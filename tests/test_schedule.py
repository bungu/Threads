import unittest
from datetime import datetime, timezone

from threads_bot.schedule import next_slot, slots_between, validate_settings


class ScheduleTest(unittest.TestCase):
    def test_validation_and_unique_times(self):
        base = {"prompt": "投稿", "days": [0], "times": ["08:00"], "enabled": True}
        self.assertEqual(validate_settings(base)["timezone"], "Asia/Tokyo")
        for bad in ({**base, "prompt": ""}, {**base, "times": ["08:00", "08:00"]},
                    {**base, "days": [0, 0]}, {**base, "times": ["24:00"]},
                    {**base, "prompt": "あ" * 2401}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_settings(bad)

    def test_jst_weekday_and_utc_boundary(self):
        settings = validate_settings({"prompt": "投稿", "days": [0],
                                      "times": ["00:00"], "enabled": True})
        before = datetime(2026, 10, 4, 14, 59, tzinfo=timezone.utc)
        slot = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(next_slot(settings, before), slot)
        self.assertEqual(slots_between(settings, before, slot), [slot])
        self.assertEqual(slots_between(settings, slot, slot), [])

    def test_off_has_no_slots(self):
        settings = validate_settings({"prompt": "", "days": [], "times": [], "enabled": False})
        self.assertIsNone(next_slot(settings, datetime.now(timezone.utc)))


if __name__ == "__main__":
    unittest.main()
