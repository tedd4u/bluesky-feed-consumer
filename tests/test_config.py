from bluesky_feed_consumer.config import Settings


def test_default_settings():
    settings = Settings()
    assert settings.port == 8000
    assert settings.window_sizes == [60, 300, 600]
    assert settings.top_n_limit == 50
    assert settings.velocity_bucket_seconds == 2
    assert settings.velocity_history_seconds == 3600
    assert settings.context_posts_count == 50
    assert settings.reply_weight_boost == 0.2
    assert settings.reply_weight_cap == 0.7
    assert settings.recent_ratio == 0.6


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("BSKY_PORT", "9000")
    monkeypatch.setenv("BSKY_API_KEY", "my-secret")
    monkeypatch.setenv("BSKY_WINDOW_SIZES", "[30, 120]")
    settings = Settings()
    assert settings.port == 9000
    assert settings.api_key == "my-secret"
    assert settings.window_sizes == [30, 120]
