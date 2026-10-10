from pathlib import Path
p = Path("tests/test_chat_concurrent.py")
text = p.read_text(encoding="utf-8")
old = '''@pytest.mark.asyncio
async def test_chat_server_gate_serializes_waiters():
    reset_chat_server_gates_for_tests()
    order: list[str] = []
    gate = chat_server_gate('gemma-12b-ar')

    async def _holder(name: str, hold: float):
        await gate.acquire()
        order.append(f'{name}-in')
        await asyncio.sleep(hold)
        order.append(f'{name}-out')
        gate.release()

    await asyncio.gather(_holder('a', 0.05), _holder('b', 0.01))
    assert order[0] == 'a-in'
    assert 'a-out' in order
    assert order.index('a-out') < order.index('b-in') or order.index('b-out') < order.index('a-in')
    # Fair mutual exclusion: never interleaved in/out across holders.
    assert order in (
        ['a-in', 'a-out', 'b-in', 'b-out'],
        ['b-in', 'b-out', 'a-in', 'a-out'],
    )
'''
new = '''def test_chat_server_gate_serializes_waiters():
    reset_chat_server_gates_for_tests()
    order: list[str] = []
    gate = chat_server_gate('gemma-12b-ar')

    async def _run():
        async def _holder(name: str, hold: float):
            await gate.acquire()
            order.append(f'{name}-in')
            await asyncio.sleep(hold)
            order.append(f'{name}-out')
            gate.release()

        await asyncio.gather(_holder('a', 0.05), _holder('b', 0.01))

    asyncio.run(_run())
    assert order in (
        ['a-in', 'a-out', 'b-in', 'b-out'],
        ['b-in', 'b-out', 'a-in', 'a-out'],
    )
'''
if old not in text:
    raise SystemExit('gate test block not found')
# also drop unused imports
text = text.replace(old, new, 1)
text = text.replace('import threading\n', '')
text = text.replace('from unittest.mock import patch\n\n', '')
p.write_text(text, encoding='utf-8')
print('rewrote gate test')
