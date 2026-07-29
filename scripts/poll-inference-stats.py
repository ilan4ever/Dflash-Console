import json
import time
import urllib.request

for i in range(15):
    data = json.loads(urllib.request.urlopen('http://127.0.0.1:8900/api/servers').read())
    server = next(item for item in data['servers'] if item['id'] == 'gemma-31b-dflash')
    stats = server.get('inference_stats') or {}
    print(
        f"{i}: generating={stats.get('generating')} secs={stats.get('generating_seconds')} "
        f"out={stats.get('generation_tokens')} tps={stats.get('tokens_per_second')}"
    )
    time.sleep(1)
