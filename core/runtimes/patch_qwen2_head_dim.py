"""One-shot patch so vLLM honors an explicit config head_dim."""
import pathlib
import vllm.model_executor.models.qwen2 as qwen2

path = pathlib.Path(qwen2.__file__)
text = path.read_text(encoding='utf-8')
if 'dflash-head-dim' in text and 'dflash-attn-bias' in text:
    raise SystemExit(0)
old_sig = '        rms_norm_eps: float = 1e-6,\n    ) -> None:'
new_sig = '        rms_norm_eps: float = 1e-6,\n        head_dim: int | None = None,  # dflash-head-dim\n    ) -> None:'
old_line = '        self.head_dim = hidden_size // self.total_num_heads\n'
new_line = '        self.head_dim = int(head_dim) if head_dim else hidden_size // self.total_num_heads\n'
old_call = '            rms_norm_eps=config.rms_norm_eps,\n        )\n'
new_call = '            rms_norm_eps=config.rms_norm_eps,\n            head_dim=getattr(config, "head_dim", None),\n        )\n'
if 'dflash-head-dim' not in text:
    missing = [name for name, chunk in (('sig', old_sig), ('line', old_line), ('call', old_call)) if chunk not in text]
    if missing:
        raise SystemExit('missing ' + ','.join(missing))
    text = text.replace(old_sig, new_sig, 1).replace(old_line, new_line, 1).replace(old_call, new_call, 1)
old_bias_sig = '        head_dim: int | None = None,  # dflash-head-dim\n    ) -> None:'
new_bias_sig = (
    '        head_dim: int | None = None,  # dflash-head-dim\n'
    '        bias: bool = True,  # dflash-attn-bias\n'
    '    ) -> None:'
)
old_bias_line = (
    '            self.total_num_kv_heads,\n'
    '            bias=True,\n'
    '            quant_config=quant_config,\n'
    '            prefix=f"{prefix}.qkv_proj",\n'
)
new_bias_line = (
    '            self.total_num_kv_heads,\n'
    '            bias=bias,\n'
    '            quant_config=quant_config,\n'
    '            prefix=f"{prefix}.qkv_proj",\n'
)
old_bias_call = '            head_dim=getattr(config, "head_dim", None),\n        )\n'
new_bias_call = (
    '            head_dim=getattr(config, "head_dim", None),\n'
    '            bias=getattr(config, "attention_bias", False if getattr(config, "qk_norm", False) else True),\n'
    '        )\n'
)
if 'dflash-attn-bias' not in text:
    missing = [
        name for name, chunk in (
            ('bias-sig', old_bias_sig),
            ('bias-line', old_bias_line),
            ('bias-call', old_bias_call),
        ) if chunk not in text
    ]
    if missing:
        raise SystemExit('missing ' + ','.join(missing))
    text = text.replace(old_bias_sig, new_bias_sig, 1).replace(old_bias_line, new_bias_line, 1).replace(old_bias_call, new_bias_call, 1)
path.write_text(text, encoding='utf-8')
print('patched', path)
