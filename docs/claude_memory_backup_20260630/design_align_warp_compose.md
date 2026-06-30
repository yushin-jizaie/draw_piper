---
name: design_align_warp_compose
description: デザイン性×アライン性を「2生成→特徴ワープ後合成」(方式③)で両立。アライン芯は入力生線が正解
metadata: 
  node_type: memory
  type: project
  originSessionId: e1718c7c-ca5d-4817-bc64-119d2b1aaea8
---

マーカー描画の生成で **デザイン性(VLM完成形・低CN)↔アライン性(入力位置への忠実さ・高CN)はトレードオフ**。
1枚で両立させず「役割を分けた2生成→ストローク座標で後合成」で解く方針 (2026-06-02 ユーザー発案、方式③)。

**実装** (`scripts/gen_flux_warp_compose.py`, 基盤 = [[flux_schnell_controlnet_setup]] + [[flux_lora_training_16gb]] + VLM `design_instruction(mode="complete")`):
1. design 生成 = 低CN(0.2) + 完成形ビジョン prompt → 魅力的だが入力からドリフト。
2. アライン芯 = **入力画像そのものの線** (`square_pad(input)` を vectorize)。
3. **DIS optical flow** (`cv2.DISOpticalFlow_create`) で design→入力 の変位場を計算
   (線は反転+GaussianBlur で疎な線にフロー支持を与える)、design ストロークを warp。
4. `_place_input_aligned` で 3層 (align_input/design/warp) を入力位置へ写像、webapp 比較。

**重要な失敗と修正**: 初版はアライン芯を「高CN(0.7)生成」にしたが、**簡素な手描き線画では
高CN FLUX 生成がほとんどストロークを生まず芯スカスカ (0-6本)** → flow 基準が空 → warp が効かない。
→ アライン源を **入力生線** に変更して解決 (常に濃く・完全アライン、生成も design 1本で GPU 半減)。
**教訓: アライン基準に生成画像を使うな。人が描いた入力線が唯一確実なアライン源**。

未確定: warp の**絵的品質はユーザー目視判定が次ステップ** (構造・座標整合は成立、寄せ量 flow 5-27px)。
崩れる場合の次手: flow を TPS/特徴点ベースへ / design の CN をさらに下げて寄せを増やす / ②アンカー合成
(入力生線 + design の bbox外装飾) へ後退。MILESTONES M18 (`88bc21f`)。
