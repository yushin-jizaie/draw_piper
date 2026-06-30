---
name: piper_end_load_gravity_comp
description: 示教(マスターモード)で arm が落ちるのは末端負荷未設定で重力補償が効いてないため
metadata: 
  node_type: memory
  type: project
  originSessionId: 1851ba0b-f126-4c5b-bac2-857ebeb6c448
---

Piper のマスターモード (MasterSlaveConfig 0xFA) で arm が自重+EOAT で落ちる原因は
**末端負荷(end-load)が firmware に設定されておらず、 重力補償が「無負荷」前提で
弱い**ため。 AgileX の示教モードは本来 gravity-comp で保持するはずだが、
末端負荷が無いと EOAT 分を補償できず落ちる。

**Latent bug:** プロジェクト全体の Config Init
`ArmParamEnquiryAndConfig(0x01, 0x02, 0, 0, 0x02)` は set_end_load=0x02(満) を
送っているが、 4番目の `end_load_param_setting_effective` が 0x00 で**無効**
(有効にするには **0xAE** が必要)。 つまり末端負荷設定は一度も効いていない。

**Fix/使い方:** `ArmParamEnquiryAndConfig(0x00, 0x00, 0x00, 0xAE, level)` で
effective=0xAE と一緒に送ると効く (level: 0x00無/0x01半/0x02満)。
2026-06-01 に GUI のキャリブ section へ「重力補償(末端負荷) 半/満/無」ボタンを
追加 (commit d2a19c2)。 大きすぎると逆に浮くので半→満で実機調整。
注意: Config Init 本体は未変更 (描画時の重力補償を変えると [[frida_well_drawn_reference]]
の校正に影響しうるため)。 関連: [[piper_firmware_v18_id_offset]]

**★WEB 裏取り (2026-06-30): 重力補償つき示教 + feedback 読取は slave mode で両立できる (master mode 不要)。** AgileX 公式 piper_ros #26 で、まさにこのユースケース (示教=手で back-drive する間も end-pose/関節を読みたい) の正解が出ている: **「slave mode にして本体の示教ボタンを押すと、重力補償を有効にしたまま end-effector 位置を取得できた」** (投稿者 YugoNishio 自己解決)。→ うちの drag-teach は master mode (`MasterSlaveConfig 0xFA`) を使っているせいで feedback が 0x3A* にシフトし読めなくなっていたが、**slave mode + teach ボタンなら重力補償(=サグ防止の保持) と 0x2A* feedback が同時に効く**。これが効けば N6 のサグ汚染も「示教中に指令 vs 実測の乖離を 0x2A* で常時監視」して即検知できる。要実機検証。Source: github.com/agilexrobotics/piper_ros/issues/26。関連: [[piper_firmware_v18_id_offset]] の WEB 裏取り節。

**★最重要 (2026-06-02): この sag が「描画歪み」の真の元凶だった。** 以前は示教中
保持できていたのが効かなくなる**回帰**が起き、 示教(drag-teach/微調整)中に arm が
サグ → 四隅が下・内側にズレて記録 (上側=伸びた姿勢ほど顕著) → **キャリブが台形化**
→ 円/螺旋/生成画像すべて歪む、 という連鎖。 何セッションも「描画コード/座標変換/
TCP」を疑って空振りした。 **描画が歪んだら、まず示教時にアームがサグしてないか・
四隅が矩形かを疑う。** 復旧: 壊れた calibration/canvas_calibration.yaml を
committed 版に `git checkout` で戻す (履歴あり) → end-load で保持を回復 → 再キャリブ。
教訓: FK/IK モデルは正しい (deg/rad 取り違えで誤診注意)。 [[drawing_distortion_diagnostic]]
