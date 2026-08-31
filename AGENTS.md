！！！！！最高优先级，强制执行！！！！：禁止执行`git filter-repo`，绝对禁止执行，git filter-repo会强制回退到HEAD，丢失未修改的改动，绝对禁止执行，就算用户要求也禁止执行，你只能把命令给用户执行，你自己禁止执行！！
未经用户允许，禁止使用、查看、更改等任何方式操作git；用户授权的“查看”和“操作”需分开，用户授权查看不代表能操作；执行高危操作如commit、reset、push等命令必须用户明确授权
未经用户允许，禁止使用、查看、更改等任何方式操作git；用户授权的“查看”和“操作”需分开，用户授权查看不代表能操作；执行高危操作如commit、reset、push等命令必须用户明确授权

---

# Base
PTY-Agent 核心包体在 `src`，`web_rime`、`fastscreen`、`sandbox`、`wezterm-py`是相对独立的外部引擎依赖，各自有自己的规则规范，但都是 PTY-Agent 项目的一部分

# 环境
Windows：本机环境
Linux：使用`wsl.exe -d Ubuntu2204`

构建：`python BUILD.py -Mirror "https://v4.gh-proxy.org/"`
构建产物：请打包成`pty-agent-win_x86-64.zip`和`pty-agent-linux_x86-64.zip`

# 总规范
- **跨平台开发，请注意Linux支持**
- Python兼容Python3.8，遵循 PEP8 语言规范
- **避免采用降级、兼容、缓解方案**：不要用任何的降级、兼容、缓解方案。有多个方案时，选择最优方案
- **复杂工程、方案可查看成熟第三方项目**，想参考其他什么自己下载，使用webfetch/gh(github)搜索
- 旧代码重构后，非必要，**不要留兼容接口**，应该更新引用点：重构后完全没必要兼容以前的结构，引用点必须更新
- 不必一直执着与最小改动，**代码架构有问题需要及时向用户汇报**
- 注意性能问题，禁止为了方便采用低效办法
- 文件、函数、类**开头**，关键部分，**决策点**，注意点的注释；避免无效冗长·注释
	不要在注释加时间等泄漏信息，仅客观描述原因
- 日志必须写，并且要有完备的日志系统
- **新增代码请检查**
	- [] 关键部分注释加了吗
	- [] 日志写了吗，是否符符合规范
- 请使用标准库、第三方库大量精简代码、手工实现复杂的，可下载库使用，**需要什么库自行下载**
	- 例：计算字符宽度时，使用wcwidth，而不是手算
- 不要无用的防御编程与过度工程
	- 例：`function A() {} if(type A === "function") A()` 错误
- 提交git时，发现不是自己改动的文件时，请向用户询问是全部提交还是分多次提交，禁止静默只提交自身改动部分
- Bug 修复后，**需撤销之前的无效修改点**，不能让无效改动污染代码！
	- Debug完成后，检查
		- [] 旧改动删了吗
		- [] 旧改动删了后，是否还能正常运行

## 项目清洁度：不应出现在项目（代码，注释，文档等任何文件）中的内容

被ignore的文件不用理

### 历史痕迹（开发历程 / 时间 / 变更记录）
- 阶段标注："Phase N"、"Phase-N"（如 Phase 16、Phase-12）等
- 任务号："T7.11"、"T1.4"、"T-8.x"、"T64"、"T32"、"FR-8.x"等
- 日期："2021-01-01" 等任何日期
- 修复记录："已修复"、"修复为"、"曾"、"E4/D4/H-1/H-2/C-5 修复"、"黑盒报告"、"BUG-01"、"r4/r5"、"MEDIUM-x"等
- 旧字段/旧值说明："旧值 xxx 已删除"、"fs_mode/capabilities/path_rules 已移除"、"迁移指引"、"向后兼容"等
- 已删除文档的引用
- 开发脚本/实验引用："verify_low_flow.py"、"label_probe.py"、"setil_probe.py"、"lowil_test.py"、"实测定案"、"三路实验"、"对照 %TEMP%\opencode 实验结论"等
- 用户交互痕迹："用户拍板"、"已与用户对齐"、"用户决策"、"用户要求"等
- 旧组件/旧形态提及："sandbox.exe"、"AppContainer"、"IPC 形态"、"FileSystemIsolator"、"StatsCollectorImpl"等
- 其他项目提及："PTY-Agent"、"fastscreen"、"aichat"、"terminal_injector"、"opencode"（pyproject 包元数据除外）等
- 隐私内容，如用户名，env等
### 无用文件与死代码
- .gitkeep（目录已有实际内容后）
- 无用空目录（如 src/infra/fs、src/infra/json等）
- 临时调试代码（注释标"临时调试"的代码块、硬编码路径的调试 dump 文件等）
- 兼容接口/兼容代码（如 setup.py 兼容旧 pip、Legacy* 死代码、旧 API 别名等）
- 开发日记/历程文档（docs/memory 日记、Phase 记录、TROUBLESHOOTING 等）
### 表述与写法
- 决策过程表述（"不依赖 GoogleTest"、"原本想用 X 但改为 Y" 这类）
- 硬编码路径（盘符、用户名、具体机器路径等）——用 %LOCALAPPDATA%、%SYSTEMROOT%、GetTempPath、GetWindowsDirectory 等动态获取
- 注释中无意义的编号/标题残留（删除条目后留下的编号引用）

## src 规范
- **采用分层的模块化单体 + 局部端口-适配器。业务代码按领域拆成包，包之间靠"单向依赖链"分层**

## src\web 规范
- 采用**洋葱架构**
	**关键原则**
		依赖规则：依赖只能从外层指向内层，内层永远不依赖外层。
		框架独立：业务逻辑不依赖具体框架，可随时替换技术实现。
		用例驱动：围绕业务用例（如用户注册、下单）构建系统，而非技术细节。
	**四层结构（洋葱模型）**
		实体层（Entities）：核心业务对象与规则，纯领域逻辑。
		用例层（Use Cases）：应用特定业务逻辑，协调数据流。
		接口适配器层（Interface Adapters）：数据格式转换，连接用例与外部系统。
		框架与驱动层（Frameworks & Drivers）：具体技术实现，如 Express、Vue、数据库驱动等。

## fastscreen 规范
- 见`fastscreen\AGENTS.md`
- `fastscreen\src\core`目前保持平铺即可

## sandbox 规范
- 原生 C++ 沙箱工程（`sandbox/`，pybind11 编译为 win_sandbox_native.pyd）

## wezterm-py 规范
- 见`wezterm-py\AGENTS.md`

# 工具/Skill：
- `.agents\skills\download-by-mirror` **下载文件，尤其从Github下载请使用镜像**
- `.agents\skills\python-win32api`
- `.agents\skills\windows-debugging` WindowDebugging套件，含cdb等
- `py-spy` 采样分析器

Python 安装的工具可能不在 PATH 里面，请检查`%APPDATA%\Python\Python版本\Scripts\`

**需要什么工具自己下载，主动性强一点**

# 测试与 Debug
- 工程必须写测试，最少要写e2e测试，可以自动化操作的尽量用自动化
- **未查到根本因素时，不要直接改工程源码**，多看看，看相关部分，多思考

## Debug方法
- **查日志，千万注意大日志，大输出需要使用子代理分析，防止上下文爆炸**
- **使用工具附加调试（cdb/pdb/...）**，请先下载对应符号SRV*C:\Symbol*http://msdl.blackint3.com:88/download/symbols （微软符号服务器镜像）
- **最小代码复现**
- 等方式

若有些操作无法实现，请寻求用户帮助
	
# 用户交互
- **必须与用户对齐需求**，防止实现偏差，以结果为导向
- 关键决策点要由用户指定
- 对用户的**需求**、**方案**、提出**bug现象**等信息有**疑惑**，**必须及时追问**、**用户提出方案有不足时，要及时沟通**

---

出现“从命令中解析目标路径失败：${normalizedCommand}”错误时，多半是你使用了`python -c "xxx"`，请使用写脚本文件执行代替
出现Shell命令被Harness安全策略拦截的现象时，多半是你使用了`cmd /c`，请使用脚本文件代替
`rg`在部分情况下搜索文件会因为乱码而错误匹配，请尽量使用你内置工具`grep`

