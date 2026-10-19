import logging

from sqlalchemy.orm import Session

from ..models import AgentEvent

log = logging.getLogger("agents")


def add_event(db: Session, campaign_id: str, agent: str, message: str,
              level: str = "info", **data) -> None:
    """Persist an audit event (info | warn | error | decision)."""
    db.add(AgentEvent(campaign_id=campaign_id, agent=agent, level=level,
                      message=message, data=data or {}))
    db.commit()
    getattr(log, "error" if level == "error" else "info")("[%s] %s: %s", campaign_id, agent, message)
