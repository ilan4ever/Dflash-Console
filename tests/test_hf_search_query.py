from core.huggingface import _is_repo_id_query, normalize_hf_search_query


def test_strips_pasted_gguf_filename():
    raw = 'zerodigest/Qwen3.8-27B-Uncensored-YMQ-MTP-GGUF - Qwen3.8-27B-Uncensored-YMQ-XXS-NO-MTP.gguf'
    assert normalize_hf_search_query(raw) == 'zerodigest/Qwen3.8-27B-Uncensored-YMQ-MTP-GGUF'
    assert _is_repo_id_query(raw) is True


def test_strips_filename_after_space():
    raw = 'zerodigest/Qwen3.8-27B-Uncensored-YMQ-MTP-GGUF  Qwen3.8-27B-Uncensored-YMQ-XXS-NO-MTP.gguf'
    assert normalize_hf_search_query(raw) == 'zerodigest/Qwen3.8-27B-Uncensored-YMQ-MTP-GGUF'


def test_plain_text_query_unchanged():
    assert normalize_hf_search_query('Qwen3.8-27B-Uncensored') == 'Qwen3.8-27B-Uncensored'
    assert _is_repo_id_query('Qwen3.8-27B-Uncensored') is False
