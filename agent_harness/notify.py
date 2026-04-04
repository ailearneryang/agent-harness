"""Webhook 通知 - pipeline 完成/失败时推送"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger("agent_harness")


class Notifier:
    """Webhook 通知器

    支持多个 webhook URL，pipeline 完成时自动推送。
    兼容 Slack / 飞书 / 钉钉 / 通用 webhook。
    """

    def __init__(self):
        self._webhooks: list[dict[str, Any]] = []

    def add_webhook(
        self,
        url: str,
        events: list[str] | None = None,
        headers: dict[str, str] | None = None,
        name: str = "",
    ) -> None:
        """注册 webhook

        events: 触发事件列表，如 ["pipeline_complete", "pipeline_failed"]
                None 表示所有事件
        """
        self._webhooks.append({
            "url": url,
            "events": events,
            "headers": headers or {"Content-Type": "application/json"},
            "name": name or url[:30],
        })

    def list_webhooks(self) -> list[dict]:
        return [{"name": w["name"], "url": w["url"], "events": w["events"]} for w in self._webhooks]

    async def notify(self, event: str, payload: dict[str, Any]) -> list[dict]:
        """发送通知到所有匹配的 webhook"""
        results = []
        for wh in self._webhooks:
            if wh["events"] and event not in wh["events"]:
                continue
            result = await self._send(wh, event, payload)
            results.append(result)
        return results

    async def _send(self, wh: dict, event: str, payload: dict) -> dict:
        try:
            import httpx
        except ImportError:
            return {"webhook": wh["name"], "success": False, "error": "httpx not installed"}

        body = {
            "event": event,
            "timestamp": __import__("time").time(),
            **payload,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(wh["url"], json=body, headers=wh["headers"])
                success = 200 <= resp.status_code < 300
                if not success:
                    logger.warning("Webhook %s returned %d", wh["name"], resp.status_code)
                return {"webhook": wh["name"], "success": success, "status": resp.status_code}
        except Exception as e:
            logger.error("Webhook %s failed: %s", wh["name"], e)
            return {"webhook": wh["name"], "success": False, "error": str(e)}
