"""取源与 offset 策略 — resolve_output / validate_offset_policy

从 handlers/utils.py 拆出的"取哪种原始输出"与 offset 互斥校验。
resolve_output 依赖 conditions.ReturnConditions（条件声明层），不依赖 handlers。
"""

from .conditions import ReturnConditions


def resolve_output(session, cond, force_full: bool = False) -> str:
    """统一的"取哪种原始输出"——根据返回条件选源（snapshot/full/diff）

    force_full: read 路径"指定 --lines 时隐式取全量"的语义（full 或 行数过滤）。
    在各执行/read/workflow 流程共用，取代三处各自的选择分支（P0-A）。
    """
    cond = cond if isinstance(cond, ReturnConditions) else ReturnConditions.from_msg(cond)
    if cond.snapshot_diff:
        return session.get_snapshot_diff(keep_ansi=cond.keep_ansi)
    if cond.full or force_full:
        return session.get_full_snapshot(keep_ansi=cond.keep_ansi)
    return session.get_snapshot(keep_ansi=cond.keep_ansi)


def validate_offset_policy(
    conn,
    offset,
    *,
    lines=None,
    full=False,
    snapshot_diff=False,
    waiting=False,
) -> bool:
    """统一校验 --offset 的互斥策略（read 路径单点归属）

    offset 仅用于"纯增量读取"；与 lines / full / snapshot_diff /
    等待模式（trigger/idle-timeout/timeout）互斥，冲突时发 error 并返回 False。
    """
    from ..protocol.message import Message
    from ..protocol.response import Response

    if offset is None:
        return True
    if lines is not None:
        Message.send(conn, Response.error("--offset cannot be used with --lines/-l"))
        return False
    if full:
        Message.send(conn, Response.error("--offset cannot be used with --full"))
        return False
    if snapshot_diff:
        Message.send(conn, Response.error("--offset cannot be used with --snapshot-diff"))
        return False
    if waiting:
        Message.send(
            conn,
            Response.error(
                "--offset cannot be used with --trigger/--idle-timeout/--timeout (waiting mode)"
            ),
        )
        return False
    return True