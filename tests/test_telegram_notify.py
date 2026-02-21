from types import SimpleNamespace

import requests

from src.notification.telegram_notify import TelegramNotifier


def _mock_telegram_config(
    monkeypatch,
    *,
    enabled=True,
    token="test_token",
    chat_id="123456",
    base_url="https://api.telegram.org",
):
    def _get_config(key):
        if key == "telegram_enabled":
            return enabled
        if key == "telegram_bot_token":
            return token
        if key == "telegram_chat_id":
            return chat_id
        if key == "telegram_base_url":
            return base_url
        return None

    monkeypatch.setattr("src.notification.telegram_notify.cm.get_config", _get_config)


def test_is_available_requires_enabled_and_credentials(monkeypatch):
    _mock_telegram_config(monkeypatch, enabled=False)
    notifier = TelegramNotifier()
    assert notifier.is_available() is False

    _mock_telegram_config(monkeypatch, enabled=True, token="", chat_id="123")
    notifier = TelegramNotifier()
    assert notifier.is_available() is False

    _mock_telegram_config(monkeypatch, enabled=True, token="abc", chat_id="")
    notifier = TelegramNotifier()
    assert notifier.is_available() is False

    _mock_telegram_config(monkeypatch, enabled=True, token="abc", chat_id="123")
    notifier = TelegramNotifier()
    assert notifier.is_available() is True


def test_send_message_success(monkeypatch):
    _mock_telegram_config(monkeypatch)

    def _fake_post(url, json, timeout):
        assert "/bottest_token/sendMessage" in url
        assert json["chat_id"] == "123456"
        assert "批次处理完成" in json["text"]
        assert timeout == 30
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setattr("src.notification.telegram_notify.requests.post", _fake_post)

    notifier = TelegramNotifier()
    success, message = notifier.send_message("批次处理完成")

    assert success is True
    assert message == "消息发送成功"


def test_send_message_http_error(monkeypatch):
    _mock_telegram_config(monkeypatch)

    def _fake_post(url, json, timeout):
        return SimpleNamespace(status_code=500, json=lambda: {"ok": False})

    monkeypatch.setattr("src.notification.telegram_notify.requests.post", _fake_post)

    notifier = TelegramNotifier()
    success, message = notifier.send_message("批次处理完成")

    assert success is False
    assert "错误状态码" in message


def test_send_message_timeout(monkeypatch):
    _mock_telegram_config(monkeypatch)

    def _fake_post(url, json, timeout):
        raise requests.exceptions.Timeout

    monkeypatch.setattr("src.notification.telegram_notify.requests.post", _fake_post)

    notifier = TelegramNotifier()
    success, message = notifier.send_message("批次处理完成")

    assert success is False
    assert "超时" in message


def test_send_photo_success(monkeypatch):
    _mock_telegram_config(monkeypatch)

    def _fake_post(url, json, timeout):
        assert "/bottest_token/sendPhoto" in url
        assert json["chat_id"] == "123456"
        assert json["photo"] == "https://image.tmdb.org/t/p/w500/abc.jpg"
        assert "已入库" in json["caption"]
        assert timeout == 30
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setattr("src.notification.telegram_notify.requests.post", _fake_post)

    notifier = TelegramNotifier()
    success, message = notifier.send_photo(
        "https://image.tmdb.org/t/p/w500/abc.jpg", "📂 已入库1个文件"
    )

    assert success is True
    assert message == "图片消息发送成功"


def test_send_photo_http_error(monkeypatch):
    _mock_telegram_config(monkeypatch)

    def _fake_post(url, json, timeout):
        return SimpleNamespace(status_code=500, json=lambda: {"ok": False})

    monkeypatch.setattr("src.notification.telegram_notify.requests.post", _fake_post)

    notifier = TelegramNotifier()
    success, message = notifier.send_photo(
        "https://image.tmdb.org/t/p/w500/abc.jpg", "📂 已入库1个文件"
    )

    assert success is False
    assert "错误状态码" in message


def test_send_photo_timeout(monkeypatch):
    _mock_telegram_config(monkeypatch)

    def _fake_post(url, json, timeout):
        raise requests.exceptions.Timeout

    monkeypatch.setattr("src.notification.telegram_notify.requests.post", _fake_post)

    notifier = TelegramNotifier()
    success, message = notifier.send_photo(
        "https://image.tmdb.org/t/p/w500/abc.jpg", "📂 已入库1个文件"
    )

    assert success is False
    assert "超时" in message


def test_send_photo_with_empty_url(monkeypatch):
    _mock_telegram_config(monkeypatch)

    notifier = TelegramNotifier()
    success, message = notifier.send_photo("", "📂 已入库1个文件")

    assert success is False
    assert "图片地址为空" in message


def test_test_connection_success(monkeypatch):
    _mock_telegram_config(monkeypatch)

    def _fake_post(url, json, timeout):
        assert "/bottest_token/sendMessage" in url
        assert json["chat_id"] == "123456"
        assert "连接测试成功" in json["text"]
        assert timeout == 10
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    monkeypatch.setattr("src.notification.telegram_notify.requests.post", _fake_post)

    notifier = TelegramNotifier()
    success, message = notifier.test_connection()

    assert success is True
    assert "连接成功" in message


def test_test_connection_not_configured(monkeypatch):
    _mock_telegram_config(monkeypatch, token="")
    notifier = TelegramNotifier()
    success, message = notifier.test_connection()
    assert success is False
    assert "Token" in message

    _mock_telegram_config(monkeypatch, token="abc", chat_id="")
    notifier = TelegramNotifier()
    success, message = notifier.test_connection()
    assert success is False
    assert "Chat ID" in message
