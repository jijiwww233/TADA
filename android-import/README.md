# 截图收藏 Android 版

`dev.mousehole.collection` 是独立应用。使用素材库「导入 + → 生成手机配对链接」，在手机 App 粘贴链接并保存。可从相册选图，也可在截图预览或相册的系统分享列表中选「截图收藏」。预览后点「传到素材库」，网页刷新即可看到。

想在截图后看到提示，可在 App 开启「截屏提示」，并在手机上授权图片和通知权限。该功能只识别开启之后新增的截图记录。支持小米焦点通知参数，系统是否展示超级岛由手机决定。普通通知里也有同一个操作入口。

配对地址由各自的素材库临时生成。联调时可用 `pair-usb.ps1`，它通过 adb reverse 转发到电脑本机服务，正式局域网使用无需 USB。上传接口仅接收图片，需要配对码，不提供读取素材库的接口。

## 改 Logo 和编译

- 替换 `app/src/main/res/drawable/ic_mouse_check.png` 即可换 App 和通知图标，保留文件名。
- 如要改 App 显示名称，在 `app/src/main/AndroidManifest.xml` 里更改 `android:label`。
- 用 Android Studio 打开本目录，安装 Android SDK 36，使用 JDK 17 和 Gradle 8.13。运行 `gradlew.bat assembleDebug`，APK 生成在 `app/build/outputs/apk/debug/`。
- 此源码包只包含 Android 工程、素材和联调脚本。手机配对信息由使用者自行配置。
