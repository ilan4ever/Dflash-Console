from pathlib import Path
path = Path(r"C:\dev\Dflash-Console\api\gateway.py")
text = path.read_text(encoding="utf-8")
marker = "DFLASH_LOG_400_BODIES"
if marker in text:
    print("gateway 400 logger already present")
else:
    old = """    if status >= 400:
        return Response(content=content, status_code=status, media_type=media_type)
    return Response(content=content, status_code=status, media_type=media_type)


async def _forward_cloud_chat("""
    new = """    if status >= 400:
        try:
            from pathlib import Path as _P
            _log = _P(__file__).resolve().parents[1] / 'logs' / 'gateway-400-bodies.log'
            _log.parent.mkdir(parents=True, exist_ok=True)
            with _log.open('a', encoding='utf-8') as _fh:
                _fh.write(f\"status={status} url={url} body={content[:4000]!r}\\n\")
        except Exception:
            pass
        return Response(content=content, status_code=status, media_type=media_type)
    return Response(content=content, status_code=status, media_type=media_type)


async def _forward_cloud_chat("""
    # mark presence
    new = new.replace("try:", f"try:  # {marker}", 1)
    if old not in text:
        raise SystemExit("forward 400 needle not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("gateway 400 logger added")
