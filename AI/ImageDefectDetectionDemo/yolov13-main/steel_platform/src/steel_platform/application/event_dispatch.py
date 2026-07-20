from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from steel_platform.application.errors import ApplicationError
from steel_platform.infrastructure.models import DomainEventModel, OutboxEventModel


def dispatch_outbox(factory: sessionmaker[Session], endpoint: str, *, limit: int = 100) -> dict[str, int]:
    sent = failed = 0
    with factory() as session:
        rows = session.execute(
            select(OutboxEventModel, DomainEventModel)
            .join(DomainEventModel, DomainEventModel.id == OutboxEventModel.domain_event_id)
            .where(OutboxEventModel.processed.is_(False))
            .order_by(OutboxEventModel.created_at, OutboxEventModel.id)
            .limit(limit)
        ).all()
    for outbox, event in rows:
        body = json.dumps(
            {"eventType": event.event_type, "payload": event.payload_json, "occurredAt": event.created_at.isoformat()},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(endpoint, data=body, method="POST", headers={"Content-Type": "application/json", "Idempotency-Key": event.id})
        try:
            with urlopen(request, timeout=10) as response:
                if response.status not in {200, 202}:
                    raise ApplicationError("event_dispatch_failed", f"Java服务返回 {response.status}", status_code=502)
            with factory.begin() as session:
                current = session.get(OutboxEventModel, outbox.id)
                if current is not None:
                    current.processed = True
            sent += 1
        except (HTTPError, URLError, TimeoutError, OSError):
            failed += 1
            break
    return {"pending": len(rows), "sent": sent, "failed": failed}
