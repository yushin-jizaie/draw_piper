# 位置関係検証 (2026-05-28 11:10)

ユーザ指摘: 「入力画像との位置関係もちゃんとできてるのかな?」

## 結論

**現状の object mode (M16, v5 settings) は位置を保持していない**。
入力 sketch を 5 位置 (中央/左上/右上/左下/右下) に置いて検証 →
出力は全て 中央 に full size cat。 ControlNet が soft hint (scale=0.65)
で位置情報を弱くしか伝えていない。

## 検証ファイル

### 5 位置の input vs v5 output

- `input_{center,tl,tr,bl,br}.png` (5 入力)
- `5position_with_v5_setting.png` 全 5 位置の結果を grid に

→ 全部中央に同じような猫が生成される。

### CN/strength sweep (TL 入力で fix 試行)

- `cn_str_sweep_grid.png` 比較 grid
- `sweep_A_t2i_cn065.png` 現状 v5 設定 (中央固定)
- `sweep_B_t2i_cn100.png` CN 上げただけ (中央のまま)
- `sweep_C_t2i_cn130.png` CN=1.3 (中央 + 小猫 upper-left に副次的に出現)
- `sweep_D_i2i_str085_cn100.png` **位置 OK!** ただし small cat (input size 反映)
- `sweep_E_i2i_str090_cn130.png` **位置 OK!** small cats 2 体
- `sweep_F_i2i_str095_cn070.png` 位置乱れ (CN 弱すぎ)

## 構造的 trade-off

| 設定 | 位置追従 | ディテール / size |
|---|---|---|
| t2i + CN low (現状 v5) | ✗ 中央固定 | ✓ Full size, 高ディテール |
| t2i + CN high (1.3+) | △ 副次的に input 位置に小描画 | △ メインは中央 |
| i2i str high + CN high | ◎ **位置完全追従** | △ input サイズ依存 (small sketch → small output) |

## 解決提案

3 つの方向性:

### 提案 1: object mode に 2 sub-mode 追加

| Sub-mode | Stage 1 | 用途 |
|---|---|---|
| `object_detail` (現状 v5) | text2img + CN 0.65 | 詳細重視 (位置は中央) |
| `object_position` | img2img str 0.90 + CN 1.0 | 位置重視 (size は input 依存) |

UX: ユーザが「位置を指定したい」 か「detail 重視」 か選ぶ。

### 提案 2: 入力 sketch を自動拡大

ユーザが小さい sketch を描いても、 pipeline で:
- bounding box 検出
- canvas の 80% 程度に拡大 + 配置維持
- pipeline 入力

これで「位置」 = canvas 全体の 80% を埋めるサイズの sketch、 出力も full size に。
ただし「中心からのオフセット」 は失われる (拡大時に中央寄せされる)。

### 提案 3: position-aware prompt 自動生成

VLM で input sketch の位置を解析 → prompt に「upper left corner」 等の位置語を
入れる → SDXL は prompt + CN の両方で位置を取る。

実装工数:
- 提案 1: 30 分 (preset 追加だけ)
- 提案 2: 1-2 時間 (sketch 拡大 logic + テスト)
- 提案 3: 2-3 時間 (VLM 統合 + prompt 拡張)

## 推奨

**提案 1 + 提案 2 の組み合わせ** が筋良い:
- detail 重視 user: object_detail mode を選ぶ (自動拡大 OFF)
- 位置重視 user: object_position mode を選ぶ (位置自動保持)
- どちらも欲しい: sketch を 大きめに描いてもらう ガイドライン
