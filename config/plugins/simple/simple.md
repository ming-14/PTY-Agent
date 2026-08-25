# simple 插件说明

本插件在客户端进程内把命令输出类响应渲染为自然文本（替代默认 JSON 打印），末尾附 triggerReturnReason 与执行时间尾巴。

## 使用要点

- 自动生效：挂载后 exec/send/read/mouse 的输出自动以文本而非 JSON 形式展示
- 错误响应与无输出流响应不干预，走默认 JSON 输出