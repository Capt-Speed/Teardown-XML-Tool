"""Independent compact VOX reader for regression tests, not the exporter.

Matches the observed native integer-cell rotation / occupied-bound rebasing.
It is an offline reference, not a claim that the game has been executed.
"""
import struct,zlib,itertools,math
from pathlib import Path
from collections import Counter

class Model:
    def __init__(self,size,data):self.size=size;self.data=data
    def cells(self):
        sx,sy,sz=self.size
        for z in range(sz):
            for y in range(sy):
                for x in range(sx):
                    color=self.data[x+sx*(y+sy*z)]
                    if color:yield (x,y,z,color)
    def bounds(self):
        lo=[999999]*3;hi=[-999999]*3
        for c in self.cells():
            for a in range(3):lo[a]=min(lo[a],c[a]);hi[a]=max(hi[a],c[a]+1)
        return lo,hi

def read(path):
    raw=Path(path).read_bytes();models=[];graph={};layers={};pos=20;metadata=[];size=None
    def dic(blob,off):
        n=struct.unpack_from('<i',blob,off)[0];off+=4;out={}
        for _ in range(n):
            values=[]
            for j in range(2):
                ln=struct.unpack_from('<i',blob,off)[0];off+=4;values.append(blob[off:off+ln].decode());off+=ln
            out[values[0]]=values[1]
        return out,off
    while pos<len(raw):
        tag=raw[pos:pos+4];n,c=struct.unpack_from('<ii',raw,pos+4);d=raw[pos+12:pos+12+n];pos+=12+n+c
        if tag==b'SIZE':size=struct.unpack('<3i',d)
        elif tag==b'XYZI':
            dense=bytearray(math.prod(size));sx,sy,sz=size
            for x,y,z,color in struct.iter_unpack('4B',d[4:]):dense[x+sx*(y+sy*z)]=color
            models.append(Model(size,dense))
        elif tag==b'TDCZ':
            size=struct.unpack_from('<3i',d);dense=zlib.decompressobj().decompress(d[12:]);models.append(Model(size,dense))
        elif tag in (b'nTRN',b'nGRP',b'nSHP'):
            nid=struct.unpack_from('<i',d)[0];a,o=dic(d,4)
            if tag==b'nTRN':
                child,res,layer,nf=struct.unpack_from('<4i',d,o);f,o=dic(d,o+16);graph[nid]=(tag,a,child,layer,f)
            else:
                count=struct.unpack_from('<i',d,o)[0];o+=4;ids=[]
                for _ in range(count):
                    ids.append(struct.unpack_from('<i',d,o)[0]);o+=4
                    if tag==b'nSHP':_,o=dic(d,o)
                graph[nid]=(tag,a,ids)
        elif tag==b'LAYR':
            lid=struct.unpack_from('<i',d)[0];layers[lid]=dic(d,4)[0]
        elif tag in (b'RGBA',b'MATL',b'NOTE',b'IMAP',b'MATT'):metadata.append((tag,d))
    ident=((1,0,0),(0,1,0),(0,0,1));leaves=[]
    def dot(a,b):return tuple(sum(a[i][j]*b[j] for j in range(3)) for i in range(3))
    def visit(nid,r,t,name='',hidden=False,ref=False):
        n=graph[nid];tag,a=n[:2];name=a.get('_name') or name;hidden=hidden or a.get('_hidden')=='1'
        if tag==b'nTRN':
            code=int(n[4].get('_r',4));axes=[code&3,(code>>2)&3];axes.append(3-sum(axes))
            lr=tuple(tuple((-1 if code&(16<<i) else 1) if j==axes[i] else 0 for j in range(3)) for i in range(3))
            rr=tuple(tuple(sum(r[i][k]*lr[k][j] for k in range(3)) for j in range(3)) for i in range(3))
            dt=dot(r,tuple(map(int,n[4].get('_t','0 0 0').split())));layer=layers.get(n[3],{})
            visit(n[2],rr,tuple(t[i]+dt[i] for i in range(3)),name,hidden or layer.get('_hidden')=='1',ref or layer.get('_name')=='$REF')
        elif tag==b'nGRP':
            for i in n[2]:visit(i,r,t,name,hidden,ref)
        else:
            for mid in n[2]:leaves.append((mid,r,t,name,hidden,ref))
    children={c for n in graph.values() for c in ([n[2]] if n[0]==b'nTRN' else n[2] if n[0]==b'nGRP' else [])}
    for root in sorted(set(graph)-children):visit(root,ident,(0,0,0))
    if not graph:leaves=[(i,ident,(0,0,0),'',False,False) for i in range(len(models))]
    return models,leaves,metadata

def parts(path,name=''):
    models,leaves,metadata=read(path);found=[];refs={}
    for mid,r,t,n,hidden,ref in leaves:
        if name and n!=name or not name and hidden:continue
        model=models[mid]
        if not name:
            if not ref:found.append([model,r,t,n,(0,0,0),model.size])
            continue
        lo,hi=model.bounds()
        cs=[tuple(sum(r[i][j]*(v[j]-model.size[j]//2) for j in range(3))+t[i] for i in range(3)) for v in itertools.product(*[(lo[i],hi[i]-1) for i in range(3)])]
        mins=tuple(min(v[i] for v in cs) for i in range(3));maxs=tuple(max(v[i] for v in cs)+1 for i in range(3))
        if ref:refs[n]=(mins,maxs)
        else:found.append([model,r,t,n,mins,maxs])
    out=[]
    for model,r,t,n,lo,hi in found:
        a,b=refs.get(n,(lo,hi));pivot=((a[0]+b[0])//2,(a[1]+b[1])//2,a[2]) if name else (0,0,0)
        # Independent native placement equation: rotate source half-cell,
        # convert to Teardown axes, then subtract half a Teardown cell.
        half=tuple(sum(row)*0.5 for row in r)
        td_half=(half[0]-0.5,half[2]-0.5,-half[1]-0.5)
        scene_offset=(td_half[0],-td_half[2],td_half[1]) if not name else (0,0,0)
        offset=tuple(t[i]-pivot[i]-sum(r[i][j]*(model.size[j]//2) for j in range(3))+int(scene_offset[i]) for i in range(3))
        out.append((model,r,offset))
    return out,metadata

def cell_points(part):
    model,r,t=part
    if r==((1,0,0),(0,1,0),(0,0,1)):
        for x,y,z,color in model.cells():yield (x+t[0],y+t[1],z+t[2],color)
        return
    for x,y,z,color in model.cells():
        p=(x,y,z)
        yield (*tuple(sum(r[i][j]*p[j] for j in range(3))+t[i] for i in range(3)),color)

def signature(path,name='',divisor=1):
    # Replicated cell blocks are compared by both their material and location.
    count=Counter();ps,meta=parts(path,name)
    for p in ps:
        for x,y,z,c in cell_points(p):count[(x//divisor,y//divisor,z//divisor,c)]+=1
    return count,meta
