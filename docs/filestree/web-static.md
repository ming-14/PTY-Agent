# web/static/ 前端静态资源

> 对应 `src/web/static/`,为 Web 端前端资源（HTML / CSS / JS / 第三方库）。从 `src/` 主包树（见 [src.md](src.md)）中单独列出以便查阅。

```
web/static/
├── index.html             # 主页面
├── login.html             # 登录页面
├── service-worker.js      # Service Worker（PWA 离线缓存）

├── css/                   # ═══ 样式（按功能拆分，13 个文件） ═══
│   ├── base.css           # 基础/重置样式
│   ├── layout.css         # 布局
│   ├── theme.css          # 主题变量
│   ├── terminal.css       # 终端
│   ├── tabbar.css         # 标签栏
│   ├── sidebar.css        # 侧边栏
│   ├── responsive.css     # 响应式
│   ├── dialogs.css        # 对话框
│   ├── components.css     # 通用组件
│   ├── fastscreen.css     # 快速屏幕
│   ├── vnc.css            # VNC
│   ├── settings.css       # 设置
│   └── devconsole.css     # 开发控制台

├── js/                    # ═══ JavaScript（按干净架构分层） ═══
│   ├── app.js             # 应用入口
│   ├── domain/            # ═══ 领域层 ═══
│   │   ├── constants.js
│   │   ├── formatters.js
│   │   ├── logger.js
│   │   ├── settingsSchema.js
│   │   └── state.js
│   ├── infrastructure/     # ═══ 基础设施层 ═══
│   │   ├── terminalAdapter.js
│   │   ├── rimeManager.js
│   │   ├── fontLoader.js
│   │   ├── settingsStorage.js
│   │   ├── storage.js
│   │   ├── auth.js
│   │   ├── domUtils.js
│   │   ├── wsClient.js
│   │   └── terminal/       # 终端基础设施（8 文件）
│   │       ├── cursorDebug.js
│   │       ├── events.js
│   │       ├── input.js
│   │       ├── lifecycle.js
│   │       ├── mouseMode.js
│   │       ├── scale.js
│   │       ├── scroll.js
│   │       └── shared.js
│   ├── application/        # ═══ 应用层 ═══
│   │   ├── messageHandlers.js
│   │   ├── ports.js
│   │   └── settingsStore.js
│   └── presentation/       # ═══ 展示层 ═══
│       ├── views/          # 视图（9 文件）
│       │   ├── ui.js
│       │   ├── sizeSelector.js
│       │   ├── detail.js
│       │   ├── settings.js
│       │   ├── fastscreen.js
│       │   ├── vnc.js
│       │   ├── sessionHandlers.js
│       │   ├── devConsole.js
│       │   └── autohide.js
│       └── controllers/    # 控制器（仅 events）
│           └── events.js

└── vendor/                # ═══ 第三方库（外部依赖，不逐文件展开） ═══
    ├── xterm/             # xterm.js 终端前端 + 插件（fit / web-links）
    ├── novnc/             # noVNC 前端（含 core/ app/ vendor/pako）
    ├── rime/              # Rime WASM 输入法（wasm / dict）
    └── rikkajs/           # 桌面宠物 shimeji（img / shimeji.js / shimeji.css）
                        # 注：字体不在此目录；前端字体由 fontLoader.js 从 CDN 加载，Rime 字体随 vendor/rime/dict 提供
```
