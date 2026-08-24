- cl，cmake位置自己找，可能不在系统变量
- 文档：
	- 代码变更时及时更新文档`AGENTS.md`
- 遵守C++语言规范，**遵守项目架构**
- 构建请使用主项目的`BUILD.py`（或本目录`build.py`）

## 变更记录

### COM 跨线程崩溃修复（image_encoder.cpp）
- `ImageEncoder::init()` 不再调用 `ensure_factory()` 创建 WIC factory
  （factory 延迟到第一次 `ensure_factory()` 时由调用线程创建，避免跨线程 COM 对象悬空）
- `ensure_factory()` 每次无条件调用 `CoInitializeEx(nullptr, COINIT_MULTITHREADED)`，
  确保当前线程 COM 公寓已初始化（MTA），其他线程可安全使用 MTA WIC factory
