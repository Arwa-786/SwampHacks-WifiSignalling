import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import pytest


@pytest.fixture(autouse=True)
def no_real_database(monkeypatch):
    """Tests never touch the real Tiger database or Vultr account from server/.env."""
    import gator
    import store

    for module in (store, gator):
        monkeypatch.setattr(module, "load_env", lambda *a, **k: None)
    for key in ("TIGER_URL", "VULTR_INFERENCE_API_KEY", "VULTR_CHAT_MODEL", "VULTR_TTS_MODEL",
                "VULTR_TTS_VOICE", "VULTR_TTS_INSTRUCTIONS"):
        monkeypatch.delenv(key, raising=False)
