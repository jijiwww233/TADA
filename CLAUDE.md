# 项目协作说明

我明明收藏过是一个本地优先的设计素材收藏、查找和归档网站。面向队友的启动说明见 README.md。

## 结构

- `frontend/` 是 React 和 Vite 网页，默认开发端口由启动命令设为 5199。
- `backend/app/` 是 FastAPI 服务、图像导入、索引和 SQLite 建表迁移逻辑，端口 8100。
- `backend/tools/` 是维护脚本。模型下载工具读根目录的 `.env`。示例安装脚本把公开示例准备到 Vite 的静态文件目录。
- `samples/` 只放确认可以公开的生成素材。
- `android-import/` 是可选的 Android 截图导入客户端源码。
- `data/app.db` 及 `data/images/` 首次启动自动建立。模型默认位于 `data/models/chinese-clip-base`。这些是本机内容。

## 从空目录启动

先安装 Python 3.12 和 Node 22.12 或更新的 22.x 版本。从项目根目录运行。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm.cmd --prefix frontend ci
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local-library.ps1
```

Mac（尚未实机验证）：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
.venv/bin/python backend/tools/prepare_samples.py
(cd backend && WOMINMIN_DESKTOP_LIBRARY=1 ../.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8100)
```

Mac 另开一个终端，从项目根目录运行：

```bash
VITE_PORT=5199 VITE_BACKEND_URL=http://127.0.0.1:8100 VITE_DEFAULT_DATA_MODE=real npm --prefix frontend run dev
```

`http://127.0.0.1:8100/api/health` 返回 `{"ok":true}`，`http://127.0.0.1:5199/main?data=live` 显示素材库，且全新目录创建了本地空库，说明启动成功。验证引导时访问 `http://127.0.0.1:5199/intro`。按 README 安装模型后验证找图。云端配置可选。

## 回滚和提交

- 改动前用 `git status --short` 确认工作树和他人正在编辑的文件。对干净的基线记录 `git rev-parse HEAD`，或建一个本地分支作回滚点。生产素材库相关改动前另做一致性 SQLite 备份，备份只放忽略目录。
- 提交前用 `git status`、`git ls-files` 和 `git diff --cached` 检查文件名与内容。只提交当前任务文件。首次整理后也要检查发布压缩包的实际清单。
- 数据库及其备份、私人图片和截图、生产模型和索引、`.env`、Key、配对码和配对链接、Cookie 和浏览器配置、同步下载、日志、回滚目录、构建产物都不要提交。`.env.example` 的赋值保持留空。测试使用独立临时数据目录。
- 遇到并行修改，保留他人的工作。直接发源码前用 `git archive` 从已经审核的提交打包，避免顺手把未跟踪文件装进去。
