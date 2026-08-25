# ai 插件说明

本插件对命令输出做 AI 二次分析：exec/send/read/mouse 响应经 aichat 分析后覆盖 `outputStream`。

## 使用要点

- 挂载后所有 exec/send/read/mouse 输出自动分析
- 分析模式：无 `-o` 时分析纯文本；带 `-o` 时先渲染文件（txt/svg/图片）再分析（可喂视觉模型）
- 分析失败自动回退原始输出并追加 `warning` 字段（不阻断主流程）
- 会话记忆：同一会话的多次分析上下文延续（按会话 uid 续聊）
- 配置：`prompt`（分析提示词）、`timeout`（aichat 调用超时秒数）