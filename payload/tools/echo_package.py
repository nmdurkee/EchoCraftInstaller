"""Bounded, read-only Echo package reader using Python 3.14's Zstandard.

Format and symbol algorithm verified against the owner's GeoRemoval/echovr_pkg.py.
No game-writing operations are exposed here.
"""
from compression import zstd
from dataclasses import dataclass
from pathlib import Path
import struct

DATA = Path('_data/5932408047/rad15/win10')
MANIFEST = '48037dc70b0ecab2'
MASK = (1 << 64)-1
POLY = 0x95AC9329AC4BC9B5


def _table():
    result = []
    for i in range(256):
        v = (POLY << 1) & MASK if i & 0x80 else 0
        if i & 0x40:
            v = 0xBEF5B57AF4DC5ADF if i & 0x80 else POLY
        for bit in (0x20,0x10,8,4,2,1):
            v = ((v*2)^POLY) & MASK if i & bit else (v*2) & MASK
        result.append((v*2) & MASK)
    return result


TABLE = _table()


def symbol(name, seed=MASK):
    v = seed
    for c in name.encode('ascii'):
        c = c+32 if 65 <= c <= 90 else c
        v = (TABLE[(v >> 56) & 255]^c^((v << 8) & MASK)) & MASK
    return v


def resource_type(name, gpu=False):
    return symbol('Win10GPU' if gpu else 'Win10', symbol(name))


def decompress(data, expected, maximum=512*1024*1024):
    if not 0 < expected <= maximum:
        raise ValueError('Invalid decompressed resource size')
    dec = zstd.ZstdDecompressor()
    raw = dec.decompress(data, max_length=expected+1)
    if len(raw) != expected or not dec.eof or dec.unused_data:
        raise ValueError('Compressed frame length mismatch')
    return raw


@dataclass(frozen=True)
class Resource:
    index: int
    type: int
    name: int
    location: int
    size: int
    alignment: int


class Package:
    def __init__(self, root, name=MANIFEST, manifest_bytes=None):
        self.root = Path(root)/DATA
        self.name = name
        self.manifest_bytes = (self.root/'manifests'/name).read_bytes() if manifest_bytes is None else manifest_bytes
        blob = self.manifest_bytes
        if len(blob)<24 or blob[:4] != b'ZSTD':
            raise ValueError('Unsupported manifest container')
        usize, csize = struct.unpack_from('<QQ',blob,8)
        if csize != len(blob)-24:
            raise ValueError('Manifest compressed size mismatch')
        self.raw = decompress(blob[24:],usize,64*1024*1024)
        counts = [struct.unpack_from('<Q',self.raw,o+56)[0] for o in (0,64,128)]
        self.counts = counts
        self.c_offset = 192+counts[0]*32+counts[1]*40
        if self.c_offset+counts[2]*16 != len(self.raw):
            raise ValueError('Manifest array sizes mismatch')
        self.resources = [Resource(i,*struct.unpack_from('<QQQII',self.raw,192+i*32)) for i in range(counts[0])]
        self.frames = [struct.unpack_from('<4I',self.raw,self.c_offset+i*16) for i in range(counts[2])]
        self.by_key = {}
        for r in self.resources:
            if r.location & 0xffffffff >= counts[2]:
                raise ValueError('Invalid frame reference')
            self.by_key.setdefault((r.type,r.name),r)
        self.cache = {}

    def read(self, r):
        frame = r.location & 0xffffffff
        offset = r.location >> 32
        if frame not in self.cache:
            package,start,csize,usize = self.frames[frame]
            with (self.root/'packages'/f'{self.name}_{package}').open('rb') as file:
                file.seek(start)
                comp = file.read(csize)
            if len(comp) != csize:
                raise ValueError('Truncated package frame')
            self.cache[frame] = decompress(comp,usize)
        data = self.cache[frame]
        if offset+r.size > len(data):
            raise ValueError('Resource extends beyond frame')
        return data[offset:offset+r.size]

    def get(self, type_name, name, gpu=False):
        return self.by_key[(resource_type(type_name,gpu),int(name,16) if isinstance(name,str) else name)]
