"""Inspect the running Minecraft bridge without printing its authentication token."""
import argparse
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'client/PrismLauncher/instances/EchoCraft/.minecraft/config/echocraft-bridge.json'


class Bridge:
    def __init__(self, config=DEFAULT_CONFIG):
        discovery = json.loads(Path(config).read_text(encoding='utf-8'))
        if discovery.get('protocol') != 1:
            raise ValueError('Unsupported bridge protocol')
        port = discovery['port']
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Invalid port')
        self.url = f'http://127.0.0.1:{port}'
        self.token = discovery['token']

    def request(self, path, data=None):
        body = None if data is None else json.dumps(data, allow_nan=False).encode()
        req = urllib.request.Request(self.url + path, body, {
            'X-EchoCraft-Token': self.token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['state', 'scene', 'items', 'assets'])
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--radius', type=int, default=4)
    parser.add_argument('--render-geometry', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = Bridge(args.config).request('/' + args.operation, {'radius': args.radius, 'renderGeometry': args.render_geometry} if args.operation == 'scene' else None)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text, encoding='utf-8')
        print(f'Saved {args.out}')
    else:
        print(text)
