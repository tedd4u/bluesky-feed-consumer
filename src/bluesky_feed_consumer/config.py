from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost:5432/bsky"

    api_key: str = "dev-key"
    host: str = "0.0.0.0"
    port: int = 8000

    jetstream_url: str = "wss://jetstream2.us-east.bsky.network/subscribe"
    reconnect_base_delay: float = 1.0
    reconnect_max_delay: float = 60.0

    window_sizes: list[int] = [60, 300, 600]
    top_n_limit: int = 50
    top_languages: int = 20
    velocity_bucket_seconds: int = 2
    velocity_history_seconds: int = 3600

    max_history_posts: int = 200
    context_posts_count: int = 50
    reply_weight_boost: float = 0.2
    reply_weight_cap: float = 0.7
    recent_ratio: float = 0.6
    persona_poll_interval: float = 30.0

    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    sse_push_interval: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", env_prefix="BSKY_")


def get_settings() -> Settings:
    return Settings()
