"""Webhook alert notifier: POSTs each alert as JSON to a configured URL (Task 7.5).

The URL is a secret setting (it often embeds a token) and never appears in logs or errors.
"""

import httpx

from fedtone.application.alerting import AlertNotifier
from fedtone.domain.alerts import Alert


class WebhookNotifier(AlertNotifier):
    def __init__(self, client: httpx.Client, url: str) -> None:
        self._client = client
        self._url = url

    def notify(self, alert: Alert) -> None:
        payload = {
            "id": str(alert.id),
            "document_id": str(alert.document_id),
            "rule": alert.rule.value,
            "message": alert.message,
            "values": alert.values,
            "created_at": alert.created_at.isoformat(),
        }
        try:
            response = self._client.post(self._url, json=payload)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"alert webhook unreachable ({exc.__class__.__name__})") from None
        if response.status_code >= 400:
            raise RuntimeError(f"alert webhook returned HTTP {response.status_code}")
