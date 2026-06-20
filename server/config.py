"""Application configuration via Pydantic Settings.

Extends the interview system's config pattern with GitHub and code review
specific settings while keeping the same structure for LLM, DB, Redis, etc.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM (Chat)
    llm_api_key: str = "sk-xxx"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_small_model: str = "gpt-4o-mini"

    # Embedding
    embedding_api_key: str = "none"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"

    # MySQL
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "code_review_123"
    mysql_database: str = "code_review_bot"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # GitHub Integration
    github_app_id: int = 0
    github_app_private_key_path: str = "./data/github-app.pem"
    github_webhook_secret: str = "whsec-xxx"
    github_default_token: str = ""

    # LangFuse Observability (optional — leave blank to disable)
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # ChromaDB
    chroma_persist_dir: str = "./data/chroma"

    # Docker (MCP sandbox)
    docker_host: str = "unix:///var/run/docker.sock"

    # App
    app_env: str = "development"
    upload_dir: str = "./data/uploads"

    @property
    def database_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            f"?charset=utf8mb4"
        )

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


settings = Settings()
