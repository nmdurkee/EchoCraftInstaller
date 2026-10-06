"""Diagnostic Minecraft collision surfaces and stereo projection; no game memory access."""
import math
import struct


def vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('Expected a three-component vector')
    result = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in result):
        raise ValueError('Non-finite vector')
    return result


def normalize(v):
    v = vector(v)
    length = math.sqrt(sum(x * x for x in v))
    if length < 1e-6:
        raise ValueError('Zero direction')
    return tuple(x / length for x in v)


class Alignment:
    """A fixed initial yaw/translation. Recalibrate after a dimension/session change."""
    def __init__(self, echo_head, minecraft_head, echo_forward, minecraft_forward):
        self.echo = vector(echo_head)
        self.minecraft = vector(minecraft_head)
        e, m = normalize(echo_forward), normalize(minecraft_forward)
        if math.hypot(e[0], e[2]) < .1 or math.hypot(m[0], m[2]) < .1:
            raise ValueError('Look approximately horizontally when calibrating')
        yaw = math.atan2(m[2], m[0]) - math.atan2(e[2], e[0])
        self.c, self.s = math.cos(yaw), math.sin(yaw)

    def direction(self, v):
        x, y, z = vector(v)
        return self.c*x-self.s*z, y, self.s*x+self.c*z

    def to_minecraft(self, point):
        local = self.direction(tuple(a-b for a, b in zip(vector(point), self.echo)))
        return tuple(a+b for a, b in zip(local, self.minecraft))

    def to_echo(self, point):
        x, y, z = (a-b for a, b in zip(vector(point), self.minecraft))
        local = self.c*x+self.s*z, y, -self.s*x+self.c*z
        return tuple(a+b for a, b in zip(local, self.echo))


def box_triangles(lo, hi):
    lo, hi = vector(lo), vector(hi)
    if any(b <= a for a, b in zip(lo, hi)):
        raise ValueError('Box must have positive extent')
    # Face normals point outward: Echo static triangles are single-sided.
    faces = [((0,4,6,2),(-1,0,0)), ((1,3,7,5),(1,0,0)),
             ((0,1,5,4),(0,-1,0)), ((2,6,7,3),(0,1,0)),
             ((0,2,3,1),(0,0,-1)), ((4,5,7,6),(0,0,1))]
    vertices = [(hi[0] if i&1 else lo[0], hi[1] if i&2 else lo[1], hi[2] if i&4 else lo[2]) for i in range(8)]
    for indices, normal in faces:
        for triangle in ((indices[0],indices[1],indices[2]),(indices[0],indices[2],indices[3])):
            yield tuple(vertices[i] for i in triangle), normal


def diagnostic_mesh(scene, alignment, max_triangles=4096):
    """Float triangles for EC_Draw. This displays collision, not Minecraft render models."""
    output = bytearray()
    count = 0
    for block in scene['blocks']:
        position = vector(block['position'])
        for box in block['collision']:
            if len(box) != 6: raise ValueError('Invalid collision box')
            lo = tuple(a+b for a,b in zip(position,vector(box[:3])))
            hi = tuple(a+b for a,b in zip(position,vector(box[3:])))
            for triangle, normal in box_triangles(lo, hi):
                if count >= max_triangles: return bytes(output), count
                shade = .5 + .3*max(0,normal[1]) + .1*abs(normal[0])
                positions = [coordinate for v in triangle for coordinate in (*alignment.to_echo(v), 1)]
                colors = [shade*.45,shade,shade*.65,1]*3
                output.extend(struct.pack('<36f',*positions,*colors,*([0.0]*12)))
                count += 1
    return bytes(output), count


def stereo_matrices(head, fovs, ipd=.064, near=.05, far=256):
    """Row-major world-to-D3D-clip matrices. IPD must later come from the active headset."""
    if not math.isfinite(ipd) or not 0 < ipd < .1 or not 0 < near < far:
        raise ValueError('Invalid projection parameters')
    position = vector(head['position'])
    left, up, forward = (normalize(head[k]) for k in ('left','up','forward'))
    for a,b in ((left,up),(left,forward),(up,forward)):
        if abs(sum(x*y for x,y in zip(a,b))) > .01: raise ValueError('Head basis is not orthogonal')
    right = tuple(-x for x in left)
    matrices=[]
    for eye,sign in zip(('left','right'),(1,-1)):
        f=fovs[eye]; l,r,u,d=(float(f[k]) for k in ('leftTan','rightTan','upTan','downTan'))
        if not all(math.isfinite(v) and 0 < v < 10 for v in (l,r,u,d)): raise ValueError('Invalid eye FOV')
        eye_pos=tuple(p+sign*x*ipd/2 for p,x in zip(position,left))
        view=[(*axis,-sum(a*b for a,b in zip(axis,eye_pos))) for axis in (right,up,forward)]
        sx,sy,ox,oy=2/(l+r),2/(u+d),(l-r)/(l+r),(d-u)/(u+d)
        a,b=far/(far-near),-far*near/(far-near)
        rows=[tuple(sx*view[0][i]+ox*view[2][i] for i in range(4)),
              tuple(sy*view[1][i]+oy*view[2][i] for i in range(4)),
              tuple(a*view[2][i]+(b if i==3 else 0) for i in range(4)),view[2]]
        matrices.extend(x for row in rows for x in row)
    return matrices
