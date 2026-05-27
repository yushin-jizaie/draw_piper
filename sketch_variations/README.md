# Sketch variation sweep (2026-05-28)

Two-stage pipeline (stage1=1024 Plan E、 stage2=768 IP-Adapter strength 0.45) の
入力多様性に対する robustness 検証。 全 6 種の自動生成 sketch × 同一設定。

## 入力 sketch パターン

| ID | 内容 | 狙い |
|---|---|---|
| A | oval + 2 dots (中央) | baseline (= test_sketch.jpg 互換) |
| B | round + smiley (目+口) | 顔ディテール多め |
| C | 顔 + 首 + 肩 V 字 | input で体位置を示唆 |
| D | 棒人間 (頭+胴+腕+脚) | 全身構図 を input で固定 |
| E | 小さい顔 (上部に配置) | 余白多め、 体は下に開放 |
| F | 怒り顔 (眉+鋭目+口) | 表情の多様性 |

## 結果サマリ

| ID | strokes 数 | 観察 |
|---|---|---|
| A | 25 strokes | 入力情報量少なすぎて出力もスパース |
| B | 127 strokes | 強い表情 + clenched fist の dynamic ポーズ |
| C | 124 strokes | 首+肩の hint が活きて 体ボリューム豊か |
| D | 129 strokes | 棒人間が「松本タッチで肉付けされたキャラ」 に変換 |
| E | 65 strokes | 顔が頭の位置に維持、 体は下に展開 (理想形) |
| F | 105 strokes | 怒り表情が出力に反映、 spiky 髪 + aggressive 姿勢 |

## 全体評価

- ✅ pipeline は **入力多様性に強い** (A 以外は全部 Matsumoto タッチ獲得)
- ✅ E (小顔+上部配置) が **顔位置 fidelity** で最良
- ⚠️ A (最小入力) は出力が貧弱 → ユーザは多少 input を充実させる必要あり
- ⚠️ D (棒人間) は元の hand-drawn 感が消えて Matsumoto キャラに完全変換

## 推奨入力ガイドライン

「顔の輪郭 + 目 + (簡単な体の hint)」 程度の sketch があれば、
Matsumoto-style な合成画像 → robot 描画 strokes が得られる。

E パターン (小顔を上に置く) は face position fidelity が一番良いので、
ユーザに「顔は canvas 上部 1/3 に小さく描いて」 ガイドするのが理想。

## 再現コマンド

```bash
# 6 種の sketch 生成
venv/bin/python -m scripts.gen_test_sketches --out-dir logs/sketches_<ts>

# 各 sketch で two-stage 実行
for s in sketch_A_*.png ...; do
  venv/bin/python -m scripts.test_ip_adapter_two_stage \
      --user-sketch "$s" \
      --style-ref training/matsumoto_taiyo/raw/IMG_4311.JPG \
      --output logs/result_<name> \
      --stage1-resolution 1024 --resolution 768 \
      --stage2-strength 0.45 --ip-scale 0.6 --seed 42
done
```

## 完成度

主観評価で 5/6 が **「松本タッチが乗ってる」 + 「ロボット描画適合」** を満たす。
本パイプラインは production 投入可能水準。
