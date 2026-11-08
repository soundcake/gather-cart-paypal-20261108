"""Download only pinned public model data; run explicitly during local setup."""
import argparse
from huggingface_hub import snapshot_download

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--destination', default='../model-qwen-1.5b')
    args = p.parse_args()
    snapshot_download('mlx-community/Qwen2.5-1.5B-Instruct-4bit',
        revision='8b403126fc14f14cfc99bb4cfa72ecbc129ea677', local_dir=args.destination,
        allow_patterns=['*.json', '*.safetensors', '*.txt', '*.jinja', 'LICENSE*', 'README.md'],
        max_workers=2, token=False)
