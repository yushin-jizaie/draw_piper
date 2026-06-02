#!/usr/bin/env python3
"""ボードを上下に動かした時、 canvas_calibration の Z を一括シフト (data 調整)。

純粋な垂直移動を仮定 (X/Y 不変)。 四隅 + 中央Z を dz だけ動かす。
使い方: venv/bin/python scripts/shift_calib_z.py --dz -50 [--write]
  --dz: Z 変化量(mm)。 ボードを 5cm 下げた = -50。
"""
import sys, numpy as np, shutil, datetime
sys.path.insert(0,'/home/jizaiedev2026/piper_test'); sys.path.insert(0,'/home/jizaiedev2026/draw_piper')
from canvas_calibration_io import read, write_v3
CAL='/home/jizaiedev2026/draw_piper/calibration/canvas_calibration.yaml'
dz=float(sys.argv[sys.argv.index('--dz')+1]) if '--dz' in sys.argv else -50.0
parsed=read(CAL); raw=parsed['raw']; wbrec=raw['whiteboard_corners_mm']
print('dz=%.1fmm'%dz)
for k in('tl','tr','br','bl'):
    z0=wbrec[k]['end_pose_mm_deg'][2]
    print('  %s Z: %.1f -> %.1f'%(k,z0,z0+dz))
comp=raw.get('whiteboard_computed') or {}
cm=comp.get('center_mm')
if cm: print('  center_z: %.1f -> %.1f'%(cm[1],cm[1]+dz))
if '--write' in sys.argv:
    ts=datetime.datetime.now().strftime('%H%M%S'); shutil.copy(CAL,'/tmp/canvas_calibration_pre_shiftz_%s.yaml'%ts)
    for k in('tl','tr','br','bl'):
        ep=list(wbrec[k]['end_pose_mm_deg']); ep[2]=round(ep[2]+dz,4); wbrec[k]['end_pose_mm_deg']=ep
        py=wbrec[k].get('pen_yz_mm');
        if py: wbrec[k]['pen_yz_mm']=[py[0],round(py[1]+dz,4)]
    if cm: comp['center_mm']=[cm[0],round(cm[1]+dz,4)]
    pf=raw.get('plane_fit') or {}
    if pf.get('centroid_mm'): c=pf['centroid_mm']; pf['centroid_mm']=[c[0],c[1],round(c[2]+dz,4)]
    traces={kk:list((raw.get('traces') or {}).get(kk) or []) for kk in ('perimeter','diagonal_tl_br','diagonal_tr_bl','surface')}
    write_v3(CAL,corners=wbrec,traces=traces,computed=comp,plane_fit=pf,joint_map=raw.get('joint_map'))
    print('WROTE. backup=/tmp/canvas_calibration_pre_shiftz_%s.yaml'%ts)
