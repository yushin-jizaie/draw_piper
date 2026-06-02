#!/usr/bin/env python3
"""指定した縦辺 (left=tl-bl / right=tr-br) を真値として四隅を矩形に正則化。

反対側の2隅を作り直し、 対辺/対角が等しい矩形にする。 平面法線は元4隅の
best-fit を使用。 幅Wは上下辺の水平射影の平均。 --write でバックアップ後に
canvas_calibration.yaml 更新 (後段で panel_frame 再生成が必要)。
使い方: venv/bin/python scripts/fix_calib_edge.py --edge left|right [--write]
"""
import sys, numpy as np, shutil, datetime
sys.path.insert(0,'/home/jizaiedev2026/piper_test'); sys.path.insert(0,'/home/jizaiedev2026/draw_piper')
from canvas_calibration_io import read, write_v3
from wall_facing_ik import fk, solve_ik
CAL='/home/jizaiedev2026/draw_piper/calibration/canvas_calibration.yaml'
edge='left' if '--edge' not in sys.argv else sys.argv[sys.argv.index('--edge')+1]
parsed=read(CAL); raw=parsed['raw']; wbrec=raw['whiteboard_corners_mm']
P={k:np.array(wbrec[k]['end_pose_mm_deg'][:3],float) for k in('tl','tr','br','bl')}
def edges(Q):
    d=lambda a,b:np.linalg.norm(Q[a]-Q[b])
    return dict(top=round(d('tl','tr'),1),bot=round(d('bl','br'),1),left=round(d('tl','bl'),1),right=round(d('tr','br'),1),d1=round(d('tl','br'),1),d2=round(d('tr','bl'),1))
# 平面法線 (元4隅 best-fit)
C0=sum(P.values())/4; M=np.array([P[k]-C0 for k in('tl','tr','br','bl')])
_,_,Vt=np.linalg.svd(M); n=Vt[2]; n/=np.linalg.norm(n)
if edge=='right':
    a_top,a_bot='tr','br'; keep=('tr','br'); rebuild=('tl','bl')
else:
    a_top,a_bot='tl','bl'; keep=('tl','bl'); rebuild=('tr','br')
v=(P[a_top]-P[a_bot]); v/=np.linalg.norm(v)      # up
u=np.cross(n,v); u/=np.linalg.norm(u)            # horizontal
# u 符号: keep が left なら u は右向き(rebuild 側へ)、 keep が right なら左向き
other_top = 'tr' if edge=='left' else 'tl'
if np.dot(P[other_top]-P[a_top],u)<0: u=-u
w_top=abs(np.dot(P['tr']-P['tl'],u)); w_bot=abs(np.dot(P['br']-P['bl'],u)); W=(w_top+w_bot)/2
Pn=dict(P); Pn[rebuild[0]]=P[a_top]+W*u; Pn[rebuild[1]]=P[a_bot]+W*u
print('edge=%s 正  width=%.1f height(%s辺)=%.1f'%(edge,W,edge,np.linalg.norm(P[a_top]-P[a_bot])))
print('BEFORE:',edges(P)); print('AFTER :',edges(Pn))
for k in rebuild: print('  new %s=%s (旧 %s)'%(k,[round(x,1) for x in Pn[k]],[round(x,1) for x in P[k]]))
# 到達性チェック (限界域でないか)
seed=np.array([0.0,54.9,-27.3,0.0,-22.6,0.0]); _,Rs=fk(seed); dz=Rs[:,2]
print('到達性 (IK pos_err):')
for k in('tl','tr','br','bl'):
    best=99
    for s in [seed,np.array([10,55,-37,3,-38,2.5]),np.array([-9,57,-36,4,-44,2.5]),np.array([0,70,-15,0,-60,2.5])]:
        q,pe,oe=solve_ik(Pn[k],dz,s,iters=500); best=min(best,pe)
    print('  %s err=%.1fmm %s'%(k,best,'<-- 限界域?' if best>8 else 'OK'))
if '--write' in sys.argv:
    ts=datetime.datetime.now().strftime('%H%M%S'); shutil.copy(CAL,'/tmp/canvas_calibration_pre_%s_%s.yaml'%(edge,ts))
    for k in rebuild:
        ep=list(wbrec[k]['end_pose_mm_deg']); ep[0],ep[1],ep[2]=[round(float(Pn[k][i]),4) for i in range(3)]
        wbrec[k]['end_pose_mm_deg']=ep; wbrec[k]['pen_yz_mm']=[round(float(Pn[k][1]),4),round(float(Pn[k][2]),4)]
    traces={kk:list((raw.get('traces') or {}).get(kk) or []) for kk in ('perimeter','diagonal_tl_br','diagonal_tr_bl','surface')}
    write_v3(CAL,corners=wbrec,traces=traces,computed=raw.get('whiteboard_computed'),plane_fit=raw.get('plane_fit'),joint_map=raw.get('joint_map'))
    print('WROTE. backup=/tmp/canvas_calibration_pre_%s_%s.yaml'%(edge,ts))
