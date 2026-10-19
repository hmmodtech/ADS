from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..llm import LLM, get_llm
from ..services.events import add_event


class BaseAgent:
    """Every agent has a name, access to settings + LLM, and writes to the audit trail."""

    name = "agent"
    label = "Agent"

    def __init__(self, settings: Settings | None = None, llm: LLM | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or get_llm()

    def log(self, db: Session, campaign_id: str, message: str, level: str = "info", **data):
        add_event(db, campaign_id, self.name, message, level, **data)
