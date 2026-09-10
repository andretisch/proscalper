"""Ollama Cloud chat client."""

from __future__ import annotations

import json
from typing import Any

import requests

from app.config import Settings


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def chat(self, messages: list[dict[str, str]], timeout: int = 60) -> str:
        url = f"{self.s.ollama_host}/api/chat"
        payload = {
            "model": self.s.ollama_model,
            "messages": messages,
            "stream": False,
        }
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self.s.ollama_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("message") or {}).get("content") or ""

    def decide_trade(self, signal: dict[str, Any], context: str) -> dict[str, Any]:
        system = (
            "Ты риск-офицер скальп-бота ProScalp. Ответь СТРОГО JSON без markdown:\n"
            '{"decision":"go"|"no_go","confidence":0-1,"comment":"кратко на русском"}\n'
            "Правила: боковик→S2/S3; тренд→S1/S4; S2 против тренда запрет; "
            "нет импульса плохо; свежая/тонкая стенка — no_go."
        )
        user = f"Сигнал:\n{json.dumps(signal, ensure_ascii=False)}\n\nКонтекст:\n{context}"
        raw = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        return _parse_json_decision(raw)

    def watchlist_pick(
        self, candidates: list[dict[str, Any]], limit: int = 5
    ) -> dict[str, Any]:
        system = (
            "Ты watchlist-агент ProScalp. Выбери до "
            f"{limit} символов для скальпа. Ответ СТРОГО JSON:\n"
            '{"symbols":["BTCUSDT",...],"comment":"почему"}\n'
            "Учитывай 24h изменение и оборот. Включи BTC/ETH если адекватны."
        )
        raw = self.chat(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(candidates[:40], ensure_ascii=False),
                },
            ]
        )
        data = _parse_json_decision(raw, default_key="symbols")
        if "symbols" not in data:
            data = {"symbols": [], "comment": raw[:300], "raw": True}
        return data


def _parse_json_decision(raw: str, default_key: str = "decision") -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    except Exception:
        pass
    if default_key == "decision":
        return {"decision": "no_go", "confidence": 0, "comment": raw[:400]}
    return {"symbols": [], "comment": raw[:400]}
