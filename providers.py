"""Bounded activity selection through a local model or explicit sample fixtures."""
import json
import time
import urllib.request
from cart import parse_selection
from planner import SYSTEM

SAMPLES = {
    'sketching': 'A quiet drawing workshop for our community group. We want to sketch with pencils.',
    'seed-starting': 'Help our community group plant basil seeds and start a small herb garden.',
    'bookbinding': 'We want to make simple handmade notebooks together at a community workshop.',
}


class Samples:
    def choose(self, brief):
        activity = next((a for a, text in SAMPLES.items() if text == brief), None)
        if not activity:
            raise ValueError('Sample rehearsal uses the three example briefs. Connect a local model for your own brief.')
        return {'activity': activity, 'reason': 'Sample selection for this workshop brief.'}, 0


class MLXModel:
    def __init__(self, path):
        import mlx.core as mx
        from mlx_lm import load
        from pathlib import Path
        if not path or not Path(path).is_dir():
            raise ValueError('Provide a downloaded MLX model directory.')
        self.mx = mx
        mx.set_memory_limit(2 * 1024**3)
        mx.set_cache_limit(128 * 1024**2)
        self.model, self.tokenizer = load(path, tokenizer_config={'trust_remote_code': False})

    def choose(self, brief):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler
        messages = [{'role': 'system', 'content': SYSTEM},
                    {'role': 'user', 'content': json.dumps({'workshop_brief': brief})}]
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        self.mx.random.seed(0)
        started = time.monotonic(); parts = []; finish = None
        for result in stream_generate(self.model, self.tokenizer, prompt=prompt,
                                      max_tokens=160, sampler=make_sampler(temp=0)):
            parts.append(result.text); finish = result.finish_reason
        if finish != 'stop':
            raise ValueError('The local model did not finish. Try a shorter brief.')
        return parse_selection(''.join(parts)), round(time.monotonic() - started, 2)


class LocalModel:
    def __init__(self, model):
        if not model or len(model) > 100:
            raise ValueError('Choose an installed local Ollama model.')
        self.model = model
        # Direct loopback connection; no proxy, remote model download or redirect.
        from paypal import NoRedirect
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def choose(self, brief):
        schema = {'type': 'object', 'additionalProperties': False,
                  'required': ['activity', 'reason'], 'properties': {
                      'activity': {'type': 'string', 'enum': ['sketching', 'seed-starting', 'bookbinding', 'unsupported']},
                      'reason': {'type': 'string', 'maxLength': 350}}}
        body = {'model': self.model, 'system': SYSTEM, 'prompt': json.dumps({'workshop_brief': brief}),
                'stream': False, 'format': schema, 'options': {'temperature': 0, 'num_predict': 160}}
        started = time.monotonic()
        request = urllib.request.Request('http://127.0.0.1:11434/api/generate',
            data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with self.opener.open(request, timeout=60) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('The local model returned too much data.')
        data = json.loads(raw)
        if data.get('done') is not True or data.get('done_reason') == 'length':
            raise ValueError('The local model did not complete the selection.')
        return parse_selection(data.get('response', '')), round(time.monotonic() - started, 2)
