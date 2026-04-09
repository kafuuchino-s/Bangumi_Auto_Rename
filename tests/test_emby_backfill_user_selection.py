from tools.emby_unwatched_subtitle_backfill.fetch_unwatched_subtitles import EmbyClient, EmbyUser


def test_resolve_user_prefers_kafuuchino_by_default(monkeypatch):
    client = EmbyClient("http://example.com", "token")
    monkeypatch.setattr(
        client,
        "list_users",
        lambda: [
            EmbyUser(user_id="1", name="fainbow"),
            EmbyUser(user_id="2", name="kafuuchino"),
        ],
    )

    user = client.resolve_user()

    assert user.user_id == "2"
    assert user.name == "kafuuchino"


def test_resolve_user_accepts_name_override(monkeypatch):
    client = EmbyClient("http://example.com", "token")
    monkeypatch.setattr(
        client,
        "list_users",
        lambda: [
            EmbyUser(user_id="1", name="fainbow"),
            EmbyUser(user_id="2", name="kafuuchino"),
        ],
    )

    user = client.resolve_user("kafuuchino")

    assert user.user_id == "2"
