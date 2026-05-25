# Canvas calibration v3 — Step 1: yaml schema v3 リーダー/ライター実装

> 日時: 2026-05-25 14:57 (JST)
> 関連既存ファイル: `20260525_1447_canvas_calibration_v3_design.md` (設計ドキュメント),
>                    `20260523_2100_wall_drawing_gui_consolidation.md` (M11),
>                    `20260523_1820_master_mode_drag_teach_calibration.md` (M10)
> ステータス: ✅ **モジュール完成・全テスト PASS、GUI 未配線**(Step 2 で配線予定)

## 実施したこと

設計ドキュメントの「実装順序の推奨」step 1 として、`~/piper_test/` に
2 ファイル新規作成:

- `canvas_calibration_io.py` (約 280 行) — v1/v2/v3 リーダー + v3 ライター
- `test_canvas_calibration_io.py` (約 320 行) — 単体テスト 38 チェック

### モジュール API (`canvas_calibration_io.py`)

```python
detect_version(data) -> int          # 1 / 2 / 3
read(path)           -> dict          # 正規化された dict を返す
write_v3(path, *, corners, traces, computed,
         plane_fit=None, joint_map=None,
         created_at=None) -> None
make_point(pen_yz_mm, joints_deg,
           end_pose_mm_deg, timestamp=None) -> dict
corner_xyz(record) -> (x, y, z)
```

`read()` が返す正規化 dict のキー(v1/v2/v3 共通):

| キー | 内容 |
|---|---|
| `schema_version` | 1, 2, or 3 (検出済) |
| `source_path` | yaml のフルパス |
| `created_at` | v3: ISO timestamp / v1,v2: `canvas.measured` 文字列 / 無ければ None |
| `whiteboard_corners_mm` | dict {tl/tr/br/bl: [x, y, z]} or None (v1 は None) |
| `whiteboard_corners_joints_deg` | dict {tl/tr/br/bl: [j1..j6]} or None |
| `whiteboard_corners_records` | **v3 専用**: フル sample record (pen_yz_mm + joints + end_pose + ts) |
| `traces` | dict {perimeter / diagonal_tl_br / diagonal_tr_bl / surface: [...]} (v1/v2 は全て `[]`) |
| `whiteboard_computed` | v3/v2 の生 dict / v1 は None |
| `plane_fit` | dict (normal, centroid_mm, rms_residual_mm, …) or None |
| `joint_map` | v3 専用 / v1/v2 は None |
| `center_xyz_mm` | (x, y, z) ベストエフォート最終中心 |
| `center_y_mm`, `center_z_mm`, `contact_x_mm` | 旧 GUI 互換のフラット accessor |
| `raw` | パース直後の yaml dict (fallback アクセス用) |

### 設計ドキュメントとの意図的な差分

設計ドキュメント上、v3 の `whiteboard_corners_mm` は:

```yaml
whiteboard_corners_mm:
  tl: {y: ..., z: ...}
  tr: {y: ..., z: ...}
  ...
```

と「y/z のみの dict」として書かれていたが、これは plane fit の入力で
**各点の X が必要**(設計ドキュメント自身が「各点の X は
`end_pose_mm_deg[0]` から取る」と記述)という矛盾があった。本実装では
corner を trace point と同じ構造で記録:

```yaml
whiteboard_corners_mm:
  tl:
    pen_yz_mm: [y, z]
    joints_deg: [j1, j2, j3, j4, j5, j6]
    end_pose_mm_deg: [x, y, z, rx, ry, rz]
    timestamp: "2026-05-25T14:48:12.345"
  tr:
    ...
```

この拡張により:

1. **X 情報の保持** — 平面 fit 入力に corner も同じ点群形式で投入可能
2. **trace point と統一構造** — `make_point()` で corner と trace point を
   同じ関数で生成できる (コード重複削減)
3. **後方互換性は保持** — v2 yaml (`whiteboard_corners_mm.{tl,tr,br,bl}: [x, y, z]`)
   も `read()` が正規化して xyz を返す。v3 の生 record はそれとは別に
   `whiteboard_corners_records` で公開

設計ドキュメント側はこの拡張を反映して corner schema を後で書き換える
余地あり(本実装が先行)。

### 後方互換のスキーマ検出

`detect_version()` で以下のように判定:

| 条件 | バージョン |
|---|---|
| `schema_version: 3` がトップにある | 3 |
| `canvas.whiteboard_corners_mm` がある | 2 |
| `canvas.points_mm` がある | 1 |
| いずれも該当なし | ValueError |

### テスト結果

```
$ /home/jizaiedev2026/draw_piper/venv/bin/python \
    /home/jizaiedev2026/piper_test/test_canvas_calibration_io.py

--- make_point_validation ---          (4 checks PASS)
--- detect_version ---                 (6 checks PASS)
--- v3_round_trip ---                  (17 checks PASS)
--- v3_partial_fields ---              (4 checks PASS)
--- v3_write_validation ---            (5 checks PASS)
--- v2_read_normalization ---          (7 checks PASS)
--- v1_read_normalization_real_file ---(6 checks PASS — disk 上の M10 yaml に対して)
--- corner_xyz_accessor ---            (1 check PASS)
============================================================
ALL CHECKS PASSED (50 checks)
```

うち 6 checks は **disk 上の本物の v1 yaml** に対する回帰テスト
(`/home/jizaiedev2026/draw_piper/calibration/canvas_calibration.yaml` =
M10 で記録された 31 点 plane_fit 形式)。 v1 検出 + corners None +
contact_x derivable + center derivable + traces all-empty + joint_map None
を確認。

### 受け入れ条件 (Step 1 完了基準)

- [x] v1 yaml(M10 既存ファイル)が読める → 正規化 dict が返る
- [x] v2 yaml(M11 GUI が書く形式)が読める → 正規化 dict が返る
- [x] v3 yaml の round-trip(書く → 読む → フィールド一致)
- [x] 既存 GUI の `_load_calib_defaults()` が必要とする情報
      (`center_y_mm`, `center_z_mm`, `contact_x_mm`) が v1/v2/v3 全てで取れる
- [x] エラー処理: 不正な schema_version、欠落 corner、center_mm 長さ不正、
      trace に list 以外、で `ValueError`
- [x] 単体テストが全通過

## 影響範囲(現時点)

- `~/piper_test/canvas_calibration_io.py` — 新規(280 行)
- `~/piper_test/test_canvas_calibration_io.py` — 新規(320 行)
- **既存ファイル無変更**(GUI 配線は Step 2)
- yaml ファイル(disk 上)は読むだけで変更なし

リグレッションリスク: なし(既存呼び出し元なし)。

## 次にやること (Step 2)

GUI [wall_drawing_gui.py:1185-1278](../../piper_test/wall_drawing_gui.py#L1185-L1278) の
`_fit_and_save()` を `canvas_calibration_io.write_v3()` に切り替える。
具体的には:

- B1 で記録した 4 corners を `make_point()` で v3 record 化
- B2 の `plane_extras_mm` は v3 では `traces.perimeter` に詰め直す
  (Step 4 で B2 が外周 drag-teach になるので、その時点で `traces.perimeter`
  に正しく入る。Step 2 では暫定的に従来 B2 を `traces.perimeter` 扱い
  にするか、空のままにするか要判断)
- `whiteboard_computed.center_mm` を 2 要素 `[y, z]` に変換
  (v2 は 3 要素 `[x, y, z]` だったので注意)
- 平面 fit は引き続き `_fit_and_save()` 内で計算(Step 7 で外出し)
- GUI からの load は `_load_calib_defaults()` を `read()` に置換

加えて GUI の `_load_calib_defaults()` も新モジュール経由に書き換え、
v1 (現状 disk 上の yaml) を読んだまま速度・center 反映できることを確認
(回帰防止)。

### 確認ポイント

Step 2 は **GUI コード改修のみ・実機接続なし** で完結する。動作確認は
GUI 起動 → load 反映 → 偽の v3 yaml を書く dry-run までで OK
(実際の drag-teach 実行は Step 4 まで不要)。

## Step 1 で学んだこと

- 設計ドキュメントの schema 表記が部分的に簡略化されていた(corner が
  y/z のみ表記)が、実用上は full record が必要だと実装時に判明。
  早期に IO モジュールを書くと設計矛盾を炙り出せて手戻り少。
- `read()` を返値 dict 1 つに統一(dataclass 不採用)したのは、PyYAML が
  dataclass を直接シリアライズしないため。フラットアクセッサを増やして
  既存 GUI 呼び出し側の書き換えを最小化。
- disk 上の M10 yaml を **テスト fixture として再利用**することで、合成
  データだけでは見逃しがちな実 yaml フォーマットの揺れ(`center_yz_mm`
  が 2 要素のフラットリストなど)を回帰テスト化できた。
