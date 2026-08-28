"""Fuzzy matching for models, engines, and runtimes."""

from __future__ import annotations

from typing import Any


def normalize(value: Any) -> str:
    return ' '.join(str(value or '').strip().lower().replace('\\', '/').split())


def score_name(query: str, *candidates: Any) -> int:
    needle = normalize(query)
    if not needle:
        return 0
    best = 0
    for raw in candidates:
        text = normalize(raw)
        if not text:
            continue
        if text == needle:
            return 100
        if text.startswith(needle) or needle.startswith(text):
            best = max(best, 90)
        elif needle in text:
            best = max(best, 80)
        elif all(part in text for part in needle.split() if part):
            best = max(best, 70)
    return best


def pick_one(query: str, rows: list[dict[str, Any]], fields: tuple[str, ...], *, kind: str) -> dict[str, Any]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        score = score_name(query, *(row.get(field) for field in fields))
        if score:
            ranked.append((score, row))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        raise ValueError(f'No {kind} matches {query!r}. Try: dflash list')
    top = ranked[0][0]
    winners = [row for score, row in ranked if score == top]
    if len(winners) > 1 and top < 100:
        labels = ', '.join(
            str(row.get('label') or row.get('id') or row.get('filename') or '?')
            for row in winners[:8]
        )
        raise ValueError(f'{query!r} is ambiguous. Matches: {labels}')
    return winners[0]


def pick_model(query: str, models: list[dict[str, Any]]) -> dict[str, Any]:
    return pick_one(query, models, ('id', 'label', 'filename', 'path', 'server_id'), kind='model')


def pick_engine(query: str, engines: list[dict[str, Any]]) -> dict[str, Any]:
    return pick_one(query, engines, ('id', 'label', 'model_id'), kind='engine')


def pick_runtime(query: str, runtimes: list[dict[str, Any]]) -> dict[str, Any]:
    return pick_one(query, runtimes, ('id', 'runtime_id', 'label'), kind='runtime')


def pick_node(query: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return pick_one(query, nodes, ('id', 'label', 'base_url'), kind='node')
