from core.inference_stats import (
    is_proxy_generating,
    mark_inference_end,
    mark_inference_start,
)


def test_active_inference_is_reference_counted():
    sid = 'refcount-server'
    while is_proxy_generating(sid):
        mark_inference_end(sid)

    mark_inference_start(sid)
    mark_inference_start(sid)
    assert is_proxy_generating(sid) is True

    mark_inference_end(sid)
    assert is_proxy_generating(sid) is True

    mark_inference_end(sid)
    assert is_proxy_generating(sid) is False

    # Extra ends are safe.
    mark_inference_end(sid)
    assert is_proxy_generating(sid) is False
