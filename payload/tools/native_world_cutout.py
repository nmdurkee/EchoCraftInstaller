"""Trim crossed-plant quads to opaque texel rectangles for the opaque carrier.

Leaves and axis-aligned block faces are left unchanged. Collision is untouched.
This is an interim geometric cutout, not a general translucent material shader.
"""
import math
from native_world_material import read_png


def opaque_rectangles(mask):
    """Non-overlapping rectangles exactly covering true cells in a pixel mask."""
    active = {}; result = []
    for y, row in enumerate(mask):
        runs = []; x = 0
        while x < len(row):
            if not row[x]: x += 1; continue
            start = x
            while x < len(row) and row[x]: x += 1
            runs.append((start, x))
        current = {}
        for run in runs:
            current[run] = active.pop(run, (run[0], y, run[1], y))[:3] + (y+1,)
        result.extend(active.values()); active = current
    result.extend(active.values())
    return result


def cutout_plants(batches, png, alignment):
    w, h, pixels = read_png(png)
    output = []; clipped = emitted = 0
    attributes = ('verts', 'normals', 'colors', 'uvs')
    for batch in batches:
        n = len(batch['verts']); out = {k:[] for k in (*attributes,'faces')}
        if n % 4 or batch['faces'] != [f for i in range(0,n,4) for f in ([i,i+1,i+2],[i,i+2,i+3])]:
            raise ValueError('Plant clipping requires baked source quads')
        def append(values):
            start = len(out['verts'])
            for key in attributes: out[key].extend(values[key])
            out['faces'].extend(([start,start+1,start+2],[start,start+2,start+3]))
        for i in range(0,n,4):
            original = {k:batch[k][i:i+4] for k in attributes}
            normal = alignment.direction(original['normals'][0])
            # Vanilla cross models are vertical diagonal planes in Minecraft
            # coordinates. Inverse yaw is necessary: the Echo world is rotated.
            if abs(normal[1]) > 1e-4 or min(abs(normal[0]),abs(normal[2])) < .1:
                append(original); continue
            uvs = original['uvs']; xs = [u*w for u,v in uvs]; ys = [v*h for u,v in uvs]
            x0,y0,x1,y1 = math.floor(min(xs)),math.floor(min(ys)),math.ceil(max(xs)),math.ceil(max(ys))
            if not 0 <= x0 < x1 <= w or not 0 <= y0 < y1 <= h: raise ValueError('Invalid plant sprite')
            mask = [[pixels[(y*w+x)*4+3]>=128 for x in range(x0,x1)] for y in range(y0,y1)]
            if all(all(row) for row in mask): append(original); continue
            # Every corner must map to a corner of a non-degenerate UV rectangle.
            xmin,xmax,ymin,ymax = min(xs),max(xs),min(ys),max(ys)
            if xmax-xmin < 1e-6 or ymax-ymin < 1e-6: raise ValueError('Degenerate plant UVs')
            corners = []
            for x,y in zip(xs,ys):
                a,b = round((x-xmin)/(xmax-xmin)),round((y-ymin)/(ymax-ymin))
                if abs(x-(xmax if a else xmin))>1e-3 or abs(y-(ymax if b else ymin))>1e-3:
                    raise ValueError('Nonrectangular plant UVs')
                corners.append((a,b))
            if len(set(corners)) != 4: raise ValueError('Repeated plant UV corner')
            def interpolate(key, x, y):
                u,v = (x-xmin)/(xmax-xmin),(y-ymin)/(ymax-ymin)
                return [sum(value[c]*(u if a else 1-u)*(v if b else 1-v)
                            for value,(a,b) in zip(original[key],corners)) for c in range(len(original[key][0]))]
            for left,top,right,bottom in opaque_rectangles(mask):
                lo_x,hi_x = max(xmin,x0+left),min(xmax,x0+right)
                lo_y,hi_y = max(ymin,y0+top),min(ymax,y0+bottom)
                if lo_x>=hi_x or lo_y>=hi_y: continue
                values = {k:[] for k in attributes}
                for a,b in corners:
                    x,y = hi_x if a else lo_x,hi_y if b else lo_y
                    for key in ('verts','colors'): values[key].append(interpolate(key,x,y))
                    values['normals'].append(original['normals'][0])
                    # Half a native 4x-upscaled texel avoids sampling black just
                    # outside the cutout. Geometry still reaches the exact edge.
                    sample_x = min(hi_x-.125,max(lo_x+.125,x))
                    sample_y = min(hi_y-.125,max(lo_y+.125,y))
                    values['uvs'].append([sample_x/w,sample_y/h])
                append(values); emitted += 1
            clipped += 1
        output.append(out)
    return output, dict(clippedPlantQuads=clipped, opaqueRectangles=emitted,
                        generalAlphaShader=False, leafTransparency=False)
