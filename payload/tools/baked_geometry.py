"""Convert Minecraft's exported baked models into the native renderer's triangle ABI."""
import argparse
import json
import math
from pathlib import Path
import struct
from scene_geometry import Alignment, vector

STRIDE = 144


def pack_quads(quads, transform, limit=4096):
    if not isinstance(limit, int) or not 0 <= limit <= 4096:
        raise ValueError('Triangle budget must be 0..4096')
    output = bytearray()
    count = 0
    for quad in quads:
        vertices = quad['vertices']
        if len(vertices) != 4:
            raise ValueError('A baked quad must have four vertices')
        tint = int(quad['tint'])
        if not 0 <= tint <= 0xffffff:
            raise ValueError('Invalid RGB tint')
        positions, colors, uvs = [], [], []
        for vertex in vertices:
            if len(vertex) != 6:
                raise ValueError('Unsupported baked vertex format')
            positions.append((*vector(transform(vector(vertex[:3]))), 1.0))
            u, v = float(vertex[3]), float(vertex[4])
            if not all(math.isfinite(x) and 0 <= x <= 1 for x in (u, v)):
                raise ValueError('Invalid atlas coordinates')
            packed = int(vertex[5])
            if not 0 <= packed <= 0xffffffff:
                raise ValueError('Invalid ABGR vertex color')
            colors.append(tuple(((packed >> shift) & 255) / 255 * ((tint >> t) & 255) / 255
                                for shift, t in ((0,16),(8,8),(16,0))) + (((packed >> 24) & 255) / 255,))
            # uv.z marks translucent geometry (water, glass, ice) for the blended pass.
            uvs.append((u,v,1.0 if quad.get('layer')=='translucent' else 0.0,0.0))
        for indices in ((0,1,2),(0,2,3)):
            if count >= limit:
                return bytes(output), count
            values = [x for attributes in (positions,colors,uvs) for i in indices for x in attributes[i]]
            output.extend(struct.pack('<36f', *values))
            count += 1
    return bytes(output), count


def block_mesh(scene, alignment, limit=4096):
    output, count = bytearray(), 0
    for block in scene['blocks']:
        if count >= limit:
            break
        origin = vector(block['position'])
        def transform(point):
            return alignment.to_echo(tuple(a+b for a,b in zip(origin,point)))
        data, added = pack_quads(block.get('quads', []), transform, limit-count)
        output.extend(data)
        count += added
    return bytes(output), count


def item_mesh(item, hand_matrix, limit=4096):
    """Hand-relative preview. Caller supplies a calibrated, row-major hand-to-Echo matrix.

    This applies the exported vanilla display transform; Vivecraft's weapon-specific
    grip offsets and animations need a separate calibration layer.
    """
    if len(hand_matrix) != 16 or not all(math.isfinite(x) for x in hand_matrix):
        raise ValueError('Expected finite 4x4 hand matrix')
    if any(abs(hand_matrix[12+i]-x) > 1e-6 for i,x in enumerate((0,0,0,1))):
        raise ValueError('Hand matrix must be affine')
    if item.get('empty') or item.get('customRenderer'):
        return b'', 0
    scale = vector(item['scale'])
    translation = list(vector(item['translation']))
    rotations = list(vector(item['rotationDegrees']))
    if item.get('leftHandMirror'):
        translation[0] *= -1
        rotations[1] *= -1
        rotations[2] *= -1
    rx,ry,rz = (math.radians(x) for x in rotations)
    cx,sx,cy,sy,cz,sz = math.cos(rx),math.sin(rx),math.cos(ry),math.sin(ry),math.cos(rz),math.sin(rz)
    def transform(point):
        x,y,z = ((v-.5)*s for v,s in zip(point,scale))
        # JOML rotationXYZ is Rx*Ry*Rz, so column vectors encounter Z first.
        x,y = cz*x-sz*y, sz*x+cz*y
        x,z = cy*x+sy*z, -sy*x+cy*z
        y,z = cx*y-sx*z, sx*y+cx*z
        p = [x+translation[0],y+translation[1],z+translation[2],1]
        return tuple(sum(hand_matrix[r*4+c]*p[c] for c in range(4)) for r in range(3))
    return pack_quads(item['quads'], transform, limit)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scene', type=Path)
    parser.add_argument('alignment', type=Path, help='JSON containing echo_head, minecraft_head, echo_forward, minecraft_forward')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data,count = block_mesh(json.loads(args.scene.read_text()), Alignment(**json.loads(args.alignment.read_text())))
    args.output.write_bytes(data)
    print(f'{count} triangles, {len(data)} bytes; ECTriangle stride={STRIDE}')
