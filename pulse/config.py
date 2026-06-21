"""Load and validate product, pipeline, and MCP configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

PulseEnvironment = Literal["local", "staging", "production"]
EmailMode = Literal["draft", "send"]

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
PRODUCTS_DIR = CONFIG_DIR / "products"
MCP_CONFIG_DIR = CONFIG_DIR / "mcp"


class PlayStoreConfig(BaseModel):
    app_id: str
    lang: str = "en"
    country: str = "in"


class IngestionConfig(BaseModel):
    window_weeks: int = Field(ge=8, le=12)
    min_reviews: int = Field(ge=1)
    max_reviews: int = Field(ge=1)
    min_words: int = Field(ge=1)
    allowed_language: str = "en"


class EmailDeliveryConfig(BaseModel):
    recipients: list[str]
    default_mode: Literal["draft", "send"] = "draft"
    from_address: str | None = Field(default=None, alias="from")


class DeliveryConfig(BaseModel):
    google_doc_id: str
    email: EmailDeliveryConfig


class ProductConfig(BaseModel):
    product: str
    display_name: str
    play_store: PlayStoreConfig
    ingestion: IngestionConfig
    delivery: DeliveryConfig


class EmbeddingConfig(BaseModel):
    provider: str
    model: str
    batch_size: int = Field(ge=1)


class UmapConfig(BaseModel):
    n_neighbors: int = Field(ge=2)
    n_components: int = Field(ge=1)
    metric: str = "cosine"


class HdbscanConfig(BaseModel):
    min_cluster_size: int = Field(ge=2)
    min_samples: int = Field(ge=1)


class ClusteringConfig(BaseModel):
    umap: UmapConfig
    hdbscan: HdbscanConfig
    dominant_cluster_threshold: float = Field(default=0.8, ge=0.5, le=1.0)
    prefix_rating_in_embed: bool = True
    fallback_rating_stratify: bool = True


class SummarizationConfig(BaseModel):
    provider: str
    model: str
    max_themes: int = Field(ge=1)
    max_tokens_per_run: int = Field(ge=1)
    max_samples_per_cluster: int = Field(ge=1)
    max_output_tokens_per_theme: int = Field(ge=1)
    request_interval_seconds: float = Field(ge=0)


class SafetyConfig(BaseModel):
    scrub_pii: bool = True
    max_review_chars: int = Field(ge=1)


class PipelineConfig(BaseModel):
    embedding: EmbeddingConfig
    clustering: ClusteringConfig
    summarization: SummarizationConfig
    safety: SafetyConfig


class HostedMcpConfig(BaseModel):
    base_url: str
    api_key: str | None = None
    health_path: str = "/health"
    append_to_doc_path: str = "/append_to_doc"
    create_email_draft_path: str = "/create_email_draft"
    send_email_path: str = "/send_email"


DEFAULT_MCP_SERVER_URL = "https://web-production-c5ea8.up.railway.app"


class SecretValidationError(Exception):
    """Raised when required secrets or env files are missing."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


def _load_dotenv() -> None:
    """Load `.env` from repo root into os.environ (does not override existing vars)."""
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    with env_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def load_product_config(product: str = "groww") -> ProductConfig:
    path = PRODUCTS_DIR / f"{product}.yaml"
    config = ProductConfig.model_validate(_load_yaml(path))
    doc_id_override = os.environ.get("GOOGLE_DOC_ID", "").strip()
    if doc_id_override:
        config = config.model_copy(
            update={"delivery": config.delivery.model_copy(update={"google_doc_id": doc_id_override})}
        )
    return config


def get_email_recipients(product_config: ProductConfig) -> list[str]:
    """Recipients from PULSE_EMAIL_TO env or product YAML."""
    override = os.environ.get("PULSE_EMAIL_TO", "").strip()
    if override:
        return [address.strip() for address in override.split(",") if address.strip()]
    return product_config.delivery.email.recipients


def get_email_from_address(product_config: ProductConfig) -> str | None:
    """Sender Gmail account — env overrides YAML; actual send-from is MCPServer OAuth."""
    override = os.environ.get("PULSE_EMAIL_FROM", "").strip()
    if override:
        return override
    return product_config.delivery.email.from_address


def load_pipeline_config() -> PipelineConfig:
    path = CONFIG_DIR / "pipeline.yaml"
    return PipelineConfig.model_validate(_load_yaml(path))


def load_mcp_servers_config() -> dict:
    path = MCP_CONFIG_DIR / "servers.json"
    if not path.is_file():
        raise FileNotFoundError(f"MCP servers config not found: {path}")
    import json

    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_hosted_mcp_config() -> HostedMcpConfig:
    """Load hosted MCP settings from env with servers.json fallback."""
    servers = load_mcp_servers_config()
    workspace = servers.get("mcpServers", {}).get("google-workspace", {})
    base_url = os.environ.get("MCP_SERVER_URL") or workspace.get("url") or DEFAULT_MCP_SERVER_URL
    endpoints = workspace.get("endpoints") or {}
    api_key = os.environ.get("MCP_API_KEY") or None
    if api_key == "":
        api_key = None
    return HostedMcpConfig(
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        health_path=endpoints.get("health", "/health"),
        append_to_doc_path=endpoints.get("append_to_doc", "/append_to_doc"),
        create_email_draft_path=endpoints.get("create_email_draft", "/create_email_draft"),
        send_email_path=endpoints.get("send_email", "/send_email"),
    )


def get_pulse_environment() -> PulseEnvironment:
    """Runtime environment: local (default), staging, or production (`PULSE_ENV`)."""
    value = os.environ.get("PULSE_ENV", "local").strip().lower()
    if value not in ("local", "staging", "production"):
        raise ValueError(
            f"PULSE_ENV must be local, staging, or production — got {value!r}"
        )
    return value  # type: ignore[return-value]


def resolve_email_mode(
    product_config: ProductConfig,
    override: EmailMode | None = None,
) -> EmailMode:
    """
    Resolve email delivery mode.

    Priority: CLI override → `PULSE_EMAIL_MODE` → environment default → YAML.
    Production (`PULSE_ENV=production`) defaults to `send` unless overridden.
    Staging defaults to `draft`.
    """
    if override in ("draft", "send"):
        return override
    env_mode = os.environ.get("PULSE_EMAIL_MODE", "").strip().lower()
    if env_mode in ("draft", "send"):
        return env_mode  # type: ignore[return-value]
    pulse_env = get_pulse_environment()
    if pulse_env == "production":
        return "send"
    if pulse_env == "staging":
        return "draft"
    return product_config.delivery.email.default_mode


def _env_file_has_required_keys(env_path: Path, required_keys: list[str]) -> list[str]:
    missing: list[str] = []
    if not env_path.is_file():
        return [f"Missing MCP env file: {env_path} (copy from {env_path.name}.example)"]

    values: dict[str, str] = {}
    with env_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()

    for key in required_keys:
        if not values.get(key):
            missing.append(f"{env_path.name}: {key} is not set")
    return missing


def validate_openai_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SecretValidationError(
            [
                "OPENAI_API_KEY is not set — required when embedding.provider is openai "
                "(export OPENAI_API_KEY=... or switch to BGE in config/pipeline.yaml)"
            ]
        )


def validate_embedding_config(pipeline_config: PipelineConfig) -> None:
    """Validate secrets required for the configured embedding provider."""
    provider = pipeline_config.embedding.provider.lower()
    if provider == "openai":
        validate_openai_key()


def validate_groq_key() -> None:
    if not get_groq_api_key(optional=True):
        raise SecretValidationError(
            [
                "GROQ_API_KEY is not set — required for summarization "
                "(export GROQ_API_KEY=... or add to your shell profile)"
            ]
        )


def get_groq_api_key(api_key: str | None = None, *, optional: bool = False) -> str:
    """Return trimmed Groq API key from argument or GROQ_API_KEY env."""
    value = (api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")).strip()
    if not value and not optional:
        raise SecretValidationError(["GROQ_API_KEY is not set"])
    return value


def create_groq_sdk_client(api_key: str | None = None):
    """Build Groq SDK client with CI-friendly HTTP settings."""
    import httpx
    from groq import Groq

    key = get_groq_api_key(api_key)
    http_client = httpx.Client(
        timeout=httpx.Timeout(120.0, connect=30.0),
        http2=False,
        follow_redirects=True,
    )
    return Groq(
        api_key=key,
        timeout=120.0,
        max_retries=5,
        http_client=http_client,
    )


def validate_groq_connectivity() -> None:
    """Reach Groq with a minimal completion (catches bad keys before long runs)."""
    pipeline = load_pipeline_config()
    if pipeline.summarization.provider.lower() != "groq":
        return

    try:
        client = create_groq_sdk_client()
        response = client.chat.completions.create(
            model=pipeline.summarization.model,
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            max_tokens=8,
        )
        if not response.choices:
            raise SecretValidationError(["Groq API returned no completion choices"])
    except SecretValidationError:
        raise
    except Exception as exc:
        raise SecretValidationError(
            [
                "Groq API connectivity check failed: "
                f"{exc}. Confirm GROQ_API_KEY is valid and has no extra spaces/newlines."
            ]
        ) from exc


def validate_agent_secrets(*, require_llm_keys: bool = True) -> None:
    """Validate API keys used by the pulse agent (not Google OAuth)."""
    errors: list[str] = []

    if require_llm_keys:
        try:
            pipeline = load_pipeline_config()
            validate_embedding_config(pipeline)
        except SecretValidationError as exc:
            errors.extend(exc.messages)
        except FileNotFoundError as exc:
            errors.append(str(exc))
        try:
            validate_groq_key()
        except SecretValidationError as exc:
            errors.extend(exc.messages)

    if errors:
        raise SecretValidationError(errors)


def validate_hosted_mcp_config() -> None:
    """Ensure hosted MCP URL is configured (API key optional)."""
    config = load_hosted_mcp_config()
    if not config.base_url.startswith("http"):
        raise SecretValidationError(
            [f"MCP_SERVER_URL must be an http(s) URL, got: {config.base_url!r}"]
        )


def validate_hosted_mcp_connectivity() -> None:
    """Reach hosted MCP and confirm Docs + Gmail endpoints are advertised."""
    from pulse.agent.mcp_client import HostedGoogleWorkspaceClient, HostedMcpError

    validate_hosted_mcp_config()
    config = load_hosted_mcp_config()
    try:
        with HostedGoogleWorkspaceClient(config) as client:
            health = client.health_check()
            if health.status != "ok":
                raise SecretValidationError(
                    [f"Hosted MCP health check failed: status={health.status!r}"]
                )
            docs_capabilities = client.list_docs_capabilities()
            if not any("append" in item for item in docs_capabilities):
                raise SecretValidationError(
                    [
                        "Hosted MCP does not advertise a Docs append endpoint — "
                        f"endpoints={health.endpoints!r}"
                    ]
                )
            gmail_capabilities = client.list_gmail_capabilities()
            if not any("draft" in item or "email" in item for item in gmail_capabilities):
                raise SecretValidationError(
                    [
                        "Hosted MCP does not advertise a Gmail draft endpoint — "
                        f"endpoints={health.endpoints!r}"
                    ]
                )
            if health.has_refresh_token is False:
                raise SecretValidationError(
                    [
                        "Hosted MCP GOOGLE_TOKEN_JSON is missing refresh_token — "
                        "run `python authenticate.py` in MCPServer, paste full token.json "
                        "into Railway GOOGLE_TOKEN_JSON, and redeploy."
                    ]
                )
            if health.google_token_usable is False:
                detail = health.google_token_error or "token not usable"
                raise SecretValidationError(
                    [f"Hosted MCP Google token check failed: {detail}"]
                )
    except HostedMcpError as exc:
        raise SecretValidationError([str(exc)]) from exc


def validate_mcp_env_files() -> None:
    """Validate hosted MCP configuration (legacy OAuth env files not required)."""
    validate_hosted_mcp_config()


def validate_delivery_config(product_config: ProductConfig) -> None:
    """Ensure delivery targets are configured for MCP writes."""
    doc_id = product_config.delivery.google_doc_id.strip()
    if not doc_id or doc_id.startswith("<") or doc_id == "REPLACE_WITH_DOC_ID":
        raise ValueError(
            "delivery.google_doc_id is not configured — set a real Google Doc id in "
            f"config/products/{product_config.product}.yaml"
        )


def validate_production_readiness(product: str = "groww") -> None:
    """Preflight checks before the first production scheduled run."""
    from pulse.agent.mcp_client import HostedGoogleWorkspaceClient, HostedMcpError

    errors: list[str] = []
    try:
        validate_all_configs(product, require_secrets=True)
        product_config = load_product_config(product)
        validate_delivery_config(product_config)
    except (FileNotFoundError, ValueError, SecretValidationError) as exc:
        if isinstance(exc, SecretValidationError):
            errors.extend(exc.messages)
        else:
            errors.append(str(exc))

    if not errors:
        product_config = load_product_config(product)
        recipients = get_email_recipients(product_config)
        if not recipients:
            errors.append(
                "No email recipients — set delivery.email.recipients in groww.yaml "
                "or PULSE_EMAIL_TO"
            )

        mode = resolve_email_mode(product_config)
        if get_pulse_environment() != "production" and mode == "send":
            errors.append(
                "PULSE_EMAIL_MODE=send but PULSE_ENV is not production — "
                "set PULSE_ENV=production for scheduled send runs"
            )

        try:
            config = load_hosted_mcp_config()
            with HostedGoogleWorkspaceClient(config) as client:
                if not client.health_check().has_google_token:
                    errors.append(
                        "Hosted MCP has no Google token — refresh OAuth on Railway MCPServer"
                    )
                if mode == "send" and not client.supports_send_email():
                    errors.append(
                        "Production send mode requires POST /send_email on hosted MCP — "
                        "MCPServer v1 is draft-only. Deploy send endpoint or set "
                        "PULSE_EMAIL_MODE=draft until send is available (see DOC/runbook.md)"
                    )
        except HostedMcpError as exc:
            errors.append(str(exc))

    if errors:
        raise SecretValidationError(errors)


def validate_all_configs(product: str = "groww", *, require_secrets: bool = False) -> None:
    load_product_config(product)
    load_pipeline_config()
    load_mcp_servers_config()
    if require_secrets:
        validate_agent_secrets()
        validate_mcp_env_files()
        validate_hosted_mcp_connectivity()
        validate_groq_connectivity()
