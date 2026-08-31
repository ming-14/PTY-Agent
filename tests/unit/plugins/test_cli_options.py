"""插件自定义 CLI 选项（cliOptions）单测 — 清单校验、冲突检测、注册、收集、消息校验"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.plugins.manifest import load_manifest  # noqa: E402
from src.plugins.cli_options import (  # noqa: E402
    CLI_OPTION_COMMANDS,
    RESERVED_OPTIONS,
    build_option_registrations,
    check_cli_option_conflicts,
    collect_option_values,
    option_strings,
    validate_plugin_options,
)
from src.plugins.registry import PluginRegistry  # noqa: E402
from src.client.cli_plugins import CliPluginHost  # noqa: E402
from tests.helpers import write_plugin_dir  # noqa: E402


PLUGIN_SRC = (
    "from src.plugins.base import Plugin\n"
    "class P(Plugin):\n"
    "    pass\n"
    "plugin = P()\n"
)

VALID_OPTIONS = [
    {"name": "pa", "short": "p", "type": "str", "default": None,
     "help": "param", "commands": ["exec", "send"]},
    {"name": "num", "type": "int", "default": 3},
    {"name": "any", "type": "flag"},
    {"name": "mode", "type": "choice", "choices": ["a", "b"], "default": "a"},
]


def _load(tmp_path, options, kind="session", extra=None, pid="demo"):
    manifest_extra = {"cliOptions": options}
    if extra:
        manifest_extra.update(extra)
    pdir = write_plugin_dir(tmp_path, pid, kind, PLUGIN_SRC,
                            manifest_extra=manifest_extra)
    return load_manifest(pdir)


# ── 清单结构校验 ─────────────────────────────────────────

class TestManifestValidation:
    def test_valid_options_parsed(self, tmp_path):
        m = _load(tmp_path, VALID_OPTIONS)
        assert m is not None
        opts = m.cli_options
        assert [o.name for o in opts] == ["pa", "num", "any", "mode"]
        assert opts[0].short == "p" and opts[0].type == "str"
        assert opts[0].commands == ["exec", "send"]
        assert opts[1].type == "int" and opts[1].default == 3
        assert opts[2].type == "flag"
        assert opts[3].choices == ["a", "b"] and opts[3].default == "a"

    def test_empty_commands_means_all(self, tmp_path):
        m = _load(tmp_path, [{"name": "pa"}])
        assert m.cli_options[0].commands == []

    @pytest.mark.parametrize("name", ["", "PA", "pa_b", "pa.b", "-pa"])
    def test_invalid_name(self, tmp_path, name):
        assert _load(tmp_path, [{"name": name}], pid="p" + str(abs(hash(name)))) is None

    def test_valid_names(self, tmp_path):
        # 连字符/数字允许（含尾部连字符与数字开头）
        m = _load(tmp_path, [
            {"name": "pa-b-"}, {"name": "1pa"}, {"name": "a1"},
        ], pid="valid")
        assert m is not None
        assert [o.name for o in m.cli_options] == ["pa-b-", "1pa", "a1"]

    def test_invalid_short(self, tmp_path):
        assert _load(tmp_path, [{"name": "pa", "short": "pp"}], pid="s1") is None
        assert _load(tmp_path, [{"name": "pa", "short": "-"}], pid="s2") is None

    def test_invalid_type(self, tmp_path):
        assert _load(tmp_path, [{"name": "pa", "type": "bool"}]) is None

    def test_choice_requires_choices(self, tmp_path):
        assert _load(tmp_path, [{"name": "pa", "type": "choice"}], pid="c1") is None
        assert _load(tmp_path, [{"name": "pa", "type": "choice", "choices": []}], pid="c2") is None
        assert _load(tmp_path, [{"name": "pa", "type": "choice", "choices": [1]}], pid="c3") is None

    def test_choices_only_for_choice(self, tmp_path):
        assert _load(tmp_path, [{"name": "pa", "choices": ["a"]}]) is None

    def test_default_type_mismatch(self, tmp_path):
        assert _load(tmp_path, [{"name": "pa", "type": "int", "default": "x"}], pid="d1") is None
        assert _load(tmp_path, [{"name": "pa", "type": "flag", "default": "yes"}], pid="d2") is None
        assert _load(tmp_path, [{"name": "pa", "type": "choice",
                                 "choices": ["a"], "default": "z"}], pid="d3") is None
        # int 默认值拒绝 bool（bool 是 int 子类）
        assert _load(tmp_path, [{"name": "pa", "type": "int", "default": True}], pid="d4") is None

    def test_duplicate_name_or_short(self, tmp_path):
        assert _load(tmp_path, [
            {"name": "pa"}, {"name": "pa"},
        ], pid="dup1") is None
        assert _load(tmp_path, [
            {"name": "pa", "short": "p"}, {"name": "pb", "short": "p"},
        ], pid="dup2") is None

    def test_unknown_command(self, tmp_path):
        assert _load(tmp_path, [{"name": "pa", "commands": ["file"]}]) is None

    def test_duplicate_commands_rejected(self, tmp_path):
        assert _load(tmp_path, [{"name": "pa", "commands": ["exec", "exec"]}]) is None

    def test_name_length_limit(self, tmp_path):
        assert _load(tmp_path, [{"name": "x" * 65}], pid="long") is None
        assert _load(tmp_path, [{"name": "x" * 64}], pid="ok64") is not None

    def test_plugin_id_length_limit(self, tmp_path):
        pdir = write_plugin_dir(
            tmp_path, "p" * 65, "session", PLUGIN_SRC,
            manifest_extra={"cliOptions": [{"name": "pa"}]},
        )
        assert load_manifest(pdir) is None

    def test_process_kind_rejected(self, tmp_path):
        assert _load(tmp_path, VALID_OPTIONS, kind="process",
                     extra={"messageTypes": ["file_read"]}) is None

    def test_cli_kind_allowed(self, tmp_path):
        assert _load(tmp_path, VALID_OPTIONS, kind="cli") is not None


# ── 冲突检测 ─────────────────────────────────────────────

class TestConflictDetection:
    def _manifests(self, tmp_path, specs):
        """specs: [(id, kind, options)] → manifests"""
        out = []
        for pid, kind, options in specs:
            pdir = write_plugin_dir(
                tmp_path, pid, kind, PLUGIN_SRC,
                manifest_extra={"cliOptions": options} if options else {},
            )
            m = load_manifest(pdir)
            assert m is not None, pid
            out.append(m)
        return out

    def test_builtin_long_conflict(self, tmp_path):
        ms = self._manifests(tmp_path, [
            ("bad", "session", [{"name": "timeout", "commands": ["exec"]}]),
        ])
        conf = check_cli_option_conflicts(ms)
        assert "bad" in conf and "--timeout" in conf["bad"]

    def test_builtin_short_conflict(self, tmp_path):
        ms = self._manifests(tmp_path, [
            ("bad", "session", [{"name": "pa", "short": "t", "commands": ["send"]}]),
        ])
        conf = check_cli_option_conflicts(ms)
        assert "bad" in conf and "-t" in conf["bad"]

    def test_cross_plugin_long_conflict_both(self, tmp_path):
        ms = self._manifests(tmp_path, [
            ("a", "session", [{"name": "pa", "commands": ["exec"]}]),
            ("b", "session", [{"name": "pa", "commands": ["exec"]}]),
        ])
        conf = check_cli_option_conflicts(ms)
        assert conf["a"] and "b" in conf["a"]
        assert conf["b"] and "a" in conf["b"]

    def test_cross_plugin_short_conflict(self, tmp_path):
        ms = self._manifests(tmp_path, [
            ("a", "session", [{"name": "pa", "short": "p", "commands": ["exec"]}]),
            ("b", "session", [{"name": "pb", "short": "p", "commands": ["exec"]}]),
        ])
        conf = check_cli_option_conflicts(ms)
        assert "a" in conf and "b" in conf

    def test_command_scope_isolation(self, tmp_path):
        ms = self._manifests(tmp_path, [
            ("a", "session", [{"name": "pa", "commands": ["exec"]}]),
            ("b", "session", [{"name": "pa", "commands": ["send"]}]),
        ])
        assert check_cli_option_conflicts(ms) == {}

    def test_long_and_short_of_same_option_not_self_conflict(self, tmp_path):
        ms = self._manifests(tmp_path, [
            ("a", "session", [{"name": "pa", "short": "p", "commands": ["exec"]}]),
        ])
        assert check_cli_option_conflicts(ms) == {}

    def test_symmetric_and_order_independent(self, tmp_path):
        ms1 = self._manifests(tmp_path, [
            ("a", "session", [{"name": "pa", "commands": ["exec"]}]),
            ("b", "session", [{"name": "pa", "commands": ["exec"]}]),
        ])
        ms2 = list(reversed(ms1))
        assert check_cli_option_conflicts(ms1) == check_cli_option_conflicts(ms2)


# ── 保留选项表与真实解析器不变量 ─────────────────────────

class TestReservedOptionsInvariant:
    def test_reserved_matches_real_parsers(self):
        from src.cli.registry import CommandRegistry
        from src.cli.commands import register_all

        registry = CommandRegistry()
        register_all(registry)
        parser = registry.build_parser(prog="t", description="", epilog="")
        sub = parser._subparsers._group_actions[0]
        for cmd in CLI_OPTION_COMMANDS:
            p = sub.choices[cmd]
            actual = set()
            for action in p._actions:
                actual.update(action.option_strings)
            assert RESERVED_OPTIONS[cmd] == actual, cmd


# ── 注册描述与值收集 ─────────────────────────────────────

class TestRegistrations:
    def _host_regs(self, tmp_path, specs):
        dirs = []
        for pid, kind, options in specs:
            pdir = write_plugin_dir(
                tmp_path, pid, kind, PLUGIN_SRC,
                manifest_extra={"cliOptions": options} if options else {},
            )
            dirs.append(pdir)
        host = CliPluginHost(dirs)
        return host, host.option_registrations()

    def test_option_strings(self):
        from src.plugins.manifest import PluginCliOption
        assert option_strings(PluginCliOption(name="pa")) == ["--pa"]
        assert option_strings(PluginCliOption(name="pa", short="p")) == ["--pa", "-p"]

    def test_registration_shape(self, tmp_path):
        _, regs = self._host_regs(tmp_path, [
            ("demo", "session", VALID_OPTIONS),
        ])
        # 未声明 commands 的选项注册到全部会话 IO 命令；pa 限定 exec/send
        assert set(regs) == set(CLI_OPTION_COMMANDS)
        exec_regs = {r.name: r for r in regs["exec"]}
        pa = exec_regs["pa"]
        assert pa.dest == "plugin_demo_pa"
        assert pa.strings == ["--pa", "-p"]
        assert pa.kwargs["default"] is __import__("argparse").SUPPRESS
        assert pa.kwargs["help"] == "param"
        assert exec_regs["num"].kwargs["type"] is int
        assert exec_regs["any"].kwargs["action"] == "store_true"
        assert exec_regs["mode"].kwargs["choices"] == ["a", "b"]
        # 未声明短选项：仅长选项
        assert exec_regs["num"].strings == ["--num"]
        # commands 限定：pa 不出现在 mouse
        mouse_names = {r.name for r in regs["mouse"]}
        assert "pa" not in mouse_names and "num" in mouse_names

    def test_conflicted_plugin_excluded(self, tmp_path):
        host, regs = self._host_regs(tmp_path, [
            ("bad", "session", [{"name": "timeout", "commands": ["exec"]}]),
            ("demo", "session", [{"name": "pa", "commands": ["exec"]}]),
        ])
        names = {r.name for r in regs["exec"]}
        assert names == {"pa"}
        assert host.names() == []

    def test_collect_only_provided(self, tmp_path):
        _, regs = self._host_regs(tmp_path, [
            ("demo", "session", VALID_OPTIONS),
        ])
        from src.cli.registry import CommandRegistry
        from src.cli.commands import register_all
        registry = CommandRegistry()
        register_all(registry)
        parser = registry.build_parser(prog="t", description="", epilog="",
                                       plugin_registrations=regs)

        args = parser.parse_args(["exec", "s", "-c", "x", "--pa", "v", "--num", "5",
                                  "--mode", "b"])
        assert collect_option_values(args, regs) == {
            "demo": {"pa": "v", "num": 5, "mode": "b"},
        }
        args2 = parser.parse_args(["exec", "s", "-c", "x"])
        # 声明 default 的选项（num=3/mode=a）未显式提供时收集到默认值；
        # 未声明 default 的 pa 不产生属性
        assert collect_option_values(args2, regs) == {
            "demo": {"num": 3, "mode": "a"},
        }
        # 短选项同样收集（同时收集 num/mode 声明默认值）
        args3 = parser.parse_args(["exec", "s", "-c", "x", "-p", "short"])
        assert collect_option_values(args3, regs) == {
            "demo": {"pa": "short", "num": 3, "mode": "a"},
        }

    def test_type_validation_by_argparse(self, tmp_path):
        _, regs = self._host_regs(tmp_path, [
            ("demo", "session", [{"name": "pa", "type": "int", "commands": ["exec"]}]),
        ])
        from src.cli.registry import CommandRegistry
        from src.cli.commands import register_all
        registry = CommandRegistry()
        register_all(registry)
        parser = registry.build_parser(prog="t", description="", epilog="",
                                       plugin_registrations=regs)
        with pytest.raises(SystemExit):
            parser.parse_args(["exec", "s", "-c", "x", "--pa", "abc"])
        with pytest.raises(SystemExit):
            parser.parse_args(["exec", "s", "-c", "x", "--pa", "1.5"])


# ── 消息校验 ─────────────────────────────────────────────

class TestValidatePluginOptions:
    def test_valid_shapes(self):
        assert validate_plugin_options({"demo": {"pa": "x"}}) is None
        assert validate_plugin_options(
            {"demo": {"n": 1, "f": 1.5, "b": True, "s": "y"}}) is None
        assert validate_plugin_options({}) is None

    def test_invalid_shapes(self):
        assert validate_plugin_options(None) is not None
        assert validate_plugin_options([]) is not None
        assert validate_plugin_options("x") is not None
        assert validate_plugin_options({"demo": "x"}) is not None
        assert validate_plugin_options({1: {}}) is not None
        assert validate_plugin_options({"": {}}) is not None
        assert validate_plugin_options({"demo": {"pa": [1]}}) is not None
        assert validate_plugin_options({"demo": {"pa": None}}) is not None
        assert validate_plugin_options({"demo": {"pa": {}}}) is not None
        assert validate_plugin_options({"demo": {"": 1}}) is not None

    def test_too_large(self):
        assert validate_plugin_options({"demo": {"pa": "x" * 70000}}) is not None

    def test_plugin_name_too_long(self):
        assert validate_plugin_options({"x" * 65: {"pa": "v"}}) is not None


# ── 注册表冲突集成 ───────────────────────────────────────

class TestRegistryConflict:
    def test_conflicted_plugin_broken(self, tmp_path):
        good = write_plugin_dir(tmp_path, "good", "session", PLUGIN_SRC,
                                manifest_extra={"cliOptions": [
                                    {"name": "pa", "commands": ["exec"]}]})
        bad = write_plugin_dir(tmp_path, "bad", "session", PLUGIN_SRC,
                               manifest_extra={"cliOptions": [
                                   {"name": "timeout", "commands": ["exec"]}]})
        reg = PluginRegistry([good, bad])
        info = reg.info("bad")
        assert info["state"] == "broken"
        assert "CLI 选项冲突" in info["error"]
        assert reg.enable("bad") is False
        assert reg.instantiate("bad") is None
        assert reg.info("good")["state"] == "enabled"
        # cliOptions 出现在 info/list_all
        assert reg.info("good")["cliOptions"][0]["name"] == "pa"
        assert any(p["name"] == "good" and p["cliOptions"] for p in reg.list_all())

    def test_cli_plugin_cross_conflict_broken(self, tmp_path):
        """daemon 插件与 cli 插件选项冲突 → daemon 插件不加载（交叉检测）"""
        sess = write_plugin_dir(tmp_path, "sess", "session", PLUGIN_SRC,
                                manifest_extra={"cliOptions": [
                                    {"name": "pa", "commands": ["exec"]}]})
        cli_src = (
            "from src.plugins.base import Plugin\n"
            "class P(Plugin):\n"
            "    def before_request(self, ctx, msg):\n"
            "        return None\n"
            "plugin = P()\n"
        )
        cli = write_plugin_dir(tmp_path, "clic", "cli", cli_src,
                               manifest_extra={"cliOptions": [
                                   {"name": "pa", "commands": ["exec"]}]})
        reg = PluginRegistry([sess, cli])
        assert reg.info("sess")["state"] == "broken"

    def test_reload_keeps_conflicted_broken(self, tmp_path):
        bad = write_plugin_dir(tmp_path, "bad", "session", PLUGIN_SRC,
                               manifest_extra={"cliOptions": [
                                   {"name": "timeout", "commands": ["exec"]}]})
        reg = PluginRegistry([bad])
        assert reg.reload("bad") is True
        assert reg.info("bad")["state"] == "broken"

    def test_reload_fixed_conflict_recoverable(self, tmp_path):
        """重载后冲突消失：插件可被启用"""
        pdir = write_plugin_dir(tmp_path, "demo", "session", PLUGIN_SRC,
                                manifest_extra={"cliOptions": [
                                    {"name": "timeout", "commands": ["exec"]}]})
        reg = PluginRegistry([pdir])
        assert reg.info("demo")["state"] == "broken"
        # 修复清单（写入新 plugin.json 覆盖）
        import json
        with open(os.path.join(pdir, "plugin.json"), "w", encoding="utf-8") as f:
            json.dump({
                "id": "demo", "version": "1.0", "kind": "session",
                "cliOptions": [{"name": "pa", "commands": ["exec"]}],
            }, f)
        assert reg.reload("demo") is True
        info = reg.info("demo")
        assert info["state"] in ("enabled", "disabled", "loaded")
        assert info["error"] == ""

    def test_conflict_recovery_after_partner_removed(self, tmp_path):
        """冲突一方卸载后，另一方恢复（BROKEN→LOADED/DISABLED）"""
        a = write_plugin_dir(tmp_path, "a", "session", PLUGIN_SRC,
                             manifest_extra={"cliOptions": [
                                 {"name": "pa", "commands": ["exec"]}]})
        b = write_plugin_dir(tmp_path, "b", "session", PLUGIN_SRC,
                             manifest_extra={"cliOptions": [
                                 {"name": "pa", "commands": ["exec"]}]})
        reg = PluginRegistry([a, b])
        assert reg.info("a")["state"] == "broken"
        # 卸载 b
        reg.disable("b")
        reg.remove("b")
        info_a = reg.info("a")
        assert info_a["state"] != "broken"
        assert info_a["error"] == ""
