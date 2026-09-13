"""Native scene placement regression; does not call the cell-signature oracle.

The half-cell terms come from the scene loader's placement arithmetic.
One-cell named inputs also have hand-calculated expected centres.
"""
import itertools, struct, tempfile, unittest
from pathlib import Path
from engine_reference import read
from voxel_scale import chunk, dictionary
from voxel_scene import bake_scene

def fixture(path, code=4):
    data=chunk(b'SIZE',struct.pack('<3i',1,3,5))
    data+=chunk(b'XYZI',struct.pack('<i',1)+bytes((0,1,4,29)))
    data+=chunk(b'nTRN',struct.pack('<i',0)+dictionary({'_name':'cell'})+struct.pack('<4i',1,-1,-1,1)+dictionary({'_r':str(code),'_t':'7 -11 3'}))
    data+=chunk(b'nSHP',struct.pack('<i',1)+dictionary({})+struct.pack('<2i',1,0)+dictionary({}))
    path.write_bytes(b'VOX '+struct.pack('<i',150)+chunk(b'MAIN',children=data))

def native_scene_centres(path):
    models,leaves,_=read(path)
    for mid,r,t,name,hidden,ref in leaves:
        if hidden or ref:continue
        model=models[mid];occupied=list(model.cells())
        grid=[tuple(t[i]+sum(r[i][j]*(v[j]-model.size[j]//2) for j in range(3)) for i in range(3)) for v in occupied]
        low=tuple(min(v[i] for v in grid) for i in range(3))
        # Native loader first builds the oriented dense model, then gives it
        # a Teardown origin and the fixed -90 degree X rotation.
        half=tuple(low[i]+sum(r[i])*0.5 for i in range(3))
        origin=(half[0]-.5,half[2]-.5,-half[1]-.5)
        for v in grid:
            local=tuple(v[i]-low[i]+.5 for i in range(3))
            yield (origin[0]+local[0],origin[1]+local[2],origin[2]-local[1])

def centroid(points):
    points=list(points)
    return tuple(sum(p[i] for p in points)/len(points) for i in range(3))

class NativeOriginTests(unittest.TestCase):
    def test_named_one_cell_has_hand_calculated_origin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);src=root/'in.vox';out=root/'out.vox'
            fixture(src)
            # Named selection ignores authoring translation/canvas margins.
            # k=1: cell centre is (0.05,0.05,-0.05) metres.
            for k,scale in ((1,1),(2,1),(4,1),(1,2.2)):
                bake_scene(src,out,k,object_name='cell')
                actual=tuple(n*.1*scale for n in centroid(native_scene_centres(out)))
                expected=(.05*k*scale,.05*k*scale,-.05*k*scale)
                for a,b in zip(actual,expected):self.assertAlmostEqual(a,b,places=12)

    def test_all_signed_orientations_full_scene_and_mirror(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);src=root/'in.vox';out=root/'out.vox'
            for axes in itertools.permutations(range(3)):
                for signs in range(8):
                    code=axes[0]+(axes[1]<<2)+(signs<<4);fixture(src,code)
                    before=centroid(native_scene_centres(src))
                    for name in ('','cell'):
                        expected=before if not name else (.5,.5,-.5)
                        for k in (1,2,3):
                            for mirror in (False,True):
                                bake_scene(src,out,k,object_name=name,mirror_x=mirror)
                                points=list(native_scene_centres(out));after=centroid(points)
                                self.assertEqual(len(points),k**3)
                                self.assertEqual(after,tuple(n*k*(-1 if mirror and i==0 else 1) for i,n in enumerate(expected)),(code,name,k,mirror))

if __name__=='__main__':unittest.main(verbosity=2)
