"""Small, dependency-free VOX scene metadata reader for mirror pivots.

Teardown ``object=`` selection retains the named nTRN's discrete rotation, then
rebases the rotated voxel box and uses an integer X/Y centre with a bottom-Z
pivot.  Mirroring an odd-sized X box therefore moves that integer pivot by one
voxel.  This module resolves the selected object's rotated X size so the XML
transform can compensate exactly.
"""
from __future__ import annotations
from localization import tr
from dataclasses import dataclass
from pathlib import Path
import struct
IDENTITY3 = ((1, 0, 0), (0, 1, 0), (0, 0, 1))

@dataclass(frozen=True)
class Trn:
    attrs: dict[str, str]
    child: int
    frame: dict[str, str]

@dataclass(frozen=True)
class Grp:
    attrs: dict[str, str]
    children: tuple[int, ...]

@dataclass(frozen=True)
class Shp:
    attrs: dict[str, str]
    models: tuple[tuple[int, dict[str, str]], ...]

class Reader:

    def __init__(self, data: bytes, start: int, end: int):
        self.data = data
        self.pos = start
        self.end = end

    def i32(self):
        if self.pos + 4 > self.end:
            raise ValueError('truncated VOX int32')
        value = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return value

    def text(self):
        size = self.i32()
        if size < 0 or self.pos + size > self.end:
            raise ValueError('invalid VOX string')
        value = self.data[self.pos:self.pos + size].decode('utf-8', 'replace')
        self.pos += size
        return value

    def dictionary(self):
        count = self.i32()
        if count < 0 or count > 100000:
            raise ValueError('invalid VOX dictionary')
        return {self.text(): self.text() for _ in range(count)}

def decode_rotation(code: int):
    a, b = (code & 3, code >> 2 & 3)
    if a > 2 or b > 2 or a == b:
        return IDENTITY3
    c = 3 - a - b
    signs = (-1 if code & 16 else 1, -1 if code & 32 else 1, -1 if code & 64 else 1)
    rows = [[0] * 3 for _ in range(3)]
    rows[0][a], rows[1][b], rows[2][c] = signs
    return tuple((tuple(row) for row in rows))

def named_object_rotated_x_size(path: str | Path, object_name: str):
    from voxel_scale import chunks, read_models, selected_size
    entries = chunks(Path(path).read_bytes())
    return selected_size(entries, read_models(entries), object_name)[0]

def named_object_mirror_x_shift(path: str | Path, object_name: str):
    size_x = named_object_rotated_x_size(path, object_name)
    return 2 * (size_x // 2) - size_x
