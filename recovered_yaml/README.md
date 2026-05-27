# Recovered YAML files (2026-05-28)

git fsck --unreachable で発見した dangling blobs を保存。
HEAD と異なる別バージョンが含まれているため、 user による選別が必要。

## ファイル一覧

| ファイル | 内容 |
|---|---|
| `panel_frame.dangling.yaml` | size_mm 96×181 の別キャリブ (HEAD は 107×197) |
| `canvas_calibration.dangling.yaml` | B5 surface trace 含む (2026-05-26 19:25 セッション、 2355 lines) |
| `canvas_calibration.dragteach_old.yaml` | 5 点のみの古い drag-teach 版 |
| `imagegen_config.v1.yaml` | matsumoto_taiyo_animagine preset + 日本語混じり long prompt template |
| `imagegen_config.v2.yaml` | sdxl_turbo (steps=1) + 同じ prompt template |
| `_unknown_json_552B.txt` | 過去の .claude/settings.local.json (permissions) |

git tag (`recovered-panel-frame`, `recovered-canvas-cal`) でも blob は GC 防止済。

## 復元する場合

```bash
cp recovered_yaml/panel_frame.dangling.yaml calibration/panel_frame.yaml
# 等
```

ただし HEAD のキャリブ値と内容が違うため、 上書き前に diff 確認推奨。
