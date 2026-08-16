---
name: framed_enrich_digital_line
description: framedルートの加筆+デジタル線設計 (VLM suggest_additions / object@CN0.35 / mistoline)
metadata: 
  node_type: memory
  type: project
  originSessionId: 66b6007c-50c0-4f19-8c75-54a94658fcb5
---

framed ルート (scripts/gen_routed.py) は「中程度の加筆 + デジタル均一線」方針 (2026-06-02 ユーザー要望: 生成が抽象的/筆っぽい/解説だけで加筆指示が無い、を解消)。

2 variant をガチャ選別用に出す:
- **enriched** = `illustrious_v2_object` を **CN 0.35** で。VLM `suggest_additions()` の加筆要素 (眼鏡/髭/服/小物/効果線) を prompt に注入。
- **clean** = `animagine_xl_31_mistoline` CN 0.5、加筆なしの忠実トレース (加筆が外したとき用)。

**Why / 非自明な点:**
- 加筆を絵に出すには ControlNet を **0.35 まで下げる必要**。preset 既定(0.65-0.9)や CN0.5 では忠実すぎて加筆が出ない。
- `mistoline` は **CN を 0.25 まで下げても加筆を無視**して入力をトレースするだけ → 加筆用には使えない。だから enriched は object preset。
- 線質は preset の style_hint を**付けない** (`illustrious_v2_object` の "no humans" が顔に混入、かつ CLIP 77 token 超過の原因)。`DIGITAL_LINE` 定数 1 本に集約。
- enriched は時に過剰 (例: sun → 雲の塊) なので clean 変種を必ず併走させて選別する。

**How to apply:** 加筆度=CN。もっと足したい→CN下げ(0.3)、控えめ→CN上げ(0.45)。掠れ/塗りは `KASURE_NEGATIVE` で抑制。VLM 側は `describe_literal`(主題)+`suggest_additions`(加筆指示) の2段。

関連: [[winning_genart_recipe_lineart_cn05]] (stylizeルートの勝ちレシピ cn0.5)。
