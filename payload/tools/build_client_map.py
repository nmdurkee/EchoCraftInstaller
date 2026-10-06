"""EchoCraft installer: build the client map patch against THIS PC's Echo install, then install it.

The patch (the buried anchor block every live collision cube copies, the hidden arena, relocated spawns) is
made from the player's own Echo packages, so no Meta data is shipped and the append offsets match this install.
The block atlas is a plain grey image of the original size: the only block is buried and never seen, and this
keeps Minecraft's textures out of the download.

Usage: python build_client_map.py [--check]   (--check builds the plan and validates it without installing)
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
GAME = Path(r'C:\Program Files\Meta Horizon\Software\Software\ready-at-dawn-echo-arena')
BASE = ROOT/'runtime/world-replacement'
ATLAS_SIZE = (1024, 512)


def grey_atlas():
    from native_world_layers import png_rgba
    w, h = ATLAS_SIZE
    return png_rgba(w, h, bytes([128, 128, 128, 255])*(w*h))


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--check', action='store_true')
    args = p.parse_args()
    if (BASE/'install-backups/latest.json').exists():
        raise SystemExit('A map patch is already recorded for this install. Reinstall Echo from the backup first.')
    world_path = BASE/'world-input.json'
    world = json.loads(world_path.read_text())
    atlas = grey_atlas()
    (BASE/'atlas.png').write_bytes(atlas)
    world['atlasSha256'] = hashlib.sha256(atlas).hexdigest()
    world_path.write_text(json.dumps(world, indent=2)+'\n')

    import build_native_mesh, build_native_collision, stage_native_map_package, native_map_install
    build_native_mesh.main()
    build_native_collision.build(GAME, world_path, BASE/'native-collision')
    stage_native_map_package.main()
    plan = BASE/'package-plan'
    native_map_install.prepare(GAME, plan)
    if args.check:
        print('Map patch built and validated against this Echo install. Nothing installed.'); return
    transaction = native_map_install.install([(GAME, plan)], BASE/'install-backups')
    print('Map patch installed. Backup: '+str(transaction))


if __name__ == '__main__':
    try: main()
    except SystemExit: raise
    except Exception as error:
        print('Map patch failed: '+str(error), file=sys.stderr); sys.exit(1)
