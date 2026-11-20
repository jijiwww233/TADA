# TADA 2.0 验收快照

- **冻结版本**：`v2.0` 标签 → `88586fb`（「统计排除我自己」上线后的提交）
- **前端构建提交**：`00fd1eb`（只加了 `/v2/` 子路径支持，界面与 `88586fb` 相同）
- **产物目录**：`.run/public-site-v2`（不进 git；`.run/` 已被忽略）
- **访问地址**：`/v2/main`、`/v2/intro`；`/api`、`/images` 与主站共用同一份数据

## 不要覆盖

`.run/public-site-v2` 是 2.0 的最终快照，用来做 2.0 和 3.0 的前后对比。

- 以后更新主站只替换 `.run/public-site`，**永远不要**替换或清理 `public-site-v2`。
- 不要用主站的构建命令直接输出到该目录；如需重建，必须从 `v2.0` 标签对应的代码、加 `--base=/v2/` 单独构建。
- 后端只读挂载它（`tools/public_demo.py` 的 `SITE_V2`），不会写入。

## 重建方法（仅在快照损坏时）

```powershell
cd .run\public-backend-4afa7d2\frontend
$env:VITE_PUBLIC_DEMO='1'; $env:VITE_DEFAULT_DATA_MODE='real'
node node_modules\vite\bin\vite.js build --config vite.public.config.ts --base=/v2/ `
  --outDir D:\CC-Work\收藏过-refine\.run\public-site-v2 --emptyOutDir
```

构建完成后必须确认 `index.html` 引用的是 `/v2/assets/...`。
