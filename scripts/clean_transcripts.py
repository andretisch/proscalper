#!/usr/bin/env python3
"""Parse playlist SRT files into a clean transcript corpus."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"
OUT = ROOT / "data" / "transcripts_clean"

ARTIFACT_RE = re.compile(
    r"\[(?:музыка|Music|Аплодисменты|аплодисменты|откашливается|кашель|"
    r"смех|смеётся|тишина|пауза)[^\]]*\]",
    re.IGNORECASE,
)
WS_RE = re.compile(r"\s+")
TIME_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)

TITLES = {
    1: "Разбор сделок (пробой уровня, стакан)",
    2: "Разбор сделок",
    3: "Почему большинство трейдеров не зарабатывает",
    4: "Один торговый день вместе",
    5: "Совместная торговля Binance / Bybit",
    6: "Большой разбор сделок за неделю (2024)",
    7: "Один торговый день вместе",
    8: "Как новички сливают депозиты",
    9: "Ложный пробой уровня (Bybit)",
    10: "Журнал сделок и статистика (Bybit)",
    11: "Совместная торговля Binance / Bybit",
    12: "Большой разбор сделок",
    13: "Путь трейдера с 2019 / обзор подхода (2025)",
    14: "Live-торговля: пробои и импульсы",
    15: "Совместная торговля",
    16: "Live-торговля / один день вместе",
    17: "Разбор сделок",
    18: "Разбор сделок",
}


def ts_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def format_duration(seconds: float) -> str:
    sec = int(round(seconds))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def parse_srt(text: str) -> list[dict]:
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[dict] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        i = 0
        if lines[0].isdigit():
            i = 1
        if i >= len(lines):
            continue
        m = TIME_RE.match(lines[i])
        if not m:
            continue
        start = ts_to_seconds(*m.groups()[:4])
        end = ts_to_seconds(*m.groups()[4:])
        body = " ".join(lines[i + 1 :])
        body = ARTIFACT_RE.sub(" ", body)
        body = body.replace("\xa0", " ")
        body = WS_RE.sub(" ", body).strip()
        if not body:
            continue
        cues.append({"start": start, "end": end, "text": body})
    return cues


def merge_overlap(prev: str, nxt: str) -> str:
    if not prev:
        return nxt
    prev_l, nxt_l = prev.lower(), nxt.lower()
    max_k = min(len(prev), len(nxt))
    best = 0
    for k in range(max_k, 7, -1):
        if prev_l.endswith(nxt_l[:k]):
            best = k
            break
    if best:
        return nxt[best:].lstrip()
    prev_words, nxt_words = prev.split(), nxt.split()
    max_w = min(len(prev_words), len(nxt_words), 12)
    for w in range(max_w, 2, -1):
        if [x.lower() for x in prev_words[-w:]] == [x.lower() for x in nxt_words[:w]]:
            return " ".join(nxt_words[w:])
    return nxt


def cues_to_text(cues: list[dict]) -> str:
    parts: list[str] = []
    prev = ""
    for c in cues:
        chunk = merge_overlap(prev, c["text"])
        if not chunk:
            continue
        parts.append(chunk)
        prev = (prev + " " + chunk).strip()[-240:]
    text = WS_RE.sub(" ", " ".join(parts)).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    text = ARTIFACT_RE.sub(" ", text)
    return WS_RE.sub(" ", text).strip()


def wrap_paragraphs(text: str, target_words: int = 120) -> str:
    chunks = [c for c in re.split(r"(?<=[.!?…])\s+", text) if c.strip()]
    if len(chunks) < max(3, len(text.split()) // 200):
        words = text.split()
        return "\n\n".join(
            " ".join(words[i : i + target_words])
            for i in range(0, len(words), target_words)
        )

    paras: list[str] = []
    buf: list[str] = []
    size = 0
    for ch in chunks:
        wcount = len(ch.split())
        if buf and size + wcount > target_words:
            paras.append(" ".join(buf))
            buf, size = [ch], wcount
        else:
            buf.append(ch)
            size += wcount
    if buf:
        paras.append(" ".join(buf))
    return "\n\n".join(paras)


def first_sentences(text: str, n: int = 2) -> str:
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    if parts and len(parts[0].split()) > 5:
        return " ".join(parts[:n])[:280]
    return " ".join(text.split()[:40])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    txt_dir = OUT / "txt"
    txt_dir.mkdir(exist_ok=True)

    files = sorted(SOURCE.glob("*.srt"))
    if len(files) != 18:
        raise SystemExit(f"expected 18 srt files in {SOURCE}, got {len(files)}")

    manifest = []
    combined_parts = []

    for f in files:
        ep = int(re.match(r"^(\d{2})_", f.name).group(1))
        cues = parse_srt(f.read_text(encoding="utf-8", errors="replace"))
        plain = cues_to_text(cues)
        body = wrap_paragraphs(plain)
        title = TITLES[ep]
        duration = cues[-1]["end"] if cues else 0.0
        words = len(plain.split())

        header = (
            f"# {ep:02d}. {title}\n\n"
            f"- source_srt: `source/{f.name}`\n"
            f"- duration_approx: {format_duration(duration)}\n"
            f"- cues: {len(cues)}\n"
            f"- words: {words}\n\n"
            f"---\n\n"
        )
        (OUT / f"{ep:02d}.md").write_text(header + body + "\n", encoding="utf-8")
        (txt_dir / f"{ep:02d}.txt").write_text(body + "\n", encoding="utf-8")

        entry = {
            "id": ep,
            "title": title,
            "file": f"transcripts_clean/{ep:02d}.md",
            "txt_file": f"transcripts_clean/txt/{ep:02d}.txt",
            "source_srt": f"source/{f.name}",
            "duration_sec": round(duration, 1),
            "duration": format_duration(duration),
            "cues": len(cues),
            "words": words,
            "summary_hint": first_sentences(plain, 2),
        }
        manifest.append(entry)
        combined_parts.append(
            f"{'=' * 72}\n"
            f"ЭПИЗОД {ep:02d}: {title}\n"
            f"source: source/{f.name} | {format_duration(duration)} | {words} words\n"
            f"{'=' * 72}\n\n"
            f"{body}\n"
        )

    (OUT / "ALL.md").write_text("\n\n".join(combined_parts) + "\n", encoding="utf-8")
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    total_words = sum(e["words"] for e in manifest)
    total_dur = sum(e["duration_sec"] for e in manifest)
    idx = [
        "# Каталог транскриптов ProScalp",
        "",
        "Чистый корпус субтитров плейлиста (этап 1).",
        "",
        f"- Эпизодов: **{len(manifest)}**",
        f"- Слов: **{total_words}**",
        f"- Длительность ≈ **{format_duration(total_dur)}**",
        "",
        "| # | Название | Длительность | Слов | Файл |",
        "|---|----------|--------------|------|------|",
    ]
    for e in manifest:
        idx.append(
            f"| {e['id']:02d} | {e['title']} | {e['duration']} | "
            f"{e['words']} | `{e['id']:02d}.md` |"
        )
    idx.append("")
    (OUT / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")

    print(f"wrote {len(manifest)} episodes -> {OUT}")
    print(f"words={total_words} duration≈{format_duration(total_dur)}")


if __name__ == "__main__":
    main()
