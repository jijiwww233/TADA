# TADA! 图然想起

给设计师的素材收藏与找回工具。

平时刷到的参考图、截图，随手收进来；真要用的时候，不用记文件名、不用翻收藏夹，直接说出你记得的样子，比如「海边」「颜色很多的海报」「手写字的菜单」，就能把它找回来。

## 它能做什么

- **收**：电脑上多选或拖拽导入；iPhone / iPad 截图后用快捷指令一键存入；任何手机都可以把图发给微信测试号自动入库；在电脑版里还能连接小红书、花瓣，把已有收藏一次性搬过来。
- **找**：「找图」用描述画面或颜色的话找回收藏；「要参考」按排版、配色、风格给出参考。
- **用**：把找到的图收进项目，或者打开参考画板，和正在做的稿子并排对照。

## 在线体验版

我们部署了一个在线体验版，手机和电脑都能打开。网址和账号请向作者索取。

**这是一个大家共用的体验版**：所有体验者看到的是同一个素材库，你上传的图其他人也能看到，请只上传可以公开的图片。

## 体验版的限制，以及原因

下面这些不是没有想到，而是受平台规则、账号安全和课程阶段的资源所限，暂时只能这样实现：

- **截图后不能自动上传**：iOS 和安卓都不允许网页在后台得知你截了图，这是系统层面的限制。我们用「快捷指令」和「微信发图」做到了不装 App 也能最快收图。
- **没有做成手机 App**：上架 iOS App 需要付费开发者账号和苹果审核，课程周期内无法完成。
- **体验版不能连接小红书等平台**：平台同步需要用你本人的账号登录。为了账号安全，也为了不触发平台的风控，体验版不会代替你登录。平台同步请在自己电脑上运行 TADA 使用。
- **没有个人账号，大家共用一个库**：要给每个人单独的库，需要完整的用户系统和更大的服务器资源，目前还在课程原型阶段。
- **体验版偶尔会打不开**：体验版运行在作者的电脑上，经由服务器转发出去，作者电脑关机或断网时就会暂时无法访问。
- **每天有上传上限**：为了防止滥用、控制分析成本，体验版所有人每天合计最多上传 200 张。
- **图片的自动描述和标签需要千问 API Key**：没有 Key 时，仍然可以导入、浏览、按颜色找图和用本地模型找图。
- **Mac 版尚未实机验证**：目前在 Windows 上完整测试过。

## 关于仓库里的图片

仓库中的演示图片仅用于课程演示，版权归原作者所有，请勿转载或另作他用。

## 验收方式

- **本机版 · 平台连接与自动同步。** 已接入小红书和花瓣，小红书的收藏自动同步已用实际账号验证。首次连接会在运行后端的电脑上打开平台登录窗口，收藏随账号同步到本机素材库。登录会话与收藏都属于个人账号，而公网版是多人共用的演示库，因此平台登录和后台同步留在本机。验收时用本机现场演示。
- **公网版 · 手机随手收图。** 访客登录演示网址后，可以用 iPhone 或 iPad 的「收进 TADA」快捷指令收图，也可以从手机浏览器点「导入 +」一次选多张。公共演示库里的图片对其他测试者可见，请只传用于演示的素材，别上传私人截图。
- **自己跑一份。** `samples/acceptance/` 附带验收用的演示图片。Windows 上安装依赖后，按下方「公网演示版」在本机启动，演示库会自动导入这些图。新开本机版时，也可以从该目录选图手动导入。账号信息、登录会话和本机数据库由各自电脑保存。

在 Windows 试平台自动同步时，先关闭自己之前启动的本机后端，再从项目目录运行下面两行。打开本机素材库后，进入「设置 → 平台」，连接自己的测试账号并开启「自动同步已连接的平台」。

```powershell
$env:WOMINMIN_ALLOW_AUTO_SYNC = '1'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local-library.ps1
```

## Windows 第一次启动

第一次装 Python 和 Node 的话，请照下面四步做，每一步都别跳过。

### 第 1 步：安装 Python 3.12

1. 打开 <https://www.python.org/downloads/windows/>。
2. 找到「Python 3.12.x」，点「Windows installer (64-bit)」下载并运行。
3. 安装程序第一屏，**务必勾选最下面的「Add python.exe to PATH」**，再点「Install Now」。
4. 装完点 Close。先不要打开任何终端窗口。

### 第 2 步：安装 Node.js 22

1. 打开 <https://nodejs.org/en/download>。
2. 选「Windows Installer (.msi)」，版本选 **22.12 或更新的 22.x**（64-bit），下载后双击安装，一路点 Next。
3. 装完关闭安装窗口。

装好这两样后，把已经打开的 PowerShell / 终端窗口全部关掉再重新打开（或者重启电脑），系统才能找到新装的程序。

### 第 3 步：解压并启动

1. 把源码压缩包解压到一个文件夹，路径里有没有中文都可以。
2. 在能看到本文件（README.md）和 `start-local-library.ps1` 的文件夹里，按住 Shift 在空白处点右键，选「在终端中打开」（Windows 11）或「在此处打开 PowerShell 窗口」。
3. 运行：

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-local-library.ps1
   ```

   第一次运行会自动查找 Python 3.12 和 Node、创建 `.venv`、安装后端和前端依赖，需要几分钟，请保持网络畅通；以后再运行就只负责启动。脚本找不到 Python 或 Node 时，会用中文提示你去哪个网址下载、装哪个版本。

### 第 4 步：打开素材库

打开 [素材库](http://127.0.0.1:5199/main?data=live)。第一次会建立一个空素材库，点击「导入 +」添加自己的图。想先熟悉界面，打开[四步引导](http://127.0.0.1:5199/intro)或[示例素材库](http://127.0.0.1:5199/main?data=demo)。

要用本地自然语言找图或以图找图，按下方「下载本地找图模型」安装一次，再重启后端。

以后从源码目录重新运行第 3 步的命令即可。脚本会在后台启动网页和后端，同一个端口已经在运行时会沿用它。

### 手动安装（可选，排错时用）

如果启动脚本自动安装依赖失败，也可以在项目根目录手动执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm.cmd --prefix frontend ci
```

- `py` 是 Python 安装包自带的启动器；如果提示找不到 `py`，先关掉终端重新打开，或改用 `python`。
- `npm.cmd` 在 Node.js 的安装目录里；如果提示找不到 `npm.cmd`，先关掉终端重新打开，或改用 `npm`。
- 手动装完依赖后，仍然推荐运行 `start-local-library.ps1` 来启动，它会自己找到刚装好的 Python 和 Node。

## Mac 第一次启动（Mac 版尚未实机验证）

先装 Python 3.12 和 Node.js 22.12 或更新的 22.x 版本。用终端进入解压后的项目目录，执行：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
npm --prefix frontend ci
.venv/bin/python backend/tools/prepare_samples.py
```

第一个终端启动后端并保持打开：

```bash
cd backend
WOMINMIN_DESKTOP_LIBRARY=1 ../.venv/bin/python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8100
```

另开一个终端，回到项目根目录启动前端：

```bash
VITE_PORT=5199 VITE_BACKEND_URL=http://127.0.0.1:8100 VITE_DEFAULT_DATA_MODE=real npm --prefix frontend run dev
```

打开 [素材库](http://127.0.0.1:5199/main?data=live)。以后可以在两个终端按 Ctrl+C 停止服务。

## 下载本地找图模型

这是 OFA-Sys/chinese-clip-vit-base-patch16，权重约 753 MB。加上 Python 依赖会占用更多磁盘空间。模型默认放进项目的 `data/models/chinese-clip-base`，也能在根目录的 `.env` 里用 `CHINESE_CLIP_MODEL_DIR` 指定目录。自定义路径请用绝对路径。

Windows（从项目根目录运行）：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-clip.txt
Push-Location backend
..\.venv\Scripts\python.exe -m tools.download_model
Pop-Location
```

Mac（从项目根目录运行）：

```bash
.venv/bin/python -m pip install -r backend/requirements-clip.txt
(cd backend && ../.venv/bin/python -m tools.download_model)
```

官网下载失败时，可在复制出来的 `.env` 里填 `HF_ENDPOINT=https://hf-mirror.com`，然后重跑下载命令。脚本会逐个核对文件大小，并验证权重指纹。镜像可能受到网络环境影响。看到「模型校验通过」后，重启后端。刚导入的素材会在后台逐步建立索引，等一会儿再试找图。

## 可选的千问图片分析

没有 Key 时，仍可导入和浏览图片、整理项目、使用颜色筛选和查看详情。安装本地模型后可以用本地找图。自动生成描述和标签，以及需要云端向量的图文融合找图，需要配置云端服务。

申请你自己的阿里云百炼 API Key。把项目根目录的 `.env.example` 复制成 `.env`，然后按百炼控制台填写：

- `AI_BASE_URL` 填适用于你的区域的兼容接口地址，形如 `https://dashscope.aliyuncs.com/compatible-mode/v1`。
- `VISION_MODEL` 填控制台中已开通、支持图片输入的模型名称。
- `AI_API_KEY` 可直接填进 `.env`，也可以暂留空，重启后端后在真实素材库的「设置」里粘贴并保存。网页验证 Key 前需要先填好接口地址。保存后再重启后端。

云端分析会把图片发送给配置的服务商并可能产生费用。第一次可以先用一两张公开样图测试。

## 常见问题

- **素材库是空的。** 首次启动从空库开始。用「导入 +」添加图片，或用示例链接浏览。
- **网页打不开或导入失败。** 打开 [后端检查页](http://127.0.0.1:8100/api/health)，正常会看到 `{"ok":true}`。Windows 屏幕上没有后端窗口时，可查看项目目录的 `.run/backend.err.log` 和 `.run/frontend.err.log`。如果旧项目占着端口，请退出你先前启动的旧服务再重试。
- **改了配置还没生效。** 后端读取配置的时间是启动时。Windows 如果脚本沿用了旧进程，请先在任务管理器停止自己启动的这个项目的后端，再运行启动脚本。Mac 在后端终端按 Ctrl+C 后重新启动。
- **能打开素材库，但找图效果不完整。** 检查模型下载与安装步骤，重启后端，并给新图留出建索引的时间。没有云端配置时使用本地路径。
- **源码怎么发给别人。** 分享干净的源码压缩包。数据库、收藏图片、配对链接和 Key 只留在自己的电脑上。

## 公网演示版（Windows）

先按前面的 Windows 步骤安装依赖和本地找图模型。维护者在本机 `.env` 配置千问和独立的 `SHORTCUT_UPLOAD_TOKEN`。上传码只私下交给有权上传的人。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start-public-demo.ps1
```

打开 [演示素材库](http://127.0.0.1:18080) 或 [引导页](http://127.0.0.1:18080/intro)。公网转发使用现有的 SSH 隧道和网站登录。演示版只监听本机 18080，使用独立的 `.run/public-demo`。启动时把 `samples/` 里的图片导入演示库，并开启分析队列。队友的图准备好后放进 `samples/`，停止旧的 18080 演示进程再重新启动，已有素材会跳过。确认本机图库里每张图都可以公开时，可以在启动命令末尾加 `-IncludeLocalLibrary`，把图片另存进演示库。

登录演示网址后点「导入 +」，可从电脑或手机相册添加图片。演示版可以新建项目、将图片加入项目，在项目详情里用「前移」「后移」「从项目移除」管理图片。删除整个项目、清库、改 Key、平台同步、监控文件夹和设备配对在演示版停用。

要在导入面板显示「添加快捷指令」，把「收进 TADA」的 iCloud 分享链接填入本机 `.env` 的 `SHORTCUT_INSTALL_URL`。微信测试号二维码保存为 `.run/public-demo/wechat-qr.png`。两项在登录演示网站后可见，请勿放进 Git 或源码压缩包。

苹果快捷指令在「获取 URL 内容」中向 `/api/shortcut/upload` 发 POST。单张图可先转成 JPEG，请求体选「文件」，选中转换后的图片，添加 `Content-Type: image/jpeg` 和 `Authorization: Bearer <上传码>`。也可以用 `multipart/form-data` 的 `files` 字段发送 1 至 8 张。单张最多 20 MB。网页、快捷指令和微信合计每天最多新增 200 张，重复图片不占额度。接口返回中文纯文本，可直接放进快捷指令通知。

从项目根目录的 PowerShell 测试，输入上传码时不会回显：

```powershell
$token = Read-Host '输入上传码' -AsSecureString
$credential = [pscredential]::new('shortcut', $token)
"Authorization: Bearer $($credential.GetNetworkCredential().Password)" | curl.exe -sS -H '@-' -F 'files=@samples/show/sample-01.jpg' 'http://127.0.0.1:18080/api/shortcut/upload'
```

示例图已导入时预期返回「这张已经在库里了」。换成一张演示库里还没有的公开图片，预期返回「已收藏 1 张」，刷新网页即可看到。

### 微信导入怎么用

1. 在本机项目根目录的 `.env` 设置 `WECHAT_TOKEN`。当前流程只需要 Token。修改配置后重启演示后端。
2. 公网回调验收通过后，在微信测试号的「接口配置信息」填 URL，格式为 `https://<你的演示域名>/api/wechat`。Token 填本机 `.env` 中的 `WECHAT_TOKEN`。如果页面提供消息加解密方式，选「明文模式」，再提交。
3. 扫测试号二维码关注，在微信会话中直接发一张图片。收到「收到啦，正在收进素材库」后稍等片刻，刷新演示库即可查看。每张最多 20 MB，来源须为微信图片 CDN。微信、网页和快捷指令合计每天最多新增 200 张，重复图片不占额度。后台处理的失败会记录在演示后端日志里。

从项目根目录运行下面的本机测试。测试会通过本机 HTTP 服务提供内置示例图，发送带签名的 XML，再在临时演示库中检查入库、去重和共享额度。无需启动 18080。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_wechat_import.py -q
```
