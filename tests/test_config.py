import re

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


class TestClaudeModelConfig:
    """Validate the Claude model name is a well-formed Anthropic model ID."""

    # Valid Anthropic model name patterns:
    #   claude-{tier}-{version}  e.g. claude-sonnet-4-6
    #   claude-{tier}-{version}-{date}  e.g. claude-sonnet-4-5-20250929
    _MODEL_PATTERN = re.compile(
        r"^claude-[a-z]+-\d+(-\d+)?(-\d{8})?$"
    )

    def test_default_model_matches_anthropic_format(self):
        settings = Settings()
        assert self._MODEL_PATTERN.match(settings.claude_model), (
            f"Model name '{settings.claude_model}' doesn't match expected "
            f"Anthropic format (claude-{{tier}}-{{version}}[-{{date}}])"
        )

    def test_model_name_not_empty(self):
        settings = Settings()
        assert settings.claude_model, "claude_model must not be empty"

    def test_model_name_overridable_via_env(self, monkeypatch):
        monkeypatch.setenv("BSKY_CLAUDE_MODEL", "claude-haiku-3-5-20241022")
        settings = Settings()
        assert settings.claude_model == "claude-haiku-3-5-20241022"
