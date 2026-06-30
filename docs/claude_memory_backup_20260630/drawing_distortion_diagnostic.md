---
name: drawing_distortion_diagnostic
description: 壁面描画が歪む時の切り分け順序 (1セッション無駄にしない為)
metadata: 
  node_type: memory
  type: project
  originSessionId: 1851ba0b-f126-4c5b-bac2-857ebeb6c448
---

壁面描画 (円/螺旋/生成画像) が歪む時の切り分け。 2026-06 に何セッションも
誤診したので順序を固定する。**上から順に潰す。**

1. **キャリブが汚染されてないか** (最有力)。 示教時にアームがサグると四隅が
   台形化 → 全描画が歪む。 [[piper_end_load_gravity_comp]] 参照。
   - 確認: 四隅が矩形か (対辺/対角が等しいか)。 committed 版と比較
     (`git log -- calibration/canvas_calibration.yaml`、 `git show HEAD:...`)。
   - 復旧: 壊れてたら committed 版に `git checkout`。 末端負荷で保持回復後に再teach。
2. **GUI 再起動したか**。 center_y/z・contact_x・xoff は **startup でのみ** yaml から
   読む (in-memory)。 再キャリブ/復元しても再起動か「キャリブ値 再読込」しないと
   古い値で描く。 起動ログ `Loaded defaults ... contact_x=.. center Y=.. Z=..` を確認。
3. **テスト図形 (MoveJ) は orthonormal frame + JointCtrl+IK** で、 bilinear/depth/
   keystone を通らない (= 純粋にキャリブ依存)。 **MoveC は EndPoseCtrl で本機 firmware
   では壊れてる**ので使わない。 ペン向きロック ON は pen-down 接触角に影響。
4. **FK/IK モデルは正しい。** ここを疑わない (fk() は度入力。 rad で渡すと誤診)。
   生成ストロークだけ歪むなら panel_frame.yaml の直交誤差/サイズ古い → 「panel更新」。

関連 commit (2026-06-01〜02): depth傾き補正(b4230be) は実機改善確認済み。
bilinear/keystone/pen-lock は補助 (キャリブが綺麗なら ほぼ不要)。
