"""client/renderer 包 SVG 渲染与压缩单元测试"""

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
        # 行高 17 与 GDI 渲染实测（Consolas 14px tmHeight）保持一致
        assert 'height="170"' in svg

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

    def test_palette_index_color(self):
        cell = {"d": "R", "f": "p1", "b": "default", "bo": False}
        lines = [[cell]]
        buf = _make_buf(cols=1, rows=1, lines=lines)
        svg = render_svg_string(buf)
        assert 'fill="#cd0000"' in svg

    def test_truecolor_hash_color(self):
        cell = {"d": "T", "f": "#ff8800", "b": "default", "bo": False}
        lines = [[cell]]
        buf = _make_buf(cols=1, rows=1, lines=lines)
        svg = render_svg_string(buf)
        assert 'fill="#ff8800"' in svg

    def test_background_rect(self):
        cell = {"d": "G", "f": "p2", "b": "p4", "bo": False}
        lines = [[cell]]
        buf = _make_buf(cols=1, rows=1, lines=lines)
        svg = render_svg_string(buf)
        assert '<rect x="0" y="0" width="8" height="17" fill="#0000ee"/>' in svg


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

    def test_level1_differ_from_level2(self):
        try:
            from scour import scour
        except ImportError:
            pytest.skip("scour 未安装")
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="17">' \
              '<defs><linearGradient id="g1"><stop offset="0" stop-color="#ff0000"/></linearGradient></defs>' \
              '<rect width="100%" height="100%" fill="url(#g1)"/>' \
              '<text x="0" y="0">Hello World</text></svg>'
        l1 = _compress_svg(svg, 1)
        l2 = _compress_svg(svg, 2)
        assert l1 != l2
        assert len(_compress_svg(svg, 2)) <= len(_compress_svg(svg, 1))

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
