"""应用层：把核心逻辑、输入与渲染组合成可运行的主循环（含奖杯拖拽成就）。

支持简单模式（argv 含 "simple"）：呈现层只输出纯棋盘文本，
主循环跳过奖杯阶段；其余行为与普通模式完全一致。
"""

from __future__ import annotations

import sys
import time
from typing import Sequence

from . import controls, debuglog, highscores, logic, savegame, sprites, ui

# 2048 变身动画参数
ANIM_DURATION = 0.8   # 秒
ANIM_FPS = 12.0

# 简单模式开关：命令行参数中出现即启用（python main.py simple）
SIMPLE_ARG = "simple"
# 新局开关：启动前清空存档（python main.py --new）
NEW_ARG = "--new"


def main(argv: Sequence[str] = None) -> None:
    """程序入口（供 console 脚本与 __main__ 调用）。

    argv 含 SIMPLE_ARG 时进入简单模式：呈现层只输出棋盘纯文本（空位 '-'），
    主循环跳过奖杯阶段；控制台行为、存档、高分、win_value 等与普通模式
    完全一致，仅呈现层与奖杯阶段不同。
    argv 含 NEW_ARG 时启动前清空存档，强制新开局。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if NEW_ARG in argv:
        savegame.clear()  # 新局：启动前清空存档
    ui.set_simple(SIMPLE_ARG in argv)
    ui.init()
    try:
        _run()
    except KeyboardInterrupt:
        pass
    finally:
        ui.cleanup()
    # 退出标记：替代屏幕已恢复，输出 "quit" 供 exec trigger 匹配返回
    # （须在 cleanup 之后打印，替代屏幕销毁前的内容不进主屏幕快照）
    print("quit")


def _run() -> None:
    highscores.ensure()  # 配置文件不存在时自动创建（含默认胜利条件）
    best = highscores.best()
    win_value = highscores.win_value()
    # 启动恢复：.game2048 有存档（base64）则续玩，否则新局
    state = savegame.load()
    if state is not None:
        game = logic.Game.from_state(state[0], state[1], win_value=win_value)
    else:
        game = logic.Game(win_value=win_value)
    while True:
        if game.score > best:
            best = game.score
            highscores.save(best)
        ui.render(game, game.status(), best)
        key = controls.read_key()

        if not isinstance(key, str):
            continue  # 鼠标事件在普通阶段忽略

        if key == "quit":
            return
        if key == "restart":
            savegame.clear()
            game = logic.Game(win_value=win_value)
            continue
        if key in logic.DIRECTIONS:
            if game.move(key):
                savegame.save(game.board, game.score)

        if game.just_win and not ui.simple():
            action = _trophy_phase(game, best)
            if action == "quit":
                return
            if action == "restart":
                savegame.clear()
                game = logic.Game(win_value=win_value)
                continue
            # 达成成就：短暂展示
            if action == "achieved":
                _flash_achievement(game, best)
            continue

        if game.status() == logic.LOST:
            # 终局：清除存档，等待玩家决定重开或退出
            savegame.clear()
            while True:
                ui.render(game, logic.LOST, best)
                key = controls.read_key()
                if isinstance(key, str) and key == "quit":
                    return
                if isinstance(key, str) and key == "restart":
                    game = logic.Game(win_value=win_value)
                    break


def _trophy_phase(game, best) -> str:
    """2048 变金 + 奖杯拖拽到成就柜。

    返回:
        'quit' | 'restart' | 'done' | 'achieved'
    """
    trophy = ui.spawn_trophy(game)
    trophy["spawn"] = (trophy["row"], trophy["col"])
    start = time.monotonic()
    achieved = False
    debuglog.log("trophy-phase: start trophy_rect={} cabinet_rect={} canvas={}".format(
        ui.trophy_rect(trophy), ui.cabinet_rect(game), ui.canvas_size(game)))

    while True:
        elapsed = time.monotonic() - start
        anim = int(elapsed * ANIM_FPS) if elapsed < ANIM_DURATION else None

        if controls.available():
            result = _handle_trophy_event(game, trophy, controls.read_key())
            if result is not None:
                if result == "achieved":
                    achieved = True
                return result
            over = ui.rects_overlap(ui.trophy_rect(trophy), ui.cabinet_rect(game))
            ui.render(game, game.status(), best, trophy, anim, achieved, over)
        else:
            over = ui.rects_overlap(ui.trophy_rect(trophy), ui.cabinet_rect(game))
            ui.render(game, game.status(), best, trophy, anim, achieved, over)
            if anim is not None:
                time.sleep(1.0 / ANIM_FPS)
            else:
                # 动画结束后阻塞等待输入，避免空转
                result = _handle_trophy_event(game, trophy, controls.read_key())
                if result is not None:
                    if result == "achieved":
                        achieved = True
                    return result


def _handle_trophy_event(game, trophy, ev):
    """处理奖杯阶段的一个事件，返回 None（继续）或退出动作。"""
    if ev is None:
        return None  # 无效输入（孤立 ESC / 未映射按键）忽略
    debuglog.log("trophy: event {!r}".format(ev))

    if isinstance(ev, str):
        if ev == "quit":
            return "quit"
        if ev == "restart":
            return "restart"
        if ev == "c":  # 放弃奖杯，继续游戏
            return "done"
        return None

    action = ev.get("action")
    col, row = ev.get("x", 0), ev.get("y", 0)
    row, col = row - 1, col - 1  # 转 0 基画布坐标

    if action == "press" and ev.get("button") == 0:
        tr = ui.trophy_rect(trophy)
        hit = ui.point_in_rect(row, col, tr)
        debuglog.log("trophy: press at 0-base({},{}) hit={} trophy_rect={}".format(
            row, col, hit, tr))
        if hit:
            trophy["held"] = True
            trophy["grab_r"] = trophy["row"] - row
            trophy["grab_c"] = trophy["col"] - col
        return None

    if action == "move" and trophy.get("held"):
        # 画布宽度与渲染一致（提示文案可能加宽画布）
        rows, cols = ui.canvas_size(game, hint=ui._HINT_TROPHY)
        trophy["row"] = max(0, min(row + trophy["grab_r"],
                                   rows - sprites.TROPHY_H))
        trophy["col"] = max(0, min(col + trophy["grab_c"],
                                   cols - sprites.TROPHY_W))
        return None

    if action == "release" and trophy.get("held"):
        trophy["held"] = False
        over = ui.rects_overlap(ui.trophy_rect(trophy), ui.cabinet_rect(game))
        debuglog.log("trophy: release overlap={} trophy_rect={} cabinet_rect={}".format(
            over, ui.trophy_rect(trophy), ui.cabinet_rect(game)))
        if over:
            return "achieved"
        # 未放进柜：回弹到初始位置，不遮挡画面
        trophy["row"], trophy["col"] = trophy.get("spawn", (trophy["row"], trophy["col"]))
        return None

    return None


def _flash_achievement(game, best) -> None:
    """成就解锁后的短暂展示（纯定时渲染，随后返回正常对局）。"""
    t0 = time.monotonic()
    while time.monotonic() - t0 < 1.6:
        ui.render(game, game.status(), best, None, None, True)
        time.sleep(0.05)
