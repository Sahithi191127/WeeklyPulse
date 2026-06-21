"""Configuration loading tests (Phase 0)."""

import pytest

from pulse.config import (
    SecretValidationError,
    get_email_recipients,
    load_hosted_mcp_config,
    load_mcp_servers_config,
    load_pipeline_config,
    load_product_config,
    validate_agent_secrets,
    validate_all_configs,
    validate_delivery_config,
    validate_hosted_mcp_config,
)


def test_load_product_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_DOC_ID", raising=False)
    monkeypatch.delenv("PULSE_EMAIL_TO", raising=False)
    config = load_product_config("groww")
    assert config.product == "groww"
    assert config.play_store.app_id == "com.nextbillion.groww"
    assert config.ingestion.window_weeks == 10
    assert config.delivery.google_doc_id == "REPLACE_WITH_DOC_ID"
    assert config.delivery.email.recipients == ["product-leads@example.com"]
    assert config.delivery.email.from_address == "pulse-sender@yourcompany.com"


def test_get_email_recipients_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_product_config("groww")
    monkeypatch.setenv("PULSE_EMAIL_TO", "other@example.com, second@example.com")
    assert get_email_recipients(config) == ["other@example.com", "second@example.com"]


def test_load_product_config_google_doc_id_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_DOC_ID", "env-doc-id-override")
    config = load_product_config("groww")
    assert config.delivery.google_doc_id == "env-doc-id-override"


def test_load_pipeline_config() -> None:
    config = load_pipeline_config()
    assert config.embedding.provider == "sentence-transformers"
    assert config.embedding.model == "BAAI/bge-small-en-v1.5"
    assert config.summarization.provider == "groq"


def test_load_mcp_servers_config() -> None:
    servers = load_mcp_servers_config()
    assert "mcpServers" in servers
    assert "google-workspace" in servers["mcpServers"]
    workspace = servers["mcpServers"]["google-workspace"]
    assert workspace["url"] == "https://web-production-c5ea8.up.railway.app"
    assert workspace["endpoints"]["append_to_doc"] == "/append_to_doc"


def test_validate_all_configs_without_secrets() -> None:
    validate_all_configs("groww", require_secrets=False)


def test_validate_agent_secrets_missing_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SecretValidationError) as exc_info:
        validate_agent_secrets()
    message = "\n".join(exc_info.value.messages)
    assert "GROQ_API_KEY" in message
    assert "OPENAI_API_KEY" not in message


def test_load_hosted_mcp_config() -> None:
    config = load_hosted_mcp_config()
    assert config.base_url == "https://web-production-c5ea8.up.railway.app"
    assert config.append_to_doc_path == "/append_to_doc"


def test_validate_hosted_mcp_config() -> None:
    validate_hosted_mcp_config()


def test_validate_delivery_config_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOOGLE_DOC_ID", raising=False)
    config = load_product_config("groww")
    config = config.model_copy(
        update={"delivery": config.delivery.model_copy(update={"google_doc_id": "REPLACE_WITH_DOC_ID"})}
    )
    with pytest.raises(ValueError, match="google_doc_id"):
        validate_delivery_config(config)
