### GPU 使用ルール（並行作業時の競合回避）

このプロジェクトは VS Code 側（人間）が GPU を使う計測作業を
並行で回している可能性がある。Claude Code が GPU を使う処理
（torch ロード、推論、CUDA カーネル呼び出しなど）を実行する前に、
必ず以下を確認すること:

1. **`nvidia-smi` を実行** して `Processes:` セクションを確認
2. 自分以外のプロセス（特に `python3` 系）が GPU を占有している場合は、
   ユーザに「GPU 計測中ですか？終わるまで待ちますか？」と確認してから進める
3. `Memory-Usage` が 1GB 以上使用されていたら、誰かが使っている可能性が高い
4. 空いていることを確認したら、その旨をログに残してから GPU 作業を開始

#### 該当する作業と該当しない作業

| 作業 | GPU 使用 | チェック必要 |
|---|---|---|
| Step E (`run_draw_test.py`) | ❌ piper_sdk のみ | 不要 |
| `modules/robot.py` の単体テスト | ❌ | 不要 |
| `modules/vectorizer.py` (OpenCV) | ❌ | 不要 |
| `modules/vlm.py` の動作確認 | ✅ torch/transformers | **必要** |
| `modules/image_gen.py` の動作確認 | ✅ torch/diffusers | **必要** |
| VRAM 計測スクリプト全般 | ✅ | **必要** |

#### 計測の信頼性のため

人間側の計測中に Claude Code が GPU を触ると、計測値が汚れて
やり直しになる。VLM 単体計測（2026-05-22）でも 5.51GB のロード値を
取得しているが、これは GPU が完全に空の状態で取った値である。
共存計測の正確性を担保するため、**計測中は GPU を一切触らない**。

#### 計測終了の通知

人間側は計測が終わったら明示的に Claude Code に知らせる
（チャット経由で「計測終わった」など）。Claude Code 側からも
`nvidia-smi` で残留プロセスがないかを確認できる
（cleanup 後は allocated 0.01GB 程度まで戻るはず）。
