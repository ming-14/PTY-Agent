"""leaf 分屏终端 — 干净架构四层洋葱模型。

    leaf.domain   实体层：纯领域规则（布局/事件/渲染规则），零外部依赖
    leaf.usecases 用例层：应用编排（帧合成/输入路由），依赖端口协议
    leaf.adapters 接口适配层：Win32 输入归一化、输出适配
    leaf.drivers  框架与驱动层：pywezterm、Win32 Console API
    leaf.app      组合根：依赖组装、线程编排、CLI 入口

依赖方向：domain ← usecases ← adapters ← drivers ← app，只允许外层 import 内层。
"""
