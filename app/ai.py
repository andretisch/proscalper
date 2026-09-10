"""Ollama Cloud chat client."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.http_client import make_session


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = make_session(settings)

    def chat(self, messages: list[dict[str, str]], timeout: int = 60) -> str:
        url = f"{self.s.ollama_host}/api/chat"
        payload = {
            "model": self.s.ollama_model,
            "messages": messages,
            "stream": False,
        }
        r = self.session.post(
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
            "Ты риск-офицер скальп-бота ProScalp (playbook S1–S5). "
            "Ответь СТРОГО JSON без markdown:\n"
            '{"decision":"go"|"no_go","confidence":0-1,"comment":"кратко на русском"}\n'
            "Вход: боковик→S2; тренд/in-play→S1/S4; drain→S5. "
            "S2 против сильного тренда — no_go. Свежая/тонкая стенка — no_go. "
            "Нет касания плотности (вход «до закола») — no_go. "
            "Пустой стакан / неликвид — no_go. "
            "Ожидаемый ход должен покрыть комиссию round-trip ×3, иначе no_go."
        )
        user = f"Сигнал:\n{json.dumps(signal, ensure_ascii=False)}\n\nКонтекст:\n{context}"
        raw = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        return _parse_json_decision(raw)

    def decide_manage(self, position: dict[str, Any]) -> dict[str, Any]:
        system = (
            "Ты риск-офицер ProScalp, ведёшь ОТКРЫТУЮ paper-сделку. "
            "Ответь СТРОГО JSON:\n"
            '{"action":"hold"|"flatten"|"be","comment":"кратко на русском"}\n'
            "Правила playbook: нет импульса → flatten; после импульса стоп в BE; "
            "S2 — часто full flat на ретесте, не держать «на глобальный разворот»; "
            "стенку сняли/переставили — flatten, не двигать стоп дальше; "
            "запрет widen стопа; запрет усреднения. "
            "pnl_if_exit_now уже NET после taker round-trip. "
            "Если сомнение и нет импульса — flatten, не пересиживать."
        )
        raw = self.chat(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(position, ensure_ascii=False),
                },
            ],
            timeout=45,
        )
        data = _parse_json_decision(raw, default_key="action")
        action = str(data.get("action") or data.get("decision") or "hold").lower()
        if action in {"go", "yes", "approve"}:
            action = "hold"
        if action in {"no_go", "close", "exit", "flat"}:
            action = "flatten"
        if action not in {"hold", "flatten", "be"}:
            action = "hold"
        data["action"] = action
        return data

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
    if default_key == "action":
        return {"action": "hold", "comment": raw[:400]}
    return {"symbols": [], "comment": raw[:400]}
