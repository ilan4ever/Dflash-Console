from pathlib import Path

path = Path("api/gateway.py")
text = path.read_text(encoding="utf-8")
old = """                async with client.stream('POST', url, content=body, headers=headers) as upstream:
                    upstream.raise_for_status()
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code or 500)
            logger.warning('gateway chat upstream HTTP %s for %s', status, url)
            try:
                raw = await exc.response.aread()
            except Exception:
                raw = b''
"""
new = """                async with client.stream('POST', url, content=body, headers=headers) as upstream:
                    if upstream.status_code >= 400:
                        raw_err = await upstream.aread()
                        # Re-raise as HTTPStatusError with body already buffered.
                        response = httpx.Response(
                            upstream.status_code,
                            content=raw_err,
                            request=upstream.request,
                            headers=upstream.headers,
                        )
                        raise httpx.HTTPStatusError(
                            f'Client error {upstream.status_code}',
                            request=upstream.request,
                            response=response,
                        )
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code or 500)
            logger.warning('gateway chat upstream HTTP %s for %s', status, url)
            try:
                raw = exc.response.content or b''
                if not raw:
                    raw = await exc.response.aread()
            except Exception:
                raw = b''
"""
if old not in text:
    raise SystemExit("gateway stream block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("gateway stream body capture fixed")
