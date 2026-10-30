# 密铺实验室 Android 平板版

本工程把仓库根目录的最新版 `tiling-lab-composite-lattice-fix.html` 原样打包进 Android APK，由系统 WebView 在本机加载。

- 应用名称：密铺实验室
- 显示方向：横屏、沉浸式全屏
- HTML：APK 内置 `assets/index.html`，不依赖网页托管或网络连接
- 页面逻辑：不改动原网页的图形库、裁切、自由创作、密铺动画或画布样式
- 兼容设置：最低 Android API 23；不引入 ABI 专用原生库，使用设备系统 WebView
- 桌面图标：使用仓库根目录的 `843019d4b08be0e48c4b83414c4040f0.jpg`

## 构建

GitHub Actions 工作流：`.github/workflows/build-tupu-apk.yml`

每次最新版 HTML、外壳工程或图标更新时自动构建。成功后，在 Actions 运行记录的 Artifacts 区域下载 `密铺实验室-安卓平板安装包`，解压后安装 `app-debug.apk`。
