"""Ollama Cloud chat client."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.http_client import SessionPool, request_with_retry
from app.logging_setup import get_logger

log = get_logger("ai")


class OllamaClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._sessions = SessionPool(settings)

    @property
    def session(self):
        return self._sessions.get()

    def chat(
        self,
        messages: list[dict[str, str]],
        timeout: int = 60,
        deadline: float | None = None,
    ) -> str:
        # Ответ модели приходит одним куском, поэтому read-таймаут здесь
        # свой и заметно больше, чем у биржевых запросов.
        r = request_with_retry(
            self.session,
            "POST",
            f"{self.s.ollama_host}/api/chat",
            timeout=(self.s.http_connect_timeout_sec, float(timeout)),
            attempts=2,
            deadline=deadline,
            label="ollama/api/chat",
            headers={
                "Authorization": f"Bearer {self.s.ollama_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.s.ollama_model,
                "messages": messages,
                "stream": False,
            },
        )
        r.raise_for_status()
        data = r.json()
        return (data.get("message") or {}).get("content") or ""

    def decide_trade(
        self,
        signal: dict[str, Any],
        context: str,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        system = (
            "Ты риск-офицер скальп-бота ProScalp (playbook S1–S5). "
            "Ответь СТРОГО JSON без markdown:\n"
            '{"decision":"go"|"no_go","confidence":0-1,"comment":"кратко на русском"}\n'
            "Вход: боковик→S2; тренд/in-play→S1/S4; drain→S5. "
            "S2/S3 работают ОТ плотности: пустое поле wall, свежая или тонкая "
            "плотность, вход «до закола» без касания — no_go. "
            "S1/S4/S5 — импульсные сетапы, плотность им НЕ нужна: "
            "wall=null здесь норма, оценивай импульс и структуру, "
            "а не отсутствие стенки. "
            "S2 против сильного тренда — no_go. Пустой стакан / неликвид — no_go. "
            "Механические фильтры уже отсеяли неокупаемые сетапы: "
            "risk_pct — расстояние до стопа, rr — отношение цели к риску, "
            "room_pct — реальный размах цены за последние 15 минут, "
            "wall.observations/age_sec — сколько плотность уже стоит. "
            "Твоя задача — отсечь то, что фильтры не видят: "
            "вход против сильного импульса, плотность на пути тренда, "
            "слишком узкий стакан относительно цели."
        )
        user = f"Сигнал:\n{json.dumps(signal, ensure_ascii=False)}\n\nКонтекст:\n{context}"
        raw = self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            timeout=45,
            deadline=deadline,
        )
        return _parse_json_decision(raw)

    def decide_manage(
        self, position: dict[str, Any], deadline: float | None = None
    ) -> dict[str, Any]:
        system = (
            "Ты риск-офицер ProScalp, ведёшь ОТКРЫТУЮ paper-сделку. "
            "Ответь СТРОГО JSON:\n"
            '{"action":"hold"|"flatten"|"be","comment":"кратко на русском"}\n'
            "Правила playbook: нет импульса → flatten; после импульса стоп в BE; "
            "S2 — часто full flat на ретесте, не держать «на глобальный разворот»; "
            "стенку сняли/переставили — flatten, не двигать стоп дальше; "
            "запрет widen стопа; запрет усреднения. "
            "pnl_if_exit_now уже NET после taker round-trip. "
            "ВАЖНО: сразу после входа pnl минусовой на размер спреда и комиссии — "
            "это норма, а не причина закрывать. Закрывай, если сетап сломан "
            "(стенка ушла, агрессия против) или импульса нет к концу окна "
            "no_impulse_window_sec. Пока идёт окно и структура цела — hold."
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
            deadline=deadline,
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
            f"{limit} символов для скальпа по плотностям. Ответ СТРОГО JSON:\n"
            '{"symbols":["SOLUSDT",...],"comment":"почему"}\n'
            "Кандидаты уже отфильтрованы по обороту и суточному размаху "
            "(range24_pct) — выбирай из списка, не придумывай символы. "
            "Приоритет: широкий размах при живом обороте. "
            "Избегай монет, где движение уже закончилось одним свечным гэпом."
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
