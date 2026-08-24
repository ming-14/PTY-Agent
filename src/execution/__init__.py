"""会话执行层 —— exec/send/read 与 workflow 共用的执行原语。

执行引擎（daemon 服务器与 workflow 编排共同依赖，
消除 daemon⇄workflow 包级循环）：
- conditions：返回条件声明（ReturnConditions / RequestContext）
- filtering：输出过滤（行/列/grep 与 ANSI 剥离）
- output_policy：取源与 offset 策略
- response：响应装配（build_result 等）
- utils：请求校验与输入准备
- execution：执行流程（快照 / 子进程触发 / 子进程无触发）
- context：HandlerContext（daemon 与 workflow 共享的执行上下文）

流程函数经本包顶层再导出，业务层统一 `from ...execution import _run_snapshot_flow` 等。
"""

from .execution import (
    _run_snapshot_flow,
    _run_subprocess_no_trigger_flow,
    _run_subprocess_trigger_flow,
    assemble_response,
)