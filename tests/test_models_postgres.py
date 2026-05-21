"""Tests that verify PostgreSQL-specific model behavior.

These tests don't require a running Postgres instance. They use SQLAlchemy's
DDL compiler against the PostgreSQL dialect to catch issues that SQLite-based
tests miss (JSONB defaults, ENUM values, ARRAY handling).
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg_dialect

from bluesky_feed_consumer.models.chat import (
    ChatMessage,
    MessageRole,
    Persona,
    PersonaPost,
    PersonaStatus,
    PostType,
    _enum_values,
)
from bluesky_feed_consumer.models.stats import StatSnapshot


class TestEnumWireValues:
    """Ensure enum values match what CREATE TYPE defines (lowercase)."""

    def test_persona_status_values_are_lowercase(self):
        values = _enum_values(PersonaStatus)
        assert values == ["loading", "ready", "error"]

    def test_post_type_values_are_lowercase(self):
        values = _enum_values(PostType)
        assert values == ["post", "reply", "quote"]

    def test_message_role_values_are_lowercase(self):
        values = _enum_values(MessageRole)
        assert values == ["user", "assistant"]

    def test_enum_values_match_str_representation(self):
        """StrEnum .value should equal str() of the member."""
        for member in PersonaStatus:
            assert str(member) == member.value
        for member in PostType:
            assert str(member) == member.value
        for member in MessageRole:
            assert str(member) == member.value

    def test_persona_status_column_uses_values_callable(self):
        """Verify the Persona.status column is configured to send values, not names."""
        col = Persona.__table__.c.status
        enum_type = col.type
        # values_callable should produce lowercase values
        assert "LOADING" not in enum_type.enums
        assert "loading" in enum_type.enums

    def test_post_type_column_uses_values_callable(self):
        col = PersonaPost.__table__.c.post_type
        enum_type = col.type
        assert "POST" not in enum_type.enums
        assert "post" in enum_type.enums

    def test_message_role_column_uses_values_callable(self):
        col = ChatMessage.__table__.c.role
        enum_type = col.type
        assert "USER" not in enum_type.enums
        assert "user" in enum_type.enums


class TestJsonbDefaults:
    """Verify JSONB server_default renders correct SQL for PostgreSQL."""

    def _render_create_table_sql(self, table: sa.Table) -> str:
        """Compile CREATE TABLE DDL against the PostgreSQL dialect."""
        dialect = pg_dialect.dialect()
        compiled = sa.schema.CreateTable(table).compile(dialect=dialect)
        return str(compiled)

    def test_top_liked_default_not_double_quoted(self):
        sql = self._render_create_table_sql(StatSnapshot.__table__)
        # Should contain '[]' as default, NOT '''[]'''
        assert "'''[]'''" not in sql, "JSONB default is double-quoted"
        assert "'[]'" in sql

    def test_top_reposted_default_not_double_quoted(self):
        sql = self._render_create_table_sql(StatSnapshot.__table__)
        assert "'''[]'''" not in sql, "JSONB default is double-quoted"

    def test_language_breakdown_default_not_double_quoted(self):
        sql = self._render_create_table_sql(StatSnapshot.__table__)
        assert "'''{}'''" not in sql, "JSONB default is double-quoted"
        assert "'{}'" in sql

    def test_jsonb_defaults_are_valid_json_literals(self):
        """The rendered defaults should be valid PostgreSQL JSON literals."""
        sql = self._render_create_table_sql(StatSnapshot.__table__)
        # After DEFAULT, the value should be a single-quoted JSON string
        # Valid: DEFAULT '[]'   Invalid: DEFAULT '''[]''' or DEFAULT '[]
        lines = sql.split("\n")
        for line in lines:
            if "JSONB" in line and "DEFAULT" in line:
                # Extract the default value between DEFAULT and the next comma/newline
                assert "'''" not in line, f"Triple-quoted default in: {line.strip()}"
