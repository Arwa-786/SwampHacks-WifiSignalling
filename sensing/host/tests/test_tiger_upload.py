from datetime import timezone

from tiger_upload import kind_of, saved_at


def test_kind_comes_from_the_recorder_named_in_the_header():
    assert kind_of("record_booth rounds=4 seed=1 person=arwa 2026-10-17 23:43:34 port=x") == "booth"
    assert kind_of("record_breathing 2026-10-17 23:43:34 rates=[10, 15, 20]") == "breathing"
    assert kind_of("something else") == "other"


def test_save_time_is_read_from_the_header_as_utc():
    t = saved_at("record_breathing 2026-10-17 23:43:34 rates=[10]")
    assert t.tzinfo == timezone.utc
