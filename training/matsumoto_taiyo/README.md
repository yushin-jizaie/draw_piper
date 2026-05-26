# 松本大洋 LoRA 学習データ

`raw/` フォルダ (このディレクトリ直下、 `.gitignore` 済み) に **15-50 枚**
の松本大洋作品 (jpg/png/webp) を置く。

## 推奨

- 顔のクローズアップ 5-10 枚 (彼の特徴的な線が出る)
- 全身 / 動き 5-10 枚 (動的な線)
- ベタ塗りや細密描写は混ぜない (LoRA が混乱)
- 解像度はバラバラで OK (scripts 側で 1024 系にリサイズ)

## 著作権

- このフォルダの中身は `.gitignore` 済み (リポにコミットされない)
- 学習目的 (個人 / 研究) 以外で使わない
- 学習結果の LoRA も公開しない

## 次の手順

```bash
python3 -m scripts.prepare_style_dataset \
    --input  training/matsumoto_taiyo/raw \
    --output training/matsumoto_taiyo/dataset \
    --trigger mt_taiyo_style
```

詳細: `docs/20260526_2200_style_lora_training.md`
