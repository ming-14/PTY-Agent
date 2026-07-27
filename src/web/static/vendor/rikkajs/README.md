# rikkajs

六花桌面宠物网页组件

## 快速开始

```html
<link rel="stylesheet" href="shimeji.css">
<script src="shimeji.js"></script>
<script>
  const manager = Shimeji.create({ maxCount: 5 });
  manager.addMascot();
  manager.start();
</script>
```

将 `shimeji.js`、`shimeji.css`、`img/spritesheet.png` 放到网站目录即可。

## API

### Shimeji.create(options)

创建管理器实例。

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| maxCount | number | 5 | 最大桌宠数量 |

### 实例方法

| 方法 | 说明 |
|------|------|
| `addMascot()` | 增加一只桌宠 |
| `gatherAll()` | 所有桌宠集合 |
| `remainOne()` | 只保留一只 |
| `disposeAll()` | 移除全部桌宠 |
| `start()` | 开始动画循环 |
| `stop()` | 停止动画循环 |

## 行为列表

桌宠会根据所处位置（地面/墙壁/天花板）自动选择行为：

- **地面**：站立、行走、跑步、坐下、晃腿、趴下、爬行
- **墙壁**：抓墙、攀爬、从墙上掉落
- **天花板**：抓天花板、天花板爬行、从天花板掉落
- **拖拽**：被抓起时根据移动方向切换姿势，松手后抛物线掉落
- **掉落**：带重力和空气阻力的物理掉落，落地后弹跳

## 文件结构

```
rikkajs/
├── index.html          演示页面
├── shimeji.js          核心引擎
├── shimeji.css         样式
└── img/
    └── spritesheet.png 图集
```

## 自定义

修改 `shimeji.js` 中的 `ACTIONS` 和 `BEHAVIORS` 对象可自定义动画和行为。替换 `img/spritesheet.png` 可更换角色外观，保持 128×128 帧尺寸和 8 列布局即可。
