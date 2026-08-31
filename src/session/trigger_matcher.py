"""触发条件匹配器 — 正则匹配 + 输出静默超时检测

职责独立于 Session，不持有 PTY 或缓冲区引用，通过回调与
OutputBuffer / Session 协作。

关键设计:
- 匹配逻辑在持锁路径（OutputBuffer.lock）中执行，通过传入的
  OutputBuffer 引用直接读取原始字节。
- 解码依赖外部的 decode_func 回调（Session._decode_only_len），
  避免引入编码探测的循环依赖。
- 滚动解码缓存：等待窗口内的已解码文本跨 check 复用，每块只增量
  解码新增字节并 append，避免对整段窗口重复解码+重扫（O(窗口)→O(块长)）。
- ReDoS 防护: CPython 中正则搜索持 GIL 不放，无法用线程池/超时安全中断
  运行中的灾难性回溯（实测验证），因此安全边界在预检：_check_regex_complexity
  判为安全的模式（确定性线性结构）在调用线程直接搜索，其余一律降级为
  O(n) 线性子串匹配，任何输入都不会持锁阻塞。
"""

import functools
import re
import threading
import time
from typing import Callable, Optional

from ..config.daemon import MAX_TRIGGER_SCAN
from ..logging import get_logger

_logger = get_logger("pty-session")

# 跨块匹配尾部重叠字符数：搜索只覆盖新增文本 + 此前最近一段尾部，
# 旧文本在之前的 check 中已全量搜索无命中（重叠区仅供跨块模式命中）。
_SCAN_TAIL_OVERLAP = 4096

# 残缺尾部字节封顶：合法的不完整多字节序列 ≤ 4 字节，更大说明是
# 持续无法解码的异常字节流，封顶防止尾部无限累积导致逐块 O(n)。
_MAX_TAIL_BYTES = 16


@functools.lru_cache(maxsize=256)
def _check_regex_complexity(pattern: str) -> bool:
    """保守判定正则是否存在灾难性回溯风险（True=安全=内联，False=风险=子串匹配）

    在 CPython 中正则搜索持 GIL 不放，线程池限时无法中断运行中的回溯
    （实测验证），故存在风险的模式必须拒绝正则执行，降级为 O(n) 线性
    子串匹配；仅判定为安全的模式才在调用线程直接搜索。

    风险规则（任一命中即返回 False）：
    - 量化词作用于分组（含命名/非捕获/环视）：(a+)+b、(a|a)*b、(a*)*b
      分组量化在失败时组合数指数增长。
    - 分组内同时出现交替与量化词：(a+|b) 无锚定输入时 O(n²)。
    - 相邻分组的内部歧义交替（分支字符集重叠）：(a|a)(a|a) 指数组合。
    - 环视与反向引用：回溯行为难以静态分析。
    - 安全集限定：每个量化词须满足（终位 || （非首消费元且前驱字符集不交）），
      且非终位量化词至多一个；否则量化词的失败回溯可被起点重复触发
      （如 a+X 在 aⁿ 上 O(n²)，实测 4 倍耗时随规模翻倍）。
    字符类内与转义字符内的括号/竖线/量化词按字面处理，不参与分组计数。
    """
    # ── 辅助常量/函数 ──
    _DIGITS = frozenset("0123456789")
    _WORD = frozenset(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
    )
    _SPACE = frozenset(" \t\n\r\f\v")
    _HEX = "0123456789abcdefABCDEF"

    def _overlap(s1, s2):
        """两个字符集是否可能匹配同一字符（None 视为任意，即与一切重叠）"""
        return s1 is None or s2 is None or bool(s1 & s2)

    def _branches_overlap(branches):
        for i in range(len(branches)):
            si = branches[i]
            for j in range(i + 1, len(branches)):
                if _overlap(si, branches[j]):
                    return True
        return False

    def _brace_quant_end(pat, i):
        """尝试解析 {m} / {m,} / {m,n} 量化词；成功返回 } 后下标，否则 None"""
        n = len(pat)
        j = i + 1
        while j < n and "0" <= pat[j] <= "9":
            j += 1
        if j < n and pat[j] == "}":
            return j + 1
        if j < n and pat[j] == ",":
            j += 1
            while j < n and "0" <= pat[j] <= "9":
                j += 1
            if j < n and pat[j] == "}":
                return j + 1
        return None

    def _is_hex4(s):
        return len(s) == 4 and all(ch in _HEX for ch in s)

    # ── 扫描状态 ──
    n = len(pattern)
    i = 0
    # 分组栈: [has_alt, has_quant, gset, branch_sets, cur_branch, last_amb]
    #   gset / cur_branch: 字符集（None = 任意）；branch_sets: 已完成分支字符集
    #   last_amb: 该层级上一个原子是否为内部歧义交替组（R3b 相邻检测用）
    stack = []
    top_last_amb = False
    # 当前原子（待量化）与上一个已提交原子
    cur_set = None
    cur_kind = None  # None | 'atom' | 'group'
    cur_amb = False
    prev_set = None
    # 消费元计数 / 首个消费元序号
    atom_idx = 0
    first_atom_idx = -1
    # 风险标志
    r_quant_group = False  # 量化词作用于分组
    r_alt_quant = False    # 分组内交替+量化词
    r_amb_group = False    # 相邻歧义交替组
    r_lookbackref = False  # 环视/反向引用
    # 量化词信息: (作用原子序号, 是否首消费元, 前驱字符集, 自身字符集)
    q_infos = []

    def _set_last_amb(amb):
        nonlocal top_last_amb
        if stack:
            stack[-1][5] = amb
        else:
            top_last_amb = amb

    def _get_last_amb():
        if stack:
            return stack[-1][5]
        return top_last_amb

    def _add_to_group(s):
        """将原子字符集 s 并入当前栈顶分组的 gset 与 cur_branch（None 即任意）"""
        if not stack:
            return
        g = stack[-1]
        if s is None:
            g[2] = None
            g[4] = None
        else:
            if g[2] is not None:
                g[2] = g[2] | s
            if g[4] is not None:
                g[4] = g[4] | s

    def _commit():
        """将当前未量化原子 cur 提交为前驱（prev_set）"""
        nonlocal prev_set, cur_set, cur_kind, cur_amb
        if cur_kind is not None:
            prev_set = cur_set
            cur_set = None
            cur_kind = None
            cur_amb = False

    def _complete_atom(s, amb=False):
        """完成一个原子（字面量/字符类/转义）：登记序号并并入所在分组"""
        nonlocal atom_idx, first_atom_idx, cur_set, cur_kind, cur_amb
        _commit()
        cur_set = s
        cur_kind = "atom"
        cur_amb = amb
        atom_idx += 1
        if first_atom_idx < 0:
            first_atom_idx = atom_idx
        _add_to_group(s)
        _set_last_amb(amb)

    def _set_cur_group(s, amb):
        """分组闭合后成为当前原子"""
        nonlocal cur_set, cur_kind, cur_amb
        _commit()
        cur_set = s
        cur_kind = "group"
        cur_amb = amb
        _add_to_group(s)
        _set_last_amb(amb)

    def _apply_quantifier():
        """量化词处理：R1 检查 + 记录量化词信息"""
        nonlocal r_quant_group, prev_set, cur_set, cur_kind, cur_amb
        if stack:
            stack[-1][1] = True
        if cur_kind == "group":
            r_quant_group = True
            return
        q_infos.append((atom_idx, first_atom_idx == atom_idx, prev_set, cur_set))
        prev_set = cur_set
        cur_set = None
        cur_kind = None
        cur_amb = False

    # ── 主扫描循环 ──
    while i < n:
        c = pattern[i]

        # ── 转义序列 ──
        if c == "\\":
            if i + 1 >= n:
                _complete_atom(frozenset(("\\",)))
                i += 1
                continue
            e = pattern[i + 1]
            if e in ("d", "w", "s"):
                _complete_atom(_DIGITS if e == "d" else (_WORD if e == "w" else _SPACE))
                i += 2
                continue
            if e in ("D", "W", "S"):
                _complete_atom(None)
                i += 2
                continue
            if e in ("b", "B", "A", "Z", "z"):
                i += 2  # 零宽断言：不构成原子
                continue
            if e == "x":
                if i + 3 < n and _is_hex4(pattern[i + 2 : i + 4]):
                    _complete_atom(frozenset((chr(int(pattern[i + 2 : i + 4], 16)),)))
                    i += 4
                else:
                    _complete_atom(None)
                    i += 2
                continue
            if e == "u":
                if i + 5 < n and _is_hex4(pattern[i + 2 : i + 6]):
                    _complete_atom(frozenset((chr(int(pattern[i + 2 : i + 6], 16)),)))
                    i += 6
                else:
                    _complete_atom(None)
                    i += 2
                continue
            if e == "U":
                if i + 9 < n and all(ch in _HEX for ch in pattern[i + 2 : i + 10]):
                    _complete_atom(frozenset((chr(int(pattern[i + 2 : i + 10], 16)),)))
                    i += 10
                else:
                    _complete_atom(None)
                    i += 2
                continue
            if e == "N":
                clos = pattern.find("}", i + 3)
                if clos < 0:
                    _complete_atom(None)
                    i += 2
                else:
                    _complete_atom(None)  # \N{name} 字符集未知
                    i = clos + 1
                continue
            if "1" <= e <= "9":
                r_lookbackref = True
                return False  # \1..\9 反向引用
            if "0" <= e <= "7":
                # 八进制 \0..\377（仅剩 \0 前缀：\1..\7 已按反向引用处理）
                j = i + 2
                while j < n and j < i + 5 and "0" <= pattern[j] <= "7":
                    j += 1
                _complete_atom(frozenset((chr(int(pattern[i + 1 : j], 8)),)))
                i = j
                continue
            # 其余转义：字面量单字符
            _complete_atom(frozenset((e,)))
            i += 2
            continue

        # ── 字符类 ──
        if c == "[":
            j = i + 1
            negated = False
            if j < n and pattern[j] == "^":
                negated = True
                j += 1
            chars = set()
            unknown = False
            first = True
            last = None
            while j < n:
                ch = pattern[j]
                if ch == "]":
                    if first:
                        chars.add("]")
                        j += 1
                        first = False
                        continue
                    break
                if ch == "\\":
                    if j + 1 >= n:
                        unknown = True
                        j += 1
                        break
                    e = pattern[j + 1]
                    if e in ("d", "w", "s"):
                        chars.update(
                            _DIGITS if e == "d" else (_WORD if e == "w" else _SPACE)
                        )
                        last = None
                        j += 2
                        first = False
                        continue
                    if e in ("D", "W", "S"):
                        unknown = True
                        j += 2
                        first = False
                        continue
                    if e == "x":
                        if j + 3 < n and _is_hex4(pattern[j + 2 : j + 4]):
                            chars.add(chr(int(pattern[j + 2 : j + 4], 16)))
                            last = None
                            j += 4
                        else:
                            unknown = True
                            j += 2
                        first = False
                        continue
                    if e == "u":
                        if j + 5 < n and _is_hex4(pattern[j + 2 : j + 6]):
                            chars.add(chr(int(pattern[j + 2 : j + 6], 16)))
                            last = None
                            j += 6
                        else:
                            unknown = True
                            j += 2
                        first = False
                        continue
                    if "0" <= e <= "7":
                        k = j + 2
                        while k < n and k < j + 5 and "0" <= pattern[k] <= "7":
                            k += 1
                        chars.add(chr(int(pattern[j + 1 : k], 8)))
                        last = None
                        j = k
                        first = False
                        continue
                    # 类内其他转义：字面量
                    chars.add(e)
                    last = e
                    j += 2
                    first = False
                    continue
                if (
                    ch == "-"
                    and not first
                    and last is not None
                    and j + 1 < n
                    and pattern[j + 1] != "]"
                ):
                    # 范围 a-z
                    j += 1
                    nxt = pattern[j]
                    if nxt == "\\":
                        unknown = True
                        j += 2
                        first = False
                        last = None
                        continue
                    if last <= nxt:
                        lo, hi = ord(last), ord(nxt)
                        if hi - lo <= 512:
                            chars.update(chr(x) for x in range(lo, hi + 1))
                        else:
                            unknown = True
                    else:
                        unknown = True
                    j += 1
                    first = False
                    last = None
                    continue
                chars.add(ch)
                last = ch
                j += 1
                first = False
                continue
            if j >= n:
                i = n  # 未闭合字符类：非法正则（编译错误），扫描结果无关
                continue
            cs = None if (negated or unknown or len(chars) > 512) else frozenset(chars)
            _complete_atom(cs)
            i = j + 1
            continue

        # ── 分组 ──
        if c == "(":
            if i + 1 >= n:
                stack.append([False, False, frozenset(), [], frozenset(), False])
                i += 1
                continue
            if pattern[i + 1] == "?":
                q = pattern[i + 2] if i + 2 < n else ""
                if q == "P":
                    if i + 3 < n and pattern[i + 3] == "<":
                        gt = pattern.find(">", i + 4)
                        if gt < 0:
                            stack.append(
                                [False, False, frozenset(), [], frozenset(), False]
                            )
                            i += 3
                        else:
                            stack.append(  # 命名捕获组 (?P<name>...)
                                [False, False, frozenset(), [], frozenset(), False]
                            )
                            i = gt + 1
                        continue
                    r_lookbackref = True
                    return False  # (?P=name) 反向引用
                if q == "#":
                    close = pattern.find(")", i + 3)  # (?# 注释
                    i = n if close < 0 else close + 1
                    continue
                if q in ("=", "!"):
                    r_lookbackref = True
                    return False  # 环视 (?= (?! 
                if q == "<":
                    if i + 3 < n and pattern[i + 3] in ("=", "!"):
                        r_lookbackref = True
                        return False  # 后行环视 (?<= (?<!
                    stack.append(  # 旧语法 (?<name>) → 按分组处理
                        [False, False, frozenset(), [], frozenset(), False]
                    )
                    i += 2
                    continue
                if q == ":":
                    stack.append(  # (?: 非捕获组
                        [False, False, frozenset(), [], frozenset(), False]
                    )
                    i += 3
                    continue
                # (?flags) / (?flags:...) / 未知
                j = i + 2
                while j < n and pattern[j] in "aiLmsux-":
                    j += 1
                if j < n and pattern[j] == ")":
                    i = j + 1  # 仅标志组，零宽
                    continue
                if j < n and pattern[j] == ":":
                    stack.append(  # (?flags:...)
                        [False, False, frozenset(), [], frozenset(), False]
                    )
                    i = j + 1
                    continue
                stack.append(  # 未知 (? 结构 → 保守按分组
                    [False, False, frozenset(), [], frozenset(), False]
                )
                i += 2
                continue
            stack.append([False, False, frozenset(), [], frozenset(), False])
            i += 1
            continue

        # ── 分组关闭 ──
        if c == ")":
            if not stack:
                i += 1  # 多余的 )：编译错误，忽略
                continue
            alt, quant, gset, branches, cb, _ = stack.pop()
            branches.append(cb)
            if stack:  # 标志传播到父组
                p = stack[-1]
                if alt:
                    p[0] = True
                if quant:
                    p[1] = True
            if alt and quant:
                r_alt_quant = True
                return False  # R2
            amb = _branches_overlap(branches)  # 内部歧义：分支字符集重叠
            prev_amb = _get_last_amb()  # 当前层级上一个原子的歧义
            if amb and prev_amb:
                r_amb_group = True
                return False  # R3b
            _set_cur_group(gset, amb)
            i += 1
            continue

        # ── 量化词 ──
        if c in ("+", "*", "?"):
            _apply_quantifier()
            if i + 1 < n and pattern[i + 1] == "?":
                i += 1  # 惰性后缀
            i += 1
            continue
        if c == "{":
            end = _brace_quant_end(pattern, i)
            if end is not None:
                _apply_quantifier()
                i = end
                if i < n and pattern[i] == "?":
                    i += 1
                continue
            _complete_atom(frozenset(("{",)))  # 字面 {
            i += 1
            continue

        # ── 交替 ──
        if c == "|":
            _commit()
            if stack:
                g = stack[-1]
                g[0] = True
                g[3].append(g[4])
                g[4] = frozenset()
            _set_last_amb(False)  # 分支边界：不构成相邻
            i += 1
            continue

        # ── 零宽断言 ──
        if c in ("^", "$"):
            _commit()
            i += 1
            continue

        # ── 字面量字符 ──
        _complete_atom(frozenset((c,)))
        i += 1
        continue

    # ── 扫描结束，综合判定 ──
    if r_quant_group or r_alt_quant or r_amb_group or r_lookbackref:
        return False
    if not q_infos:
        return True
    # 每个量化词：终位，或（非首消费元 + 前驱字符集不交）；非终位至多一个
    non_terminal = 0
    for q_atom, q_first, q_prev, q_set in q_infos:
        if q_atom == atom_idx:
            continue  # 终位量化词：贪心匹配成功即结束，线性
        non_terminal += 1
        if non_terminal > 1:
            return False
        if q_first or _overlap(q_prev, q_set):
            return False
    return True


def safe_regex_search(
    pattern: re.Pattern,
    text: str,
    timeout: float = 2.0,
    pos: int = 0,
) -> bool:
    """执行正则搜索（timeout 参数保留兼容调用方，语义上不再需要）

    安全模式（_check_regex_complexity 通过）在调用线程直接 pattern.search；
    风险模式（含量化词等可能灾难性回溯的结构）不执行正则——CPython 中正则
    搜索持 GIL 不放，线程池限时无法中断（实测验证），一律降级为 O(n) 线性
    子串匹配（匹配的是模式字面量，等价于旧设计的子串降级语义）。

    Args:
        pattern: 预编译正则。
        text:    待搜索文本。
        timeout: 已废弃（保留以兼容既有调用），不再使用。
        pos:     搜索起始偏移（匹配必须从 pos 起；旧文本已搜索时可跳过）。
    """
    if _check_regex_complexity(pattern.pattern):
        try:
            return pattern.search(text, pos) is not None
        except re.error:
            return False
    return text.find(pattern.pattern, pos) >= 0


class TriggerMatcher:
    """触发条件匹配器

    管理一组触发条件（正则/子串匹配 + 换行策略 + 新鲜模式 + 静默超时）。
    不直接持有 IO 资源，通过回调与 OutputBuffer 协作。
    """

    def __init__(self, decode_func: Callable[[bytes], tuple]):
        """
        Args:
            decode_func: 解码回调，接收 bytes 返回 (文本, 被消费的字节长度)。
                         通常为 Session._decode_only_len（EncodingDetector.decode_only_len）。
        """
        self._decode_func = decode_func

        self._state_lock = threading.Lock()

        self._pattern: Optional[str] = None
        self._regex: Optional[re.Pattern] = None  # 预编译正则
        self._matched = False
        self._event = threading.Event()
        self._start_offset = 0
        # 第二缓冲（子进程模式 stderr）：独立扫描起始偏移与新鲜周期，
        # 保证 stdout/stderr 双流都按各自"当前末尾"起算等待窗口
        self._err_buffer: Optional[OutputBuffer] = None
        self._err_start_offset = 0
        self._fresh_cycle_err = 0
        self._on_newline = False
        self._newline_count = 0
        self._newline_first_ok = False
        self._fresh = False
        self._fresh_cycle = 0

        # 滚动解码缓存（check 持锁路径使用）：
        # 等待窗口 [start_offset, start_offset+MAX_TRIGGER_SCAN) 的已解码文本，
        # 每块只增量解码新增字节。缓冲裁剪（trim_gen 变化）或切换缓冲
        # （out/err 双流）时重建；set/clear 通过 _scan_version 使缓存失效。
        # 跨块拆分的多字节字符：解码回调返回被消费的字节长度，被丢弃的
        # 残缺尾部（≤3 字节）留待与下块合并解码补全，无需字节对齐假设。
        self._scan_buf: Optional[object] = None
        self._scan_gen = -1
        self._scan_end = 0
        self._scan_text = ""
        self._scan_tail = b""  # 上一块解码被丢弃的残缺尾部字节（待补全）
        self._scan_version = 0

        # 输出静默超时触发条件
        self._idle_timeout: Optional[float] = None
        self._idle_after_first = False
        self._idle_last_activity = 0.0
        self._idle_had_output = False

        # ── 公开接口 ──

    def set(
        self,
        pattern: str,
        newline: bool = False,
        fresh: bool = False,
        start_offset: Optional[int] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
        buffer_length: int = 0,
        err_buffer=None,
        err_buffer_length: Optional[int] = None,
    ):
        """设置触发条件

        Args:
            pattern:              正则表达式模式。
            newline:              仅在换行后才检查触发条件。
            fresh:                新鲜模式 — 跳过即时匹配等待新数据。
            start_offset:         扫描起始偏移。None 表示从末尾开始。
            idle_timeout:         输出静默超时秒数。
            idle_after_first_output: 是否在首次输出后才开始检测。
            buffer_length:        当前缓冲区长度（用于计算 start_offset）。
        """
        with self._state_lock:
            self._pattern = pattern
            try:
                self._regex = re.compile(pattern, re.MULTILINE)
                if not _check_regex_complexity(pattern):
                    _logger.debug(
                        "TriggerMatcher.set: pattern=%r 存在回溯风险，"
                        "将降级为子串匹配（匹配模式字面量）",
                        pattern[:200],
                    )
            except re.error:
                self._regex = None
            self._matched = False
            self._event.clear()
            self._start_offset = (
                start_offset if start_offset is not None else buffer_length
            )
            # 第二缓冲（子进程 stderr）：start_offset 语义与主缓冲一致
            self._err_buffer = err_buffer
            self._err_start_offset = (
                start_offset
                if (start_offset is not None and err_buffer is not None)
                else (err_buffer_length if err_buffer_length is not None else 0)
            )
            self._on_newline = newline

            # 等待窗口起始变化，滚动解码缓存失效
            self._reset_scan_cache_locked()

            self._idle_timeout = idle_timeout
            self._idle_after_first = idle_after_first_output
            now = time.monotonic()
            if idle_timeout is not None:
                if idle_after_first_output:
                    self._idle_had_output = False
                    self._idle_last_activity = now
                else:
                    self._idle_had_output = True
                    self._idle_last_activity = now

        _logger.info(
            "TriggerMatcher.set: pattern=%r newline=%s fresh=%s "
            "offset=%d idle_timeout=%s idle_after_first=%s",
            pattern,
            newline,
            fresh,
            self._start_offset,
            idle_timeout,
            idle_after_first_output,
        )

        if fresh:
            self._fresh = True
            self._fresh_cycle = 0  # 由调用者设置实际值
            # 换行状态与普通路径同步重置，避免 clear() 后残留旧值
            self._newline_first_ok = newline
            self._newline_count = 0
            return

        self._newline_first_ok = newline
        self._newline_count = 0  # 由调用者在持锁后更新

    def on_data_appended(self, now_monotonic: float):
        """通知有新数据追加（更新静默超时计时）

        Args:
            now_monotonic: time.monotonic() 当前值。
        """
        if self._idle_timeout is not None:
            self._idle_last_activity = now_monotonic
            if not self._idle_had_output:
                self._idle_had_output = True
                _logger.debug("静默超时检测: 首次输出到达, 开始计时")

    def check(self, output_buffer) -> bool:
        """检查触发条件是否匹配（**需在持锁状态下调用**）

        需在 OutputBuffer.lock 已获取的线程上下文中调用。
        内部通过快照读取 _state_lock 保护的状态字段，避免与 set/clear 竞争。

        性能：等待窗口内的解码文本跨 check 复用（滚动缓存），每块只
        增量解码新增字节；搜索只覆盖新增文本 + 尾部重叠，避免整窗重扫。

        Args:
            output_buffer: OutputBuffer 实例（持锁状态下）。

        Returns:
            True 表示匹配成功并设置了 _event。
        """
        with self._state_lock:
            pattern = self._pattern
            regex = self._regex
            matched = self._matched
            is_second = (
                self._err_buffer is not None and output_buffer is self._err_buffer
            )
            start_offset = self._err_start_offset if is_second else self._start_offset
            on_newline = self._on_newline
            fresh = self._fresh
            fresh_cycle = self._fresh_cycle_err if is_second else self._fresh_cycle
            scan_buf = self._scan_buf
            scan_gen = self._scan_gen
            scan_end = self._scan_end
            scan_text = self._scan_text
            scan_tail = self._scan_tail
            scan_version = self._scan_version

        if not pattern or matched:
            return False

        if fresh:
            if output_buffer.read_cycle <= fresh_cycle:
                return False
            with self._state_lock:
                self._fresh = False

        if on_newline:
            cur = output_buffer.raw.count(b"\n")
            with self._state_lock:
                if cur > self._newline_count:
                    self._newline_count = cur
                elif self._newline_first_ok:
                    self._newline_first_ok = False
                else:
                    return False

        raw = output_buffer.raw
        start = min(start_offset, len(raw))
        end = min(start + MAX_TRIGGER_SCAN, len(raw))

        # ── 滚动解码缓存：仅增量解码新增字节 ──
        # 缓冲被头部裁剪（trim_gen 变化）或切换缓冲（子进程模式 out/err 双流）
        # 时缓存失效重建；首次 check 从等待窗口起点整段解码一次。
        # 上一块解码丢弃的残缺尾部与新增字节合并解码（跨块拆分的多字节字符
        # 在此补全）；新增字节整体残缺时尾部继续累积，封顶防止异常流膨胀。
        prev_len = len(scan_text)
        if scan_buf is not output_buffer or scan_gen != output_buffer.trim_gen:
            scan_buf = output_buffer
            scan_gen = output_buffer.trim_gen
            scan_end = start
            scan_text = ""
            scan_tail = b""
            prev_len = 0
        if end > scan_end:
            new_bytes = bytes(memoryview(raw)[scan_end:end])
            joined = scan_tail + new_bytes
            joined_text, joined_len = self._decode_func(joined)
            if joined_text:
                scan_text += joined_text
            # 未消费的尾部字节：残缺多字节序列，留待下块补全（封顶防膨胀）
            scan_tail = joined[joined_len:]
            if len(scan_tail) > _MAX_TAIL_BYTES:
                scan_tail = scan_tail[-_MAX_TAIL_BYTES:]
            scan_end = end
            # 裁剪 scan_text 头部：仅保留尾部重叠区 + 本块解码文本。
            # 旧文本在之前 check 已全量搜索无命中，重叠区仅供跨块模式命中，
            # 避免窗口（MAX_TRIGGER_SCAN 1MB）内每块 O(窗口) 字符串追加拷贝。
            keep = _SCAN_TAIL_OVERLAP + len(joined_text)
            if len(scan_text) > keep:
                scan_text = scan_text[-keep:]
                # prev_len 同步为裁剪后"旧文本"长度，pos 计算仍覆盖
                # 尾部重叠区 + 新增文本
                prev_len = len(scan_text) - len(joined_text)

        if regex:
            # 新增文本 + 尾部重叠区；旧文本在之前 check 已全量搜索无命中
            pos = max(0, prev_len - _SCAN_TAIL_OVERLAP)
            if safe_regex_search(regex, scan_text, pos=pos):
                _logger.info("TriggerMatcher.check: MATCHED pattern=%r", pattern)
                with self._state_lock:
                    self._matched = True
                self._event.set()
                self._commit_scan_cache(
                    scan_buf, scan_gen, scan_end, scan_text, scan_tail, scan_version,
                )
                return True
        else:
            pos = max(0, prev_len - (len(pattern) - 1))
            if pattern in scan_text[pos:]:
                _logger.info(
                    "TriggerMatcher.check: substring MATCHED pattern=%r", pattern
                )
                with self._state_lock:
                    self._matched = True
                self._event.set()
                self._commit_scan_cache(
                    scan_buf, scan_gen, scan_end, scan_text, scan_tail, scan_version,
                )
                return True
        self._commit_scan_cache(
            scan_buf, scan_gen, scan_end, scan_text, scan_tail, scan_version,
        )
        return False

    def _commit_scan_cache(
        self, scan_buf, scan_gen, scan_end, scan_text, scan_tail, scan_version
    ):
        """提交滚动解码缓存（仅在 set/clear 未并发失效时写入）"""
        with self._state_lock:
            if self._scan_version == scan_version:
                self._scan_buf = scan_buf
                self._scan_gen = scan_gen
                self._scan_end = scan_end
                self._scan_text = scan_text
                self._scan_tail = scan_tail

    def _reset_scan_cache_locked(self):
        """清空滚动解码缓存（须持有 _state_lock）"""
        self._scan_buf = None
        self._scan_gen = -1
        self._scan_end = 0
        self._scan_text = ""
        self._scan_tail = b""
        self._scan_version += 1

    def check_idle_timeout(self) -> bool:
        """检查输出静默是否超时

        Returns:
            True 表示已超时。
        """
        if self._idle_timeout is None:
            return False
        if not self._idle_had_output and self._idle_after_first:
            return False
        elapsed = time.monotonic() - self._idle_last_activity
        return elapsed >= self._idle_timeout

    def clear(self):
        """清除所有触发条件"""
        with self._state_lock:
            _logger.info(
                "TriggerMatcher.clear: pattern=%r matched=%s",
                self._pattern,
                self._matched,
            )
            self._pattern = None
            self._regex = None
            self._matched = False
            self._fresh = False
            self._err_buffer = None
            self._err_start_offset = 0
            self._fresh_cycle_err = 0
            self._idle_timeout = None
            self._idle_after_first = False
            self._idle_had_output = False
            self._idle_last_activity = 0.0
            # 换行计数状态一并清除，避免残留旧值影响下一次 set
            self._newline_first_ok = False
            self._newline_count = 0
            self._reset_scan_cache_locked()
        self._event.clear()

    def set_snapshot_trigger(
        self,
        pattern: Optional[str] = None,
        idle_timeout: Optional[float] = None,
        idle_after_first_output: bool = False,
    ):
        """设置快照模式触发条件

        Args:
            pattern:              正则表达式模式（匹配快照文本）。
            idle_timeout:         快照静默超时（秒）。
            idle_after_first_output: 是否在首次快照变化后才开始检测静默超时。

        ``--newline`` 换行语义由调用方（``_run_snapshot_flow``）按
        "换行计数增量后才检查"（对齐流式模式）在传入 check_snapshot 前处理。
        """
        with self._state_lock:
            if pattern is not None:
                self._pattern = pattern
                try:
                    self._regex = re.compile(pattern, re.MULTILINE)
                    if not _check_regex_complexity(pattern):
                        _logger.debug(
                            "set_snapshot_trigger: pattern=%r 存在回溯风险，"
                            "将降级为子串匹配（匹配模式字面量）",
                            pattern[:200],
                        )
                except re.error:
                    self._regex = None
            self._matched = False
            self._event.clear()
            self._idle_timeout = idle_timeout
            self._idle_after_first = idle_after_first_output
            self._idle_had_output = False
            self._idle_last_activity = time.monotonic()
            self._reset_scan_cache_locked()

    def check_snapshot(self, text: str) -> bool:
        """对快照文本直接匹配（不依赖 OutputBuffer）

        Args:
            text: 当前终端屏幕快照文本。

        Returns:
            True 表示匹配成功。
        """
        with self._state_lock:
            pattern = self._pattern
            regex = self._regex

        if not pattern:
            return False

        if regex:
            if safe_regex_search(regex, text):
                with self._state_lock:
                    self._matched = True
                self._event.set()
                return True
        else:
            if pattern in text:
                with self._state_lock:
                    self._matched = True
                self._event.set()
                return True
        return False

    def notify_snapshot_changed(self, now_monotonic: float):
        """通知快照内容发生变化（更新静默超时计时）"""
        if self._idle_timeout is not None:
            self._idle_last_activity = now_monotonic
            if not self._idle_had_output:
                self._idle_had_output = True
                _logger.debug("快照静默超时: 首次变化到达, 开始计时")

    # ── 属性 ──

    @property
    def has_pattern(self) -> bool:
        return self._pattern is not None

    @property
    def matched(self) -> bool:
        return self._matched

    @property
    def event(self) -> threading.Event:
        return self._event

    @property
    def pattern(self) -> Optional[str]:
        return self._pattern

    @property
    def idle_timeout(self) -> Optional[float]:
        return self._idle_timeout

    @property
    def newline_count(self) -> int:
        return self._newline_count

    @newline_count.setter
    def newline_count(self, value: int):
        self._newline_count = value

    @property
    def fresh_cycle(self) -> int:
        return self._fresh_cycle

    @fresh_cycle.setter
    def fresh_cycle(self, value: int):
        self._fresh_cycle = value
