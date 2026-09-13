"""Offline geometry regressions using a reader independent of the exporter."""
import unittest,tempfile,pathlib,struct,itertools,re
from collections import Counter
import xml.etree.ElementTree as ET
import engine_reference as ref
import audit_latest as world
from voxel_scale import chunk,dictionary
from voxel_scene import bake_scene
from scale_package import ScalePackage,Cancelled

def vox_points(path,a):
    k=READ_FACTOR if '_assets_' in str(path) else 1
    cells,_=ref.signature(path,a.get('object',''),k)
    s=.1*float(a.get('scale',1))*k
    return [(((x+.5)*s,(z+.5)*s,-(y+.5)*s),c)
        for (x,y,z,c),n in cells.items() for _ in range(n//(k**3))]

READ_FACTOR=1
world.vox_points=vox_points

class GeometryTests(unittest.TestCase):
    def test_short_box_dimensions_are_clamped_before_scaling(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t);src=p/'short.xml';out=p/'scaled.xml'
            src.write_text('<prefab><body rot="23 17 -41"><group pos=".1 -.2 .3"><voxbox size="8"/><voxbox size="8 3"/><voxbox size="0 2.9 -3"/><voxbox size="8" scale="1.1" tags="keep_me"/></group></body></prefab>')
            ScalePackage(src,out,2).build()
            boxes=list(ET.parse(out).iter('voxbox'))
            self.assertEqual([b.get('size') for b in boxes],['16 2 2','16 6 2','2 4 2','8 1 1'])
            self.assertEqual(boxes[-1].get('scale'),'2.2');self.assertEqual(boxes[-1].get('tags'),'keep_me')
            before,after=world.scene(src),world.scene(out)
            for key,pts in before.items():
                for (a,_),(b,_) in zip(pts,after[key]):self.assertLess(max(abs(a[i]*2-b[i]) for i in range(3)),1e-10)
            for axis in 'XYZ':
                mirrored=p/('mirror'+axis+'.xml');ScalePackage(src,mirrored,1,mirror_axis=axis).build()
                self.assertFalse(world.compare(src,mirrored,axis)[0])

    def test_nested_world_positions_materials_and_mirror(self):
        global READ_FACTOR
        with tempfile.TemporaryDirectory() as t:
            sources=world.fixture(pathlib.Path(t)/'source')
            for p in sources:p.write_text(re.sub(r'scale="[^"]+"','scale="1"',p.read_text()))
            for src in sources:
                READ_FACTOR=1;original=world.scene(src)
                for k in (2,3,4):
                    out=src.with_name(src.stem+f'_x{k}.xml');ScalePackage(src,out,k).build()
                    READ_FACTOR=k;scaled=world.scene(out)
                    self.assertEqual(original.keys(),scaled.keys())
                    for key,points in original.items():
                        remaining=scaled[key][:];self.assertEqual(len(points),len(remaining),(src,key))
                        for p,c in points:
                            choices=[(max(abs(p[i]*k-q[i]) for i in range(3)),j) for j,(q,d) in enumerate(remaining) if c==d]
                            error,j=min(choices);remaining.pop(j)
                            self.assertLess(error,2e-5,(src.name,k,key,error))
                READ_FACTOR=1
                for axis in 'XYZ':
                    out=src.with_name(src.stem+'_mirror'+axis+'.xml')
                    ScalePackage(src,out,1,mirror_axis=axis).build()
                    errors,worst=world.compare(src,out,axis)
                    self.assertFalse(errors,(src.name,axis,errors[:3]))
                    twice=src.with_name(src.stem+'_twice'+axis+'.xml')
                    ScalePackage(out,twice,1,mirror_axis=axis).build()
                    self.assertFalse(world.compare(src,twice,'none')[0],(src.name,axis,'double'))

    def test_large_tiles_seams_sparse_canvas_and_palette(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t);src=p/'large.vox';out=p/'out.vox'
            cells=[(0,1,0,3),(63,2,0,4),(64,1,0,5),(255,2,0,6)]
            meta=chunk(b'RGBA',bytes(range(256))*4)+chunk(b'NOTE',b'preserved material names')
            data=chunk(b'SIZE',struct.pack('<3i',256,4,1))+chunk(b'XYZI',struct.pack('<i',len(cells))+b''.join(bytes(c) for c in cells))+meta
            src.write_bytes(b'VOX '+struct.pack('<i',150)+chunk(b'MAIN',children=data))
            a,ma=ref.signature(src)
            result=bake_scene(src,out,4)
            b,mb=ref.signature(out,divisor=4)
            self.assertEqual(b,Counter({p:n*64 for p,n in a.items()}));self.assertEqual(ma,mb)
            self.assertGreater(result['models'],1)
            self.assertEqual(result['selection']['parts'][0]['dimensions'][0],1024)
            self.assertTrue(all(max(d)<=256 for d in result['dimensions']))
            bake_scene(out,p/'mirror.vox',mirror_x=True)
            mirrored,_=ref.signature(p/'mirror.vox');full,_=ref.signature(out)
            self.assertEqual(mirrored,Counter({(-x-1,y,z,c):n for (x,y,z,c),n in full.items()}))

    def test_joint_wheel_and_inherited_properties(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t);src=p/'in.xml';out=p/'out.xml'
            src.write_text('<prefab><group pos="2 3 -4" rot="21 43 81"><body pos=".01 -.09 1"><voxbox size="5 6 7"><joint name="hinge" type="hinge" pos=".12 -.35 2" rot="12 38 95" limits="-35 120"/><joint name="slider" type="prismatic" limits="-.1 .25"/><wheel pos=".25 .3 -.9"/><rope/></voxbox></body></group></prefab>')
            report=ScalePackage(src,out,4).build();a=ET.parse(src);b=ET.parse(out)
            for x,y in zip(a.iter(),b.iter()):
                self.assertEqual(x.tag,y.tag);self.assertEqual(x.get('rot'),y.get('rot'))
                if x.get('pos'):self.assertEqual([float(v)*4 for v in x.get('pos').split()],[float(v) for v in y.get('pos').split()])
            self.assertEqual(b.find('.//joint[@name="hinge"]').get('limits'),'-35 120')
            self.assertEqual(b.find('.//joint[@name="slider"]').get('limits'),'-0.4 1')
            self.assertEqual(b.find('.//wheel').get('travel'),'-0.4 0.4')
            self.assertEqual(b.find('.//rope').get('size'),'0.8')
            with self.assertRaises(FileExistsError):ScalePackage(src,out,4).build()
            with self.assertRaises(Cancelled):ScalePackage(src,p/'cancel.xml',4,cancel=lambda:True).build()
            self.assertFalse((p/'cancel.xml').exists())

    def test_selected_brush_and_colon_reference(self):
        with tempfile.TemporaryDirectory() as t:
            p=pathlib.Path(t);world.fixture(p)
            src=p/'brush.xml'
            src.write_text('<prefab><voxbox size="9 7 5" brush="MOD/parts.vox:part3" offset="3 1 -2"/><voxbox size="3 4 5" brush="MOD/parts.vox" object="part7"/></prefab>')
            report=ScalePackage(src,p/'brush_x4.xml',4).build()
            self.assertEqual({a['selection']['object'] for a in report['vox']},{'part3','part7'})
            self.assertTrue(all(len(a['selection']['parts'])==1 for a in report['vox']))
            from verify_export import verify_export
            self.assertTrue(verify_export(p/'brush_x4.report.json')['ok'])
            root=ET.parse(p/'brush_x4.xml')
            self.assertTrue(all(n.get('object')=='' for n in root.iter('voxbox')))
            self.assertEqual(root.find('voxbox').get('offset'),'12 4 -8')

if __name__=='__main__':unittest.main(verbosity=2)
