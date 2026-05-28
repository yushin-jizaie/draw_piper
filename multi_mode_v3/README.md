# Multi-mode + Gacha UX 検証 (2026-05-28 09:30)

ユーザフィードバック反映:
- B/F のみ良かった → seed ランダム化 (gacha) で pose 多様化
- 人間以外も描く → 複数 mode で対応

## 実装

1. `scripts/test_ip_adapter_two_stage.py` に:
   - `--category {character|urban|other}` 追加
   - 各カテゴリに style ref pool (raw/ から curate)
   - `--style-ref` 未指定なら pool から seed 由来でランダム選択
2. `scripts/generate_gacha.py` 新規:
   - 1 sketch + category → N 個生成 + grid 合成
   - seed をランダム化 (master_seed から派生)
3. `scripts/gen_test_sketches_objects.py` 新規:
   - 非人間 sketch (家・木・猫・車) を programmatic 生成

## 検証結果

### ✅ character mode + gacha (B sketch x 5 variants)

result_character_smiley_5gacha.png 参照。 同じ smiley face sketch から
5 種類の異なる pose の Matsumoto キャラ。 「同じ pose しか出ない」 問題 解消。

### ⚠️ urban mode (家/猫 sketch)

result_house_urban.png / result_cat_urban.png:
- House → ほぼ空白 (4 strokes)
- Cat → ほぼ空白 (5 strokes)

style ref pool に 街並 (IMG_4321 等) を入れたが、 これらも IP-Adapter は
character composition を学んでしまい、 非 character 入力では bridge できず
出力が消失。 **urban mode は使い物にならない**。

### ✅ other mode (木・車)

result_tree_other.png: 67 strokes、 美しい樹木の線画 3 variants
result_car_other.png: 65 strokes、 車のディテール (タイヤ・窓・グリル)

IP-Adapter off + Plan E のみで clean lineart が生成される。 prompt と
input sketch の組み合わせが key。

### ✗ other mode (家・猫の一部)

result_house_other.png: 4-7 strokes、 house の幾何形は再現されず断片
result_cat_other.png: 部分的成功 (36, 24 strokes) だが頭の輪郭中心

仮説: inpaint mode が「sketch の細かい線を全部保持 + 残り少しを repaint」
の動作で、 複雑 sketch (house の四角+三角+窓) では大半が「保持」 領域に
なり 自由度がなく うまく描けない。 **img2img mode への切替** が必要。

## 推奨方針 (改訂版)

| Mode | IP-Adapter | Base mode | 用途 |
|---|---|---|---|
| `character` | ON (松本キャラ ref) | inpaint | 人間の顔/キャラ sketch |
| `object` (新規) | OFF | **img2img** | 物体・動物・植物 (sketch を全体的に stylize) |
| `freeform` (新規) | OFF | text2img | 言葉だけで生成 (sketch 影響薄) |

`urban` mode は廃止、 `object` mode に統合。

## 即次の手

1. modules/image_gen.py に `illustrious_v2_object` preset (img2img mode) 追加
2. `--category object` で それを使うように two_stage script 修正
3. house/cat で再検証

これらは 30 分程度の作業で済む。 ユーザ判断待ち。
