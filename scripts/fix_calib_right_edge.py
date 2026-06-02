#!/usr/bin/env python3
"""右辺(tr-br)を正として canvas_calibration の四隅を矩形に正則化。

left 辺が短い等の歪みを 右辺基準で修正。 --write でバックアップ後に書込。
書込後は GUI の「panel_frame.yaml を更新」(or canvas_to_panel_frame_dev) も実行。
使い方: venv/bin/python scripts/fix_calib_right_edge.py [--write]
"""
import sys, numpy as np, shutil, datetime
sys.path.insert(0,'.'); sys.path.insert(0,'/home/jizaiedev2026/draw_piper')
from canvas_calibration_io import read, write_v3
CAL='/home/jizaiedev2026/draw_piper/calibration/canvas_calibration.yaml'
parsed=read(CAL); raw=parsed['raw']; wbrec=raw['whiteboard_corners_mm']
P={k:np.array(wbrec[k]['end_pose_mm_deg'][:3],float) for k in('tl','tr','br','bl')}
def edges(Q):
    d=lambda a,b:np.linalg.norm(Q[a]-Q[b])
    return dict(top=d('tl','tr'),bot=d('bl','br'),left=d('tl','bl'),right=d('tr','br'),
               d1=d('tl','br'),d2=d('tr','bl'))
print('BEFORE:',{k:round(v,1) for k,v in edges(P).items()})
# 右辺 tr,br を固定。 v=上向き(br->tr), n=平面法線, u=水平(左右)
v=(P['tr']-P['br']); v/=np.linalg.norm(v)           # up
n=np.cross(P['tr']-P['br'], P['bl']-P['br']); n/=np.linalg.norm(n)
u=np.cross(n,v); u/=np.linalg.norm(u)               # horizontal
# u の符号: tl は tr から左へ。 (P['tl']-P['tr'])·u が正になる向きに揃える
if np.dot(P['tl']-P['tr'],u)<0: u=-u
# 幅 W = 上下辺の u 方向射影の平均 (測定幅を尊重)
w_top=abs(np.dot(P['tl']-P['tr'],u)); w_bot=abs(np.dot(P['bl']-P['br'],u))
W=(w_top+w_bot)/2.0
print('  右辺長(height)=%.1f  width(top=%.1f bot=%.1f -> 平均 %.1f)'%(np.linalg.norm(P['tr']-P['br']),w_top,w_bot,W))
# 新 tl,bl = 右隅から左へ W (右辺と平行・同長の左辺になる)
tl_new=P['tr']+W*u; bl_new=P['br']+W*u
Pn=dict(P); Pn['tl']=tl_new; Pn['bl']=bl_new
print('AFTER :',{k:round(v,1) for k,v in edges(Pn).items()})
print('  new tl=%s  bl=%s'%([round(x,1) for x in tl_new],[round(x,1) for x in bl_new]))
print('  (旧 tl=%s bl=%s)'%([round(x,1) for x in P['tl']],[round(x,1) for x in P['bl']]))
if '--write' in sys.argv:
    ts=datetime.datetime.now().strftime('%H%M%S')
    shutil.copy(CAL,'/tmp/canvas_calibration_pre_rightedge_%s.yaml'%ts)
    for k,newp in (('tl',tl_new),('bl',bl_new)):
        ep=list(wbrec[k]['end_pose_mm_deg'])
        ep[0],ep[1],ep[2]=round(float(newp[0]),4),round(float(newp[1]),4),round(float(newp[2]),4)
        wbrec[k]['end_pose_mm_deg']=ep
        wbrec[k]['pen_yz_mm']=[round(float(newp[1]),4),round(float(newp[2]),4)]
    traces={kk:list((raw.get('traces') or {}).get(kk) or []) for kk in ('perimeter','diagonal_tl_br','diagonal_tr_bl','surface')}
    write_v3(CAL,corners=wbrec,traces=traces,computed=raw.get('whiteboard_computed'),plane_fit=raw.get('plane_fit'),joint_map=raw.get('joint_map'))
    print('WROTE. backup=/tmp/canvas_calibration_pre_rightedge_%s.yaml'%ts)
