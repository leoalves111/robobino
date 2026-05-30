"""
Proteção contra troca de estratégia com exposição aberta.

READ-ONLY: detecta deals via WebSocket quando disponível e bloqueia
trocas após emissão de sinal (janela M5 — operador pode ter entrado manualmente).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

OPEN_STATUSES = frozenset(
    {"OPEN", "ACTIVE", "WAITING", "CREATED", "IN_PROGRESS", "PENDING", "RUNNING"}
)
CLOSED_STATUSES = frozenset(
    {"CLOSED", "WIN", "LOSS", "CANCELLED", "CANCELED", "EXPIRED", "FINISHED", "DONE"}
)


class OrderGuard:
    """Bloqueia troca de estratégia se order_status == OPEN."""

    def __init__(
        self,
        *,
        lock_on_signal_seconds: int = 300,
        lock_on_signal: bool = True,
    ) -> None:
        self.lock_on_signal = lock_on_signal
        self.lock_on_signal_seconds = lock_on_signal_seconds
        self._open_deal_ids: set[str | int] = set()
        self._signal_lock_until: datetime | None = None

    @property
    def order_status(self) -> str:
        if self._open_deal_ids:
            return "OPEN"
        if self.lock_on_signal and self._signal_lock_until:
            if datetime.now(timezone.utc) < self._signal_lock_until:
                return "OPEN"
        return "NONE"

    @property
    def open_deal_count(self) -> int:
        return len(self._open_deal_ids)

    def on_signal_emitted(self) -> None:
        if not self.lock_on_signal:
            return
        self._signal_lock_until = datetime.now(timezone.utc) + timedelta(
            seconds=self.lock_on_signal_seconds
        )
        logger.info(
            "OrderGuard: sinal emitido — troca de estratégia bloqueada por %ds",
            self.lock_on_signal_seconds,
        )

    def ingest_message(self, event: str, payload: dict[str, Any]) -> None:
        """Atualiza estado a partir de mensagens WebSocket Binomo."""
        if not isinstance(payload, dict):
            return

        event_l = (event or "").lower()
        if "deal" in event_l or "order" in event_l or "option" in event_l:
            self._apply_deal_payload(payload)

        for key in ("deal", "deals", "orders", "options", "data"):
            block = payload.get(key)
            if isinstance(block, dict):
                self._apply_deal_payload(block)
            elif isinstance(block, list):
                for item in block:
                    if isinstance(item, dict):
                        self._apply_deal_payload(item)

    def _apply_deal_payload(self, data: dict[str, Any]) -> None:
        deal_id = data.get("deal_id") or data.get("id") or data.get("uuid")
        status = str(data.get("status") or data.get("state") or "").upper()

        if deal_id is None and not status:
            return

        if status in OPEN_STATUSES or (
            status == "" and data.get("finished_at") in (None, "", 0)
        ):
            if deal_id is not None:
                self._open_deal_ids.add(deal_id)
                logger.debug("OrderGuard: deal aberto detectado id=%s", deal_id)
        elif status in CLOSED_STATUSES or data.get("finished_at"):
            if deal_id is not None:
                self._open_deal_ids.discard(deal_id)
                logger.debug("OrderGuard: deal encerrado id=%s", deal_id)

    def clear_signal_lock(self) -> None:
        self._signal_lock_until = None
