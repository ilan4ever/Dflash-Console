from pathlib import Path

from core.gguf_meta import infer_context_length_from_metadata, read_gguf_context_length


def test_infer_context_from_architecture_field():
    meta = {'qwen35.context_length': 262144, 'general.architecture': 'qwen35'}
    assert infer_context_length_from_metadata(meta) == 262144


def test_infer_context_from_qwen38_base_model_name():
    meta = {
        'general.basename': 'Qwen3.8-27B-GSQ-RCO',
        'general.base_model.0.name': 'Qwen3.8 27B',
    }
    assert infer_context_length_from_metadata(meta) == 262144


def test_read_gguf_context_length_from_disk():
    root = Path(__file__).resolve().parent.parent / 'models'
    candidates = list(root.rglob('Qwen3.8-27B-Q6_K_L.gguf'))
    if not candidates:
        return
    assert read_gguf_context_length(candidates[0]) == 262144
