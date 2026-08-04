# web_rime/ Rime WASM 输入法

> Rime WASM 输入法插件源码。

```
web_rime/                    # Rime WASM 输入法插件源码
├── .gitignore
├── AGENTS.md
├── README.md
├── plugin/                  # 前端插件（TypeScript + Webpack）
│   ├── package.json / package-lock.json
│   ├── tsconfig.json
│   ├── webpack.config.js
│   └── src/
│       ├── ime.ts / index.ts / manager.ts / panel.ts / toolbar.ts / types.ts
│       ├── backends/        # base.ts / remote.ts / wasm.ts
│       └── keyboard/        # dom/index/layouts/render/theme/touch/types/viewport
│           └── tkl/         # 87 键布局：dom/index/layouts/render/theme/touch/viewport
├── rime-config/             # Rime 配置
│   ├── lua/date_translator.lua
│   └── rime.lua
└── server/                  # 输入法服务端（TypeScript）
    ├── package.json / package-lock.json / tsconfig.json
    ├── wasm/                # rime.js / rime.data
    ├── src/                 # dict-manager.ts / index.ts / rime-engine.ts / ws-handler.ts
    └── dict/                # 词典（double-pinyin / luna-pinyin / pinyin-simp / stroke / terra-pinyin）
```
