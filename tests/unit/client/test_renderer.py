"""client/renderer.py SVG 渲染与压缩单元测试"""

import pytest

from src.client.renderer import render_svg_string, _compress_svg, _expand_lines


def _make_buf(cols=80, rows=24, lines=None):
    if lines is None:
        default_cell = {"d": " ", "f": "default", "b": "default", "bo": False}
        lines = [[dict(default_cell) for _ in range(cols)] for _ in range(rows)]
    return {"cols": cols, "rows": rows, "lines": lines}


class TestRenderSvgString:
    def test_basic_output(self):
        buf = _make_buf()
        svg = render_svg_string(buf)
        assert svg.startswith("<svg")
        assert svg.endswith("</svg>")
        assert 'xmlns="http://www.w3.org/2000/svg"' in svg

    def test_contains_text_element(self):
        default_cell = {"d": " ", "f": "default", "b": "default", "bo": False}
        hello_cell = {"d": "H", "f": "default", "b": "default", "bo": False}
        lines = [[hello_cell] + [dict(default_cell) for _ in range(79)]]
        buf = _make_buf(rows=1, lines=lines)
        svg = render_svg_string(buf)
        assert "H" in svg
        assert "<text" in svg

    def test_dimensions(self):
        buf = _make_buf(cols=40, rows=10)
        svg = render_svg_string(buf)
        assert 'width="320"' in svg
        assert 'height="160"' in svg

    def test_bold_text(self):
        bold_cell = {"d": "X", "f": "default", "b": "default", "bo": True}
        lines = [[bold_cell]]
        buf = _make_buf(cols=1, rows=1, lines=lines)
        svg = render_svg_string(buf)
        assert "font-weight" in svg

    def test_colored_text(self):
        red_cell = {"d": "R", "f": "red", "b": "default", "bo": False}
        lines = [[red_cell]]
        buf = _make_buf(cols=1, rows=1, lines=lines)
        svg = render_svg_string(buf)
        assert "#cd0000" in svg


class TestCompressSvg:
    def test_level_0_removes_empty_text(self):
        svg = '<svg><text x="0" y="0">  </text><text x="0" y="16">Hello</text></svg>'
        result = _compress_svg(svg, 0)
        assert ">Hello<" in result
        assert ">  <" not in result

    def test_level_0_preserves_content(self):
        svg = '<svg><text x="0" y="0">Hello World</text></svg>'
        result = _compress_svg(svg, 0)
        assert "Hello World" in result

    def test_level_1_without_scour(self):
        svg = '<svg><text x="0" y="0">Test</text></svg>'
        try:
            from scour import scour
            result = _compress_svg(svg, 1)
            assert "Test" in result
        except ImportError:
            result = _compress_svg(svg, 1)
            assert "Test" in result

    def test_level_2_without_scour(self):
        svg = '<svg><text x="0" y="0">Test</text></svg>'
        try:
            from scour import scour
            result = _compress_svg(svg, 2)
            assert "Test" in result
        except ImportError:
            result = _compress_svg(svg, 2)
            assert "Test" in result

    def test_negative_level_treated_as_zero(self):
        svg = '<svg><text x="0" y="0">  </text></svg>'
        result = _compress_svg(svg, -1)
        assert ">  <" not in result


class TestExpandLines:
    def test_sparse_format(self):
        sparse = [[{"c": 0, "d": "A", "f": "default", "b": "default", "bo": False}]]
        buf = {"cols": 2, "rows": 1, "lines": sparse}
        expanded = _expand_lines(buf)
        assert len(expanded) == 1
        assert len(expanded[0]) == 2
        assert expanded[0][0]["d"] == "A"
        assert expanded[0][1]["d"] == " "

    def test_full_format(self):
        full = [[{"d": "A", "f": "default", "b": "default", "bo": False},
                 {"d": "B", "f": "default", "b": "default", "bo": False}]]
        buf = {"cols": 2, "rows": 1, "lines": full}
        expanded = _expand_lines(buf)
        assert expanded[0][0]["d"] == "A"
        assert expanded[0][1]["d"] == "B"
