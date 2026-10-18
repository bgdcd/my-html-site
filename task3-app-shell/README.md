# 任务单三平板 APK 外壳

此项目把仓库根目录的 `index.html` 原样复制到 Android assets 中，并由 Android WebView 加载。

- 不改动 HTML 的页面、计算公式、交互逻辑或脚本。
- Android 应用横屏显示，支持 JavaScript、DOM Storage 和网页缩放。
- 源文件：`../index.html`
- 打包内文件：`app/src/main/assets/index.html`

GitHub Actions 构建成功后，在对应运行记录的 Artifacts 区域下载 `task3-tablet-apk`，解压后安装 `app-debug.apk`。
