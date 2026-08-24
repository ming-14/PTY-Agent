"""WebSocket 消息用例处理器包。

按领域分组：
- base：基类（HandlerContext / MessageHandler）与共享工具函数
- system：系统信息（ping / 会话列表 / shell 列表 / 系统状态）
- session：会话运行时（创建 / 订阅 / 输入 / 信号 / resize / 终止）
- detail：会话详情（活动详情 / 详情刷新）
- history：历史记录（列表 / 详情 / 删除）
- vnc：VNC 远程桌面
- screenshare：FastScreen 屏幕查看
- cursor：光标定位器
- size_mode：自适应排他锁（接管 / 模式设定）
- registry：消息类型 → 处理器映射注册表
"""