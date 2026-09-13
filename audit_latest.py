"""World-space geometry oracle. No matrix/pivot functions imported from mirror core."""
import argparse, importlib.util, sys, math, itertools, pathlib, struct, json, re
from functools import lru_cache
import xml.etree.ElementTree as ET
from collections import defaultdict

HERE=pathlib.Path(__file__).parent
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
import vox_metadata as VOX

def identity(): return [[float(i==j) for j in range(4)] for i in range(4)]
def mul(a,b): return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]
def point(m,p): return tuple(sum(m[i][j]*(*p,1)[j] for j in range(4)) for i in range(3))
def vector(s,default=(0,0,0)):
    v=[]
    for x in (s or '').split()[:3]:
        hit=re.match(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?',x)
        v.append(float(hit[0]) if hit else 0.)
    return tuple((v+list(default[len(v):]))[:3])
def local(a):
    angles=vector(a.get('rot')); r=identity()
    for axis in (1,2,0):
        t=math.radians(angles[axis]); c,s=math.cos(t),math.sin(t);q=identity()
        u,v=((1,2),(2,0),(0,1))[axis];q[u][u]=q[v][v]=c;q[u][v]=-s;q[v][u]=s;r=mul(r,q)
    for i,x in enumerate(vector(a.get('pos'))):r[i][3]=x
    return r
def resolve(path,ref):
    if ref.startswith('MOD/'):
        for p in [path.parent,*path.parents]:
            if (p/'info.txt').exists():return p/ref[4:]
    if ref.startswith('LEVEL/'): return path.parent/path.stem/ref[6:]
    if ref.startswith('BUILT-IN/'):return pathlib.Path(r'E:\SteamLibrary\steamapps\common\Teardown\data\built-in')/ref[9:]
    q=pathlib.Path(ref);return q if q.is_absolute() else path.parent/q
def vox_points(path,a):
    from engine_reference import signature
    cells,_=signature(path,a.get('object',''))
    scale=.1*float(a.get('scale',1))
    return [(((x+.5)*scale,(z+.5)*scale,-(y+.5)*scale),c)
        for (x,y,z,c),n in cells.items() for _ in range(n)]

def scene(path):
    found={}
    def document(path,parent,inh,prefix):
        root=ET.parse(path).getroot();els=list(root) if root.tag in ('scene','prefab') else [root]
        suppressed=root.tag=='prefab' and len(els)==1 and els[0].tag=='group' and els[0].get('name','').startswith('instance=')
        for i,n in enumerate(els):walk(n,parent,inh,path,prefix+f'{i}:',suppressed)
    def walk(n,parent,inh,path,prefix,suppress=False):
        a={**inh,**n.attrib};w=mul(parent,identity() if suppress else local(a));kind=n.tag
        key=prefix+a.get('name',kind)
        pts=[]
        if kind=='vox':pts=vox_points(resolve(path,a['file']),a)
        elif kind in ('voxbox','box'):
            tokens=a.get('size','50 30 20').split()
            size=tuple(max(1,int(re.match(r'^[+-]?\d+',tokens[i])[0])) if i<len(tokens) else 1 for i in range(3))
            s=.1*float(a.get('scale',1));pts=[(p,0) for p in itertools.product(*[(0,t*s) for t in size])]
        elif kind in ('voxagon','trigger','boundary','water'):
            ys=(0,.1*float(a.get('extrude',1))) if kind=='voxagon' else (0,)
            pts=[((float(v.get('pos').split()[0]),y,float(v.get('pos').split()[1])),0) for v in n if v.tag=='vertex' for y in ys]
        elif kind in ('location','joint','wheel'):pts=[((0,0,0),0)]
        if pts:found[key]=[(point(w,p),c) for p,c in pts]
        if kind=='instance':document(resolve(path,a['file']),w,inh,key+'/')
        child=dict(inh)
        if kind=='group':
            props=sorted((int(k[4:]),v) for k,v in n.attrib.items() if k.startswith('prop') and k[4:].isdigit())
            for _,v in props:
                k,val=v.split('=',1);child[k.strip()]=val.strip()
        for i,ch in enumerate(n):
            if ch.tag!='vertex':walk(ch,w,child,path,key+f'/{i}:')
    document(path,identity(),{},'');return found
def compare(src,dst,axis):
    a,b=scene(src),scene(dst);fail=[];worst=0
    if a.keys()!=b.keys():return [('structure',str(a.keys()),str(b.keys()))],float('inf')
    for key,pts in a.items():
        remaining=list(b[key]);error=0
        if len(pts)!=len(remaining):fail.append((key,'count'));continue
        for p,color in pts:
            p=list(p)
            if axis in 'XYZ':p['XYZ'.index(axis)]*=-1
            choices=[(max(abs(p[i]-q[i]) for i in range(3)),j) for j,(q,c) in enumerate(remaining) if c==color]
            if not choices:error=float('inf');break
            d,j=min(choices);remaining.pop(j);error=max(error,d)
        worst=max(worst,error)
        if error>2e-5:fail.append((key,error))
    return fail,worst
def i(n):return struct.pack('<i',n)
def d(a):return i(len(a))+b''.join(i(len(k.encode()))+k.encode()+i(len(v.encode()))+v.encode() for k,v in a.items())
def chunk(k,c):return k.encode()+i(len(c))+i(0)+c
def fixture(folder):
    folder.mkdir(parents=True,exist_ok=True);(folder/'info.txt').write_text('name = Mirror regression')
    codes=[];seen=set()
    for code in range(128):
        if (code&3)>2 or ((code>>2)&3)>2 or (code&3)==((code>>2)&3):continue
        r=VOX.decode_rotation(code)
        det=r[0][0]*(r[1][1]*r[2][2]-r[1][2]*r[2][1])-r[0][1]*(r[1][0]*r[2][2]-r[1][2]*r[2][0])+r[0][2]*(r[1][0]*r[2][1]-r[1][1]*r[2][0])
        if abs(det)==1 and r not in seen:codes.append(code);seen.add(r)
    data=b''
    for sz in [(3,4,5),(4,5,2)]:
        data+=chunk('SIZE',b''.join(i(v) for v in sz))+chunk('XYZI',i(3)+bytes((0,0,0,1,sz[0]-1,1,1,2,1,sz[1]-1,sz[2]-1,3)))
    data+=chunk('nTRN',i(0)+d({})+i(1)+i(-1)+i(0)+i(1)+d({'_t':'4 7 -2','_r':'17'}))
    data+=chunk('nGRP',i(1)+d({})+i(len(codes))+b''.join(i(2+2*j) for j in range(len(codes))))
    for j,code in enumerate(codes):
        data+=chunk('nTRN',i(2+2*j)+d({'_name':f'part{j}'})+i(3+2*j)+i(-1)+i(0)+i(1)+d({'_r':str(code),'_t':f'{j*8} {j} {-j}'}))
        data+=chunk('nSHP',i(3+2*j)+d({})+i(1)+i(j%2)+d({}))
    (folder/'parts.vox').write_bytes(b'VOX '+i(150)+b'MAIN'+i(0)+i(len(data))+data)
    leaf=''.join(f'<vox name="named{j}" file="MOD/parts.vox" object="part{j}" pos="{j*.13} 1 -2" rot="{j*7} {j*11} {90 if j==1 else j*3}" scale="{.5+j*.05}"/>' for j in range(len(codes)))
    cases={
      'named':f'<prefab><group rot="31 47 11" pos="3 5 9"><body rot="-20 13 4" pos="1 -5 2">{leaf}</body></group></prefab>',
      'full_scene':'<prefab><group rot="31 47 11" pos="3 5 9"><body rot="-20 13 4"><vox name="whole" file="MOD/parts.vox" scale="1.7" pos="1 2 3"><location name="socket" pos="4 3 2"/></vox></body></group></prefab>',
      'inherited':'<prefab><group prop0="file=MOD/parts.vox" prop1="object=part0" prop2="pos=1 2 3" prop3="rot=11 27 39"><body><vox name="inherited"><location name="child" pos="1 0 0"/></vox></body><group prop0="object=part1"><vox name="nearer"/></group></group></prefab>',
      'primitives':'<prefab><group rot="31 47 11"><body rot="-20 13 4"><voxbox name="outer" size="7 5 3" scale="1.7" rot="12 49 81"><group pos="2 3 1" rot="-34 71 12"><voxbox name="inner" size="3 8 7" rot="90 0 90"><location name="socket" pos="4 3 2"/></voxbox></group></voxbox><voxagon name="poly" rot="7 23 89" extrude="8"><vertex pos="-3 -1"/><vertex pos="2 1"/><vertex pos="1 4"/></voxagon></body></group></prefab>',
      'polygon':'<scene><group rot="31 47 11"><trigger name="trigger" type="polygon" rot="8 13 19"><vertex pos="-3 -1"/><vertex pos="2 1"/><vertex pos="1 4"/></trigger></group></scene>',
      'mixed':'<scene><group rot="-17 34 -89.99999"><vox name="named" file="MOD/parts.vox" object="part0"/><vox name="scene" file="MOD/parts.vox" pos="5 4 3"/></group></scene>',
      'trailing_dot':'<scene><vox name="dot" file="MOD/parts.vox." object="part0" rot="15 26 74"/></scene>',
      'root_shape':'<vox file="MOD/parts.vox" object="part0" name="root" rot="27 89 90" pos="4 3 -7"><body rot="6 7 8"><voxbox name="box" size="3 5 9" pos="1 2 3"/></body></vox>',
      'polygon_tags':'<scene><voxagon name="p" extrude="3"><vertex pos="-1 3" tags="first"/><vertex pos="4 -2" tags="second"/><vertex pos="5 6" tags="third"/></voxagon></scene>',
    }
    deep='<voxbox name="deepbox" size="3 7 5" scale="0.4"><location name="socket" pos="1 -2 4"/></voxbox>'
    for j in range(48):
        kind=('group','body','voxbox')[j%3]
        deep=f'<{kind} name="layer{j}" rot="{j*7} {j*13} {j*3}" pos="{j*.27} -1.1 0.3" size="7 4 3">{deep}</{kind}>'
    cases['deep']='<prefab>'+deep+'</prefab>'
    for name,text in cases.items():(folder/(name+'.xml')).write_text(text)
    (folder/'nested.xml').write_text('<prefab><group name="instance=MOD/nested.xml" pos="999 111 222" rot="20 30 50"><voxbox name="box" size="7 4 3" rot="23 51 -13"/></group></prefab>')
    (folder/'instances.xml').write_text('<prefab><body rot="17 32 89"><instance name="a" file="MOD/nested.xml" pos="2 3 4" rot="19 80 27"/><instance name="b" file="MOD/nested.xml" pos="-3 4 1" rot="27 -9 18"/></body></prefab>')
    return [folder/(s+'.xml') for s in [*cases,'instances']]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('core');ap.add_argument('label');args=ap.parse_args();sys.path.insert(0,str(pathlib.Path(args.core).parent));m=load('tested_core',args.core)
    sources=fixture(HERE/'audit_fixtures');report={'label':args.label,'cases':[]}
    for src in sources:
        for axis in 'XYZ':
            dst=src.parent/f'{src.stem}_{args.label}_{axis}.xml'
            try:
                m.build_mirror_package(src,dst,axis);fail,error=compare(src,dst,axis)
                report['cases'].append({'case':src.stem,'axis':axis,'failures':fail,'max_error':error})
                if args.label.startswith('fixed'):
                    twice=src.parent/f'{src.stem}_{args.label}_{axis}_twice.xml'
                    m.build_mirror_package(dst,twice,axis);fail,error=compare(src,twice,'none')
                    report['cases'].append({'case':src.stem+'_double','axis':axis,'failures':fail,'max_error':error})
            except Exception as e:report['cases'].append({'case':src.stem,'axis':axis,'failures':[str(e)]})
    report['failed_cases']=sum(bool(c['failures']) for c in report['cases'])
    (HERE/(args.label+'_audit.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
