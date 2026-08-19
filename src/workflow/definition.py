"""Workflow 定义解析与校验（YAML）

定义文件结构：
    name: str                 # 可选，工作流名称
    vars: {k: v}              # 可选，全局变量（可被 CLI --vars 覆盖）
    max_parallel: int         # 可选，最大并行步骤数（默认取配置）
    steps:
      - id: str               # 必填，步骤唯一标识（表达式引用名）
        type: exec|send|read|kill|wait
        ... 步骤字段（见各类型必填校验）
        if: expr              # 可选，条件表达式，为假时跳过
        depends_on: [id...]   # 可选，显式依赖；未声明时隐式依赖前一个步骤
        on_error: fail|continue|ignore   # 可选，默认 fail
        retry: int            # 可选，失败重试次数（默认 0）
        retry_interval: float # 可选，重试间隔秒数（默认 1.0）

解析阶段完成：YAML 加载、步骤字段校验、隐式依赖显式化、循环依赖检测。
执行时（engine）再按运行时变量插值 {{...}} 与 if 条件求值。
"""

from ..logging import get_logger
import re
from typing import Dict, List, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

_logger = get_logger("pty-daemon")

VALID_TYPES = ("exec", "send", "read", "kill", "wait")
ON_ERROR_VALUES = ("fail", "continue", "ignore")

# 各步骤类型必填字段（定义期校验；运行时插值后字段可为空由执行期报错）
REQUIRED_FIELDS = {
    "exec": ("session", "command"),
    "send": ("session", "input"),
    "read": ("session",),
    "kill": ("session",),
    "wait": ("seconds",),
}

_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class DefinitionError(ValueError):
    """workflow 定义文件解析/校验错误"""


class ParsedStep:
    """单个步骤的解析结果（原始字段 + 流程控制元数据）"""

    __slots__ = ("idx", "id", "type", "raw", "depends_on", "on_error",
                 "retry", "retry_interval", "condition")

    def __init__(self, idx: int, id: str, type: str, raw: dict,
                 depends_on: Optional[List[str]], on_error: str, retry: int,
                 retry_interval: float, condition: Optional[str]):
        self.idx = idx
        self.id = id
        self.type = type
        self.raw = raw
        self.depends_on = depends_on
        self.on_error = on_error
        self.retry = retry
        self.retry_interval = retry_interval
        self.condition = condition


class WorkflowDefinition:
    """解析后的 workflow 定义（步骤列表 + 全局 vars + 并行上限）"""

    def __init__(self, name: str, vars: Dict, max_parallel: int,
                 steps: List[ParsedStep]):
        self.name = name
        self.vars = vars
        self.max_parallel = max_parallel
        self.steps = steps

    def step_by_id(self, step_id: str) -> Optional[ParsedStep]:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None


def _require_yaml():
    if yaml is None:
        raise DefinitionError(
            "PyYAML 未安装，无法解析 workflow 定义文件（pip install pyyaml）"
        )


def _err(msg: str) -> DefinitionError:
    return DefinitionError(msg)


def _check_cycle(steps: List[ParsedStep]) -> None:
    """DFS 检测依赖环；同时确认 depends_on 引用的 id 均存在"""
    ids = {s.id for s in steps}
    for s in steps:
        for dep in s.depends_on:
            if dep == s.id:
                raise _err("步骤 '%s' 依赖自身" % s.id)
            if dep not in ids:
                raise _err(
                    "步骤 '%s' 的 depends_on 引用了不存在的步骤 '%s'" % (s.id, dep)
                )

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {s.id: WHITE for s in steps}
    steps_by_id = {s.id: s for s in steps}

    def dfs(sid: str, path: List[str]) -> None:
        color[sid] = GRAY
        path.append(sid)
        step = steps_by_id[sid]
        for dep in step.depends_on:
            if color[dep] == GRAY:
                cycle = " -> ".join(path[path.index(dep):] + [dep])
                raise _err("依赖环检测到: %s" % cycle)
            if color[dep] == WHITE:
                dfs(dep, path)
        path.pop()
        color[sid] = BLACK

    for s in steps:
        if color[s.id] == WHITE:
            dfs(s.id, [])


def _validate_step(idx: int, raw: dict) -> ParsedStep:
    if not isinstance(raw, dict):
        raise _err("步骤 #%d 必须是映射（dict）" % (idx + 1))

    step_id = raw.get("id")
    if not isinstance(step_id, str) or not step_id.strip():
        raise _err("步骤 #%d 缺少非空 id" % (idx + 1))
    step_id = step_id.strip()
    if not _ID_RE.match(step_id):
        raise _err(
            "步骤 id '%s' 非法（仅允许字母/数字/下划线/连字符）" % step_id
        )

    step_type = raw.get("type")
    if step_type not in VALID_TYPES:
        raise _err(
            "步骤 '%s' 的 type '%s' 非法（可选: %s）"
            % (step_id, step_type, "/".join(VALID_TYPES))
        )

    for field in REQUIRED_FIELDS[step_type]:
        if field not in raw or raw[field] in (None, ""):
            raise _err("步骤 '%s' 缺少必填字段: %s" % (step_id, field))

    if step_type == "send":
        # eol/json 与 CLI send 对齐：名称映射见 client.config_manager._SEND_EOL_MAP
        eol = raw.get("eol", "cr")
        if eol not in ("lf", "crlf", "cr", "none"):
            raise _err(
                "步骤 '%s' 的 eol '%s' 非法（可选: lf/crlf/cr/none）" % (step_id, eol)
            )
        if "json" in raw and not isinstance(raw["json"], bool):
            raise _err("步骤 '%s' 的 json 必须是布尔值" % step_id)

    on_error = raw.get("on_error", "fail")
    if on_error not in ON_ERROR_VALUES:
        raise _err(
            "步骤 '%s' 的 on_error '%s' 非法（可选: fail/continue/ignore）"
            % (step_id, on_error)
        )

    retry = raw.get("retry", 0)
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
        raise _err("步骤 '%s' 的 retry 必须是非负整数" % step_id)

    retry_interval = raw.get("retry_interval", 1.0)
    if not isinstance(retry_interval, (int, float)) or retry_interval < 0:
        raise _err("步骤 '%s' 的 retry_interval 必须是非负数" % step_id)

    condition = raw.get("if")
    if condition is not None and not isinstance(condition, str):
        raise _err("步骤 '%s' 的 if 条件必须是字符串表达式" % step_id)

    depends_on = raw.get("depends_on")
    if depends_on is None:
        depends_on = None  # 未声明 → 隐式依赖前一个步骤（串行）
    elif isinstance(depends_on, str):
        depends_on = [depends_on]
    elif isinstance(depends_on, list) and all(isinstance(d, str) for d in depends_on):
        depends_on = list(depends_on)  # 显式声明（含空列表=无依赖）
    else:
        raise _err("步骤 '%s' 的 depends_on 必须是 id 列表" % step_id)

    return ParsedStep(
        idx=idx,
        id=step_id,
        type=step_type,
        raw=raw,
        depends_on=depends_on,
        on_error=on_error,
        retry=retry,
        retry_interval=float(retry_interval),
        condition=condition,
    )


def parse_definition(
    text: str,
    default_max_parallel: int = 4,
    max_parallel_override: Optional[int] = None,
) -> WorkflowDefinition:
    """解析 workflow 定义文本（YAML）并校验

    Args:
        text: YAML 定义文本。
        default_max_parallel: max_parallel 未声明时的默认值。
        max_parallel_override: 调用方显式覆盖（CLI --parallel），优先于定义值。

    Raises:
        DefinitionError: YAML 解析失败或结构校验失败。
    """
    _require_yaml()
    try:
        data = yaml.safe_load(text)
    except Exception as e:
        raise _err("YAML 解析失败: %s" % e) from e

    if data is None:
        raise _err("workflow 定义为空")
    if not isinstance(data, dict):
        raise _err("workflow 定义必须是映射（dict）")

    name = data.get("name")
    if name is not None and not isinstance(name, str):
        raise _err("name 必须是字符串")

    vars_raw = data.get("vars", {})
    if not isinstance(vars_raw, dict):
        raise _err("vars 必须是映射（dict）")
    for k, v in vars_raw.items():
        if not isinstance(v, (str, int, float, bool)):
            raise _err("变量 '%s' 的值类型不支持（仅 str/int/float/bool）" % k)

    max_parallel = data.get("max_parallel")
    if max_parallel is not None and (
        not isinstance(max_parallel, int)
        or isinstance(max_parallel, bool)
        or max_parallel < 1
    ):
        raise _err("max_parallel 必须是 >= 1 的整数")
    if max_parallel is None:
        max_parallel = default_max_parallel
    if max_parallel_override is not None:
        if not isinstance(max_parallel_override, int) or max_parallel_override < 1:
            raise _err("--parallel 必须是 >= 1 的整数")
        max_parallel = max_parallel_override

    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise _err("steps 必须是非空列表")

    steps = [_validate_step(i, raw) for i, raw in enumerate(steps_raw)]

    # id 唯一性
    seen = {}
    for s in steps:
        if s.id in seen:
            raise _err(
                "步骤 id 重复: '%s'（#%d 与 #%d）"
                % (s.id, seen[s.id] + 1, s.idx + 1)
            )
        seen[s.id] = s.idx

    # 隐式依赖显式化：未声明 depends_on 的步骤依赖前一个步骤（串行语义）；
    # 显式声明（含空列表）保持不变，空列表表示无依赖（可并行）
    prev_id = None
    for s in steps:
        if s.depends_on is None:
            if prev_id is not None:
                s.depends_on = [prev_id]
            else:
                s.depends_on = []
        prev_id = s.id

    _check_cycle(steps)
    _logger.info(
        "workflow 定义解析成功: name=%r steps=%d max_parallel=%d",
        name,
        len(steps),
        max_parallel,
    )
    return WorkflowDefinition(
        name=name,
        vars=dict(vars_raw),
        max_parallel=max_parallel,
        steps=steps,
    )