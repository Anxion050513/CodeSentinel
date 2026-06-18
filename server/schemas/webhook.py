"""GitHub Webhook payload schemas."""
from pydantic import BaseModel, Field


class WebhookRepository(BaseModel):
    id: int
    full_name: str = ""
    name: str = ""
    owner: dict = Field(default_factory=dict)
    html_url: str = ""


class WebhookPullRequest(BaseModel):
    number: int
    title: str = ""
    body: str | None = None
    state: str = "open"
    html_url: str = ""
    head: dict = Field(default_factory=dict)
    base: dict = Field(default_factory=dict)


class GitHubWebhookPayload(BaseModel):
    """GitHub Webhook payload for PR events."""
    action: str  # opened / synchronize / reopened / closed
    pull_request: WebhookPullRequest = Field(default_factory=WebhookPullRequest)
    repository: WebhookRepository = Field(default_factory=WebhookRepository)
    sender: dict = Field(default_factory=dict)
    installation: dict | None = None
