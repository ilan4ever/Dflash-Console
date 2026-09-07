"""Local Hugging Face catalog index for instant typed search."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
from typing import Any

from core.config import ROOT
from core.huggingface import HF_API

logger = logging.getLogger(__name__)

_INDEX_PATH = ROOT / 'logs' / 'hf-catalog-index.sqlite'
_SEARCH_CACHE_PATH = ROOT / 'logs' / 'hf-catalog-cache.json'
_INDEX_VERSION = 1
_READY_COUNT = 1500
_PAGE_PAUSE_SECONDS = 0.12
_SOURCE_PAUSE_SECONDS = 0.4
_REFRESH_SECONDS = 6 * 60 * 60
_PAGE_TIMEOUT = 30.0
_SEARCH_SCAN = 250
_EARLY_SAVE_PAGES = 1

_SOURCES: tuple[dict[str, Any], ...] = (
    {
        'id': 'gguf',
        'max_models': 40000,
        'params': {
            'filter': 'gguf',
            'sort': 'downloads',
            'direction': '-1',
            'limit': 200,
        },
    },
    {
        'id': 'tts',
        'max_models': 6000,
        'params': {
            'filter': 'text-to-speech',
            'sort': 'downloads',
            'direction': '-1',
            'limit': 200,
        },
    },
    {
        'id': 'stt',
        'max_models': 6000,
        'params': {
            'filter': 'automatic-speech-recognition',
            'sort': 'downloads',
            'direction': '-1',
            'limit': 200,
        },
    },
    {
        'id': 'ocr',
        'max_models': 6000,
        'params': {
            'filter': 'image-to-text',
            'sort': 'downloads',
            'direction': '-1',
            'limit': 200,
        },
    },
    {
        'id': 'embed',
        'max_models': 8000,
        'params': {
            'filter': 'feature-extraction',
            'sort': 'downloads',
            'direction': '-1',
            'limit': 200,
        },
    },
)

_PAYLOAD_FIELDS = (
    'id',
    'author',
    'lab',
    'author_avatar_url',
    'label',
    'title',
    'downloads',
    'likes',
    'last_modified',
    'size_gb',
    'size_label',
    'size_bytes',
    'accelerator_only',
    'dflash_generation',
    'dflash_generation_label',
    'pipeline_tag',
    'description',
    'gguf_count',
    'file_count',
    'has_gguf',
    'has_files',
    'modality',
    'runtime_id',
    'engines',
    'kind',
    'catalog_visible',
    'downloadable',
    'runnable',
    'family',
    'task',
    'tags',
)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_loop_started = False
_building = False

_TOKEN_RE = re.compile(r'[^a-z0-9]+')
_SIZE_TOKEN_RE = re.compile(r'^(\d+(?:\.\d+)?)([bmk])$', re.I)
_E_SIZE_RE = re.compile(r'^e(\d+(?:\.\d+)?)([bmk])$', re.I)
_STOPWORDS = frozenset({'a', 'an', 'and', 'for', 'from', 'of', 'on', 'or', 'the', 'to', 'with'})
_WORD_NUMBERS = {
    'zero': '0',
    'one': '1',
    'two': '2',
    'three': '3',
    'four': '4',
    'five': '5',
    'six': '6',
    'seven': '7',
    'eight': '8',
    'nine': '9',
    'ten': '10',
    'eleven': '11',
    'twelve': '12',
}


def _fold(value: str) -> str:
    return _TOKEN_RE.sub('', str(value or '').lower())


def _tokens(value: str) -> list[str]:
    return [part for part in _TOKEN_RE.split(str(value or '').lower()) if part]


def _like_safe(value: str) -> str:
    return str(value or '').lower().replace('\\', '').replace('%', '').replace('_', '')


def _is_size_token(token: str) -> bool:
    return bool(_SIZE_TOKEN_RE.fullmatch(str(token or '').lower()))


def _expand_query_tokens(query: str) -> list[str]:
    """Turn typed search into keywords: gemma4 2b → gemma, 4, 2b."""
    expanded: list[str] = []
    seen: set[str] = set()
    for raw in _tokens(query):
        token = _WORD_NUMBERS.get(raw, raw)
        if token in _STOPWORDS:
            continue
        e_size = _E_SIZE_RE.fullmatch(token)
        if e_size:
            token = f'{e_size.group(1)}{e_size.group(2).lower()}'
        pieces = [token]
        if not _is_size_token(token):
            runs = re.findall(r'[a-z]+|\d+(?:\.\d+)?', token)
            if len(runs) >= 2:
                pieces = runs
        for piece in pieces:
            if piece in _STOPWORDS or piece in seen:
                continue
            seen.add(piece)
            expanded.append(piece)
    return expanded


def _size_boundary_re(token: str) -> re.Pattern[str] | None:
    match = _SIZE_TOKEN_RE.fullmatch(str(token or '').lower())
    if not match:
        return None
    number, unit = match.group(1), match.group(2).lower()
    return re.compile(
        rf'(?:^|[^0-9])(?:e)?{re.escape(number)}(?:\s|-)?{unit}(?:[^0-9]|$)',
        re.I,
    )


def _token_in_text(token: str, haystack: str, folded: str) -> bool:
    needle = str(token or '').lower()
    if not needle:
        return True
    size_re = _size_boundary_re(needle)
    if size_re:
        return bool(size_re.search(haystack) or size_re.search(folded))
    if needle.isdigit():
        digit_re = re.compile(rf'(?:^|[^0-9]){re.escape(needle)}(?:[^0-9]|$)')
        return bool(digit_re.search(haystack) or needle in folded)
    return needle in haystack or _fold(needle) in folded


def _row_matches_tokens(row: dict[str, Any], tokens: list[str]) -> bool:
    if not tokens:
        return True
    haystack = _haystack(row)
    folded = _fold(haystack)
    return all(_token_in_text(token, haystack, folded) for token in tokens)


def _token_sql(token: str) -> tuple[str, list[str]]:
    safe = _like_safe(token)
    if not safe:
        return '1=1', []
    size = _SIZE_TOKEN_RE.fullmatch(safe)
    if size:
        number, unit = size.group(1), size.group(2).lower()
        patterns = [
            f'%e{number}{unit}%',
            f'%-{number}{unit}%',
            f'%{number}-{unit}%',
            f'% {number}{unit}%',
            f'%/{number}{unit}%',
        ]
        clause = ' OR '.join(['haystack LIKE ?'] * len(patterns) + ['folded LIKE ?'])
        return f'({clause})', [*patterns, f'%e{number}{unit}%']
    return '(haystack LIKE ? OR folded LIKE ?)', [f'%{safe}%', f'%{_fold(safe)}%']


def _haystack(row: dict[str, Any]) -> str:
    tags = row.get('tags') if isinstance(row.get('tags'), list) else []
    return ' '.join(
        str(part or '')
        for part in (
            row.get('id'),
            row.get('author'),
            row.get('title'),
            row.get('label'),
            row.get('lab'),
            row.get('description'),
            ' '.join(str(tag) for tag in tags[:24]),
        )
    ).lower()


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_INDEX_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            author TEXT,
            downloads INTEGER,
            likes INTEGER,
            last_modified TEXT,
            pipeline_tag TEXT,
            has_gguf INTEGER,
            accelerator_only INTEGER,
            dflash_generation TEXT,
            modality TEXT,
            folded TEXT,
            haystack TEXT,
            payload TEXT NOT NULL
        )
        """
    )
    conn.execute('CREATE INDEX IF NOT EXISTS models_downloads ON models(downloads DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS models_likes ON models(likes DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS models_modified ON models(last_modified DESC)')
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    version = _meta_get(conn, 'version')
    if version != str(_INDEX_VERSION):
        conn.execute('DELETE FROM models')
        conn.execute('DELETE FROM meta')
        conn.execute('INSERT INTO meta(key, value) VALUES (?, ?)', ('version', str(_INDEX_VERSION)))
        conn.commit()
    _conn = conn
    return conn


def _meta_get(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute('SELECT value FROM meta WHERE key = ?', (key,)).fetchone()
    return str(row['value']) if row else ''


def _meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        'INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, value),
    )


def _compact_payload(summary: dict[str, Any]) -> dict[str, Any]:
    tags = [str(tag) for tag in (summary.get('tags') or []) if tag][:24]
    payload = {field: summary.get(field) for field in _PAYLOAD_FIELDS}
    payload['tags'] = tags
    payload['gguf_files'] = []
    payload['download_files'] = []
    payload['download_options'] = []
    payload['local_ready'] = False
    payload['local_installs'] = {}
    return payload


def _inflate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from core.huggingface import _format_downloads, _time_ago, estimate_disk_size_from_name

    row = dict(payload)
    row['downloads_label'] = _format_downloads(row.get('downloads'))
    row['updated_ago'] = _time_ago(str(row.get('last_modified') or '') or None)
    row.setdefault('gguf_files', [])
    row.setdefault('download_files', [])
    row.setdefault('download_options', [])
    row.setdefault('local_ready', False)
    row.setdefault('local_installs', {})
    if not _payload_has_size(row):
        est_gb, est_label = estimate_disk_size_from_name(
            str(row.get('id') or row.get('title') or ''),
            has_gguf=bool(row.get('has_gguf')),
        )
        if est_gb:
            row['size_gb'] = est_gb
            row['size_label'] = est_label
    return row


def _row_from_hf_item(raw: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    from core.dflash_generation import dflash_generation_label, repo_dflash_generation
    from core.huggingface import _summary_from_model

    if not isinstance(raw, dict):
        return None
    summary = _summary_from_model(raw)
    repo_id = str(summary.get('id') or '').strip()
    if not repo_id:
        return None
    tags = [str(tag).lower() for tag in (summary.get('tags') or [])]
    library = str(raw.get('library_name') or '').lower()
    if source == 'gguf' or 'gguf' in tags or library == 'gguf':
        summary['has_gguf'] = True
        summary['downloadable'] = True
        if not summary.get('modality'):
            summary['modality'] = 'llm'
    repo_lower = repo_id.lower()
    if 'dflash' in repo_lower or 'dspark' in repo_lower:
        gen = repo_dflash_generation(repo_id, str(summary.get('label') or ''))
        summary['accelerator_only'] = True
        summary['dflash_generation'] = gen
        summary['dflash_generation_label'] = dflash_generation_label(gen)
    return _compact_payload(summary)


def upsert_models(rows: list[dict[str, Any]]) -> int:
    """Insert compact catalog rows. Used by the builder and tests."""
    if not rows:
        return 0
    with _lock:
        conn = _connect()
        payload_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            repo_id = str(row.get('id') or '').strip()
            if not repo_id:
                continue
            payload = _compact_payload(row) if 'gguf_files' in row or 'download_files' in row else dict(row)
            payload['id'] = repo_id
            haystack = _haystack(payload)
            payload_rows.append(
                (
                    repo_id,
                    str(payload.get('author') or ''),
                    int(payload.get('downloads') or 0),
                    int(payload.get('likes') or 0),
                    str(payload.get('last_modified') or ''),
                    str(payload.get('pipeline_tag') or ''),
                    1 if payload.get('has_gguf') else 0,
                    1 if payload.get('accelerator_only') else 0,
                    str(payload.get('dflash_generation') or ''),
                    str(payload.get('modality') or ''),
                    _fold(f"{repo_id} {payload.get('title') or ''} {payload.get('label') or ''}"),
                    haystack,
                    json.dumps(payload, ensure_ascii=False),
                )
            )
        if not payload_rows:
            return 0
        conn.executemany(
            """
            INSERT INTO models (
                id, author, downloads, likes, last_modified, pipeline_tag,
                has_gguf, accelerator_only, dflash_generation, modality, folded, haystack, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                author = excluded.author,
                downloads = excluded.downloads,
                likes = excluded.likes,
                last_modified = excluded.last_modified,
                pipeline_tag = excluded.pipeline_tag,
                has_gguf = CASE WHEN excluded.has_gguf > models.has_gguf THEN excluded.has_gguf ELSE models.has_gguf END,
                accelerator_only = CASE WHEN excluded.accelerator_only > models.accelerator_only THEN excluded.accelerator_only ELSE models.accelerator_only END,
                dflash_generation = CASE
                    WHEN excluded.dflash_generation != '' THEN excluded.dflash_generation
                    ELSE models.dflash_generation
                END,
                modality = CASE WHEN excluded.modality != '' THEN excluded.modality ELSE models.modality END,
                folded = excluded.folded,
                haystack = excluded.haystack,
                payload = excluded.payload
            """,
            payload_rows,
        )
        conn.commit()
        return len(payload_rows)


def _payload_has_size(payload: dict[str, Any]) -> bool:
    label = str(payload.get('size_label') or '').strip()
    if label.startswith('~'):
        return False
    if label and label not in ('—', '-', '0 GB', '0.0 GB'):
        return True
    size_gb = payload.get('size_gb')
    return isinstance(size_gb, (int, float)) and float(size_gb) > 0


def update_model_sizes(updates: dict[str, dict[str, Any]]) -> None:
    if not updates:
        return
    with _lock:
        conn = _connect()
        for repo_id, size in updates.items():
            if not repo_id or not isinstance(size, dict):
                continue
            row = conn.execute('SELECT payload FROM models WHERE id = ?', (repo_id,)).fetchone()
            if not row:
                continue
            try:
                payload = json.loads(row['payload'])
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if isinstance(size.get('size_gb'), (int, float)) and float(size['size_gb']) > 0:
                payload['size_gb'] = float(size['size_gb'])
            if str(size.get('size_label') or '').strip():
                payload['size_label'] = str(size['size_label']).strip()
            if isinstance(size.get('size_bytes'), int) and size['size_bytes'] > 0:
                payload['size_bytes'] = int(size['size_bytes'])
            conn.execute(
                'UPDATE models SET payload = ? WHERE id = ?',
                (json.dumps(payload, ensure_ascii=False), repo_id),
            )
        conn.commit()


def resolve_repo_sizes(repo_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Return disk sizes for catalog cards, using the local index then Hub usedStorage."""
    from concurrent.futures import ThreadPoolExecutor
    from core.huggingface import fetch_used_storage_size

    wanted = []
    seen: set[str] = set()
    for repo_id in repo_ids:
        key = str(repo_id or '').strip()
        if not key or key in seen:
            continue
        seen.add(key)
        wanted.append(key)
    wanted = wanted[:25]
    result: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    with _lock:
        conn = _connect()
        for repo_id in wanted:
            row = conn.execute('SELECT payload FROM models WHERE id = ?', (repo_id,)).fetchone()
            if not row:
                missing.append(repo_id)
                continue
            try:
                payload = json.loads(row['payload'])
            except (TypeError, json.JSONDecodeError):
                missing.append(repo_id)
                continue
            if isinstance(payload, dict) and _payload_has_size(payload):
                result[repo_id] = {
                    'size_gb': payload.get('size_gb'),
                    'size_label': payload.get('size_label'),
                    'size_bytes': payload.get('size_bytes'),
                }
            else:
                missing.append(repo_id)
    if missing:
        fetched: dict[str, dict[str, Any]] = {}
        from concurrent.futures import wait

        pool = ThreadPoolExecutor(max_workers=min(8, len(missing)))
        try:
            futures = {
                pool.submit(fetch_used_storage_size, repo_id): repo_id
                for repo_id in missing
            }
            finished, _unfinished = wait(futures, timeout=5.0)
            for future in finished:
                repo_id = futures[future]
                try:
                    size = future.result(timeout=0)
                except Exception:
                    continue
                if size:
                    fetched[repo_id] = size
                    result[repo_id] = size
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if fetched:
            try:
                update_model_sizes(fetched)
            except sqlite3.Error:
                pass
        from core.huggingface import estimate_disk_size_from_name

        for repo_id in missing:
            if repo_id in result:
                continue
            est_gb, est_label = estimate_disk_size_from_name(repo_id)
            if est_gb:
                result[repo_id] = {
                    'size_gb': est_gb,
                    'size_label': est_label,
                }
    return result


def get_indexed_model(repo_id: str) -> dict[str, Any] | None:
    key = str(repo_id or '').strip().strip('/')
    if not key:
        return None
    try:
        with _lock:
            conn = _connect()
            row = conn.execute('SELECT payload FROM models WHERE id = ?', (key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    try:
        payload = json.loads(row['payload'])
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _inflate_payload(payload)


def index_count() -> int:
    try:
        with _lock:
            conn = _connect()
            row = conn.execute('SELECT COUNT(*) AS n FROM models').fetchone()
            return int(row['n'] if row else 0)
    except sqlite3.Error:
        return 0


def index_ready() -> bool:
    if index_count() >= _READY_COUNT:
        return True
    try:
        with _lock:
            conn = _connect()
            return _meta_get(conn, 'source:gguf:done') == '1'
    except sqlite3.Error:
        return False


def index_status() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        count = int((conn.execute('SELECT COUNT(*) AS n FROM models').fetchone() or {'n': 0})['n'])
        return {
            'models': count,
            'ready': count >= _READY_COUNT or _meta_get(conn, 'source:gguf:done') == '1',
            'gguf_done': _meta_get(conn, 'source:gguf:done') == '1',
            'building': _building,
        }


def _category_sql(category: str) -> str:
    cat = str(category or '').strip().lower()
    if cat in ('all', 'all-models', ''):
        return '1=1'
    if cat == 'all-gguf':
        return 'has_gguf = 1'
    if cat == 'dflash':
        return "accelerator_only = 1 AND IFNULL(dflash_generation, '') != 'dflash2'"
    if cat == 'dflash2':
        return "accelerator_only = 1 AND dflash_generation = 'dflash2'"
    if cat == 'text-generation':
        return "(pipeline_tag = 'text-generation' OR (has_gguf = 1 AND modality = 'llm'))"
    if cat == 'text-to-speech':
        return "(pipeline_tag = 'text-to-speech' OR modality = 'text-to-speech')"
    if cat == 'automatic-speech-recognition':
        return "(pipeline_tag = 'automatic-speech-recognition' OR modality = 'speech-to-text')"
    if cat == 'image-to-text':
        return "(pipeline_tag IN ('image-to-text', 'image-text-to-text') OR modality = 'vision')"
    if cat == 'feature-extraction':
        return "(pipeline_tag = 'feature-extraction' OR modality = 'embedding')"
    if cat == 'supported':
        return (
            "has_gguf = 1 OR accelerator_only = 1 OR modality IN "
            "('speech-to-text', 'text-to-speech', 'embedding', 'vision')"
        )
    return '1=1'


def _order_sql(sort: str) -> str:
    key = str(sort or 'downloads').strip()
    if key == 'likes':
        return 'likes DESC, downloads DESC'
    if key in ('lastModified', 'createdAt'):
        return 'last_modified DESC, downloads DESC'
    return 'downloads DESC'


def _rank_rows(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    needle = str(query or '').strip().lower()
    if not needle:
        return rows
    folded = _fold(needle)
    tokens = _expand_query_tokens(needle)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for row in rows:
        if tokens and not _row_matches_tokens(row, tokens):
            continue
        repo_id = str(row.get('id') or '').lower()
        title = str(row.get('title') or row.get('label') or '').lower()
        slug = repo_id.split('/')[-1] if '/' in repo_id else repo_id
        blob = f'{repo_id} {title}'
        blob_fold = _fold(blob)
        haystack = _haystack(row)
        score = 0
        if repo_id == needle or slug == needle:
            score += 1000
        if folded and folded == _fold(slug):
            score += 800
        if folded and folded in _fold(slug):
            score += 400
        if folded and folded in blob_fold:
            score += 200
        if needle in repo_id or needle in title:
            score += 120
        if tokens and all(_token_in_text(token, blob, blob_fold) for token in tokens):
            score += 160
        if tokens and all(_token_in_text(token, slug, _fold(slug)) for token in tokens):
            score += 220
        if folded and folded in _fold(haystack):
            score += 40
        scored.append((max(score, 1), int(row.get('downloads') or 0), row))
    scored.sort(key=lambda item: (-item[0], -item[1]))
    return [row for _score, _downloads, row in scored]


def search_local(
    query: str,
    *,
    category: str = 'all',
    sort: str = 'downloads',
    limit: int = 25,
) -> dict[str, Any] | None:
    """Search the on-disk Hub index. None means caller should use live Hugging Face."""
    needle = str(query or '').strip()
    try:
        with _lock:
            conn = _connect()
            count = int((conn.execute('SELECT COUNT(*) AS n FROM models').fetchone() or {'n': 0})['n'])
            ready = count >= _READY_COUNT or _meta_get(conn, 'source:gguf:done') == '1'
            if count <= 0:
                return None
            where = _category_sql(category)
            order = _order_sql(sort)
            params: list[Any] = []
            sql = f'SELECT payload, downloads FROM models WHERE {where}'
            if needle:
                folded = _fold(needle)
                like_raw = f'%{_like_safe(needle)}%'
                clauses = ['haystack LIKE ?']
                params.append(like_raw)
                if folded:
                    clauses.append('folded LIKE ?')
                    params.append(f'%{folded}%')
                token_clauses: list[str] = []
                token_params: list[Any] = []
                for token in _expand_query_tokens(needle):
                    clause, values = _token_sql(token)
                    if values:
                        token_clauses.append(clause)
                        token_params.extend(values)
                if token_clauses:
                    clauses.append('(' + ' AND '.join(token_clauses) + ')')
                    params.extend(token_params)
                sql += ' AND (' + ' OR '.join(clauses) + ')'
            sql += f' ORDER BY {order} LIMIT ?'
            params.append(_SEARCH_SCAN if needle else max(1, min(int(limit), 50)))
            fetched = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        logger.warning('hf catalog index search failed: %s', exc)
        return None

    models = []
    for item in fetched:
        try:
            payload = json.loads(item['payload'])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            models.append(_inflate_payload(payload))
    if needle:
        models = _rank_rows(needle, models)
    models = models[: max(1, min(int(limit), 50))]
    if not models and not ready:
        return None
    return {
        'success': True,
        'models': models,
        'query': needle,
        'category': category,
        'sort': sort,
        'limit': limit,
        'cached': True,
        'from_index': True,
        'index_size': count,
        'index_ready': ready,
    }


def _source_url(source: dict[str, Any], next_url: str = '') -> str:
    if next_url:
        return next_url
    params = dict(source.get('params') or {})
    params.setdefault('expand', 'usedStorage')
    return f'{HF_API}/models?{urllib.parse.urlencode(params)}'


def _fetch_source_pages(source: dict[str, Any]) -> None:
    from core.huggingface import _request_json_page

    source_id = str(source['id'])
    max_models = int(source.get('max_models') or 0)
    with _lock:
        conn = _connect()
        if _meta_get(conn, f'source:{source_id}:done') == '1':
            stored = int(_meta_get(conn, f'source:{source_id}:count') or 0)
            if stored >= min(500, max_models or stored):
                return
        next_url = _meta_get(conn, f'source:{source_id}:next')
        stored_count = int(_meta_get(conn, f'source:{source_id}:count') or 0)
    url = _source_url(source, next_url)
    pages = 0
    added = stored_count
    completed = False
    while url and (not max_models or added < max_models):
        try:
            payload, next_url = _request_json_page(url, timeout=_PAGE_TIMEOUT)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                logger.warning('hf catalog index rate-limited on %s; backing off', source_id)
                time.sleep(8.0)
                continue
            logger.warning('hf catalog index page failed for %s: %s', source_id, exc)
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.warning('hf catalog index page failed for %s: %s', source_id, exc)
            break
        if not isinstance(payload, list):
            break
        rows = []
        for item in payload:
            compact = _row_from_hf_item(item, source=source_id)
            if compact:
                rows.append(compact)
        upsert_models(rows)
        added += len(rows)
        pages += 1
        with _lock:
            conn = _connect()
            _meta_set(conn, f'source:{source_id}:count', str(added))
            _meta_set(conn, f'source:{source_id}:next', next_url or '')
            conn.commit()
        if pages == _EARLY_SAVE_PAGES:
            logger.info('hf catalog index %s: %s models (first page ready)', source_id, index_count())
        if not next_url:
            completed = True
            break
        url = next_url
        time.sleep(_PAGE_PAUSE_SECONDS)
    else:
        completed = not url or bool(max_models and added >= max_models)
    with _lock:
        conn = _connect()
        if completed:
            _meta_set(conn, f'source:{source_id}:done', '1')
            _meta_set(conn, f'source:{source_id}:next', '')
        conn.commit()
    logger.info(
        'hf catalog index %s %s: ~%s rows in this source',
        source_id,
        'done' if completed else 'paused',
        added,
    )


def build_hf_catalog_index(*, force: bool = False) -> None:
    """Page Hugging Face into the local index. Safe to call from a background thread."""
    global _building
    with _lock:
        if _building:
            return
        _building = True
        conn = _connect()
        if force:
            for source in _SOURCES:
                _meta_set(conn, f'source:{source["id"]}:done', '0')
                _meta_set(conn, f'source:{source["id"]}:next', '')
            conn.commit()
    try:
        bootstrap_from_search_cache()
    except Exception as exc:
        logger.warning('hf catalog index bootstrap failed: %s', exc)
    try:
        for source in _SOURCES:
            try:
                _fetch_source_pages(source)
            except Exception as exc:
                logger.warning('hf catalog index source %s failed: %s', source['id'], exc)
            time.sleep(_SOURCE_PAUSE_SECONDS)
        with _lock:
            conn = _connect()
            _meta_set(conn, 'built_at', str(time.time()))
            conn.commit()
        logger.info('hf catalog index ready: %s models', index_count())
    finally:
        with _lock:
            _building = False


def bootstrap_from_search_cache() -> int:
    """Seed the index from catalog snapshots already on disk (instant first search)."""
    cache_path = _SEARCH_CACHE_PATH
    if not cache_path.is_file():
        return 0
    try:
        payload = json.loads(cache_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning('hf catalog index bootstrap failed: %s', exc)
        return 0
    rows: list[dict[str, Any]] = []
    entries = payload.get('entries') if isinstance(payload, dict) else None
    if isinstance(entries, dict):
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            models = (entry.get('payload') or {}).get('models') if isinstance(entry.get('payload'), dict) else None
            if not isinstance(models, list):
                continue
            rows.extend(row for row in models if isinstance(row, dict) and row.get('id'))
    details = payload.get('details') if isinstance(payload, dict) else None
    if isinstance(details, dict):
        for entry in details.values():
            if not isinstance(entry, dict):
                continue
            model = (entry.get('payload') or {}).get('model') if isinstance(entry.get('payload'), dict) else None
            if isinstance(model, dict) and model.get('id'):
                rows.append(model)
    if not rows:
        return 0
    count = upsert_models(rows)
    logger.info('hf catalog index seeded %s rows from search cache', count)
    return count


def start_hf_catalog_index_loop(*, interval_seconds: float = _REFRESH_SECONDS) -> None:
    global _loop_started
    with _lock:
        if _loop_started:
            return
        _loop_started = True
        _connect()
    try:
        bootstrap_from_search_cache()
    except Exception as exc:
        logger.warning('hf catalog index bootstrap failed: %s', exc)

    def run() -> None:
        try:
            build_hf_catalog_index()
        except Exception as exc:
            logger.exception('hf catalog index build failed: %s', exc)
        while True:
            time.sleep(max(1800.0, float(interval_seconds)))
            try:
                build_hf_catalog_index(force=True)
            except Exception as exc:
                logger.warning('hf catalog index refresh failed: %s', exc)

    threading.Thread(target=run, daemon=True, name='hf-catalog-index').start()


def reset_index_runtime(*, path=None) -> None:
    """Test helper: close the SQLite handle and optionally retarget the file."""
    global _conn, _building, _INDEX_PATH
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
            _conn = None
        _building = False
        if path is not None:
            _INDEX_PATH = path
