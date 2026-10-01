from src.config import Secrets, load_config


def test_config_yaml_loads():
    assert load_config().symbol == "SPY"


def test_xavier_telegram_vars_are_accepted(monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("XAVIER_TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("XAVIER_TELEGRAM_CHAT_ID", "123")
    s = Secrets.from_env()
    assert (s.telegram_token, s.telegram_chat_id) == ("tok", "123")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "new")
    assert Secrets.from_env().telegram_token == "new"
