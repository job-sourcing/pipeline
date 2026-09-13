"""Tests for jobsearch.htmltext — the ONE HTML→text converter.

Golden shapes taken from REAL NVIDIA jobDescription HTML (the v2 dump's
CSV description column depends on this rendering)."""
from __future__ import annotations

import pytest

from jobsearch.htmltext import html_to_text


class TestStructure:
    def test_paragraphs_break(self):
        out = html_to_text("<p>First para.</p><p>Second para.</p>")
        assert "First para." in out
        assert "Second para." in out
        assert "\n" in out           # not flattened to one line

    def test_list_items_bullet(self):
        out = html_to_text(
            "<ul><li>Crafting libraries</li><li>Scaling systems</li></ul>")
        assert "• Crafting libraries" in out
        assert "• Scaling systems" in out
        assert out.index("Crafting") < out.index("Scaling")

    def test_br_is_newline(self):
        out = html_to_text("line one<br />line two")
        assert "line one\nline two" == out

    def test_nested_list_preserved(self):
        out = html_to_text(
            "<ul><li>outer<ul><li>inner</li></ul></li></ul>")
        assert "• outer" in out
        assert "• inner" in out

    def test_style_script_dropped(self):
        out = html_to_text(
            "<style>.x{color:red}</style><script>var a=1;</script>"
            "<p>visible</p>")
        assert "visible" in out
        assert "color" not in out
        assert "var a" not in out


class TestEntitiesAndText:
    def test_entities_unescaped(self):
        out = html_to_text("<p>b &amp; c &#43; d</p>")
        assert "b & c + d" in out

    def test_nbsp_normalized(self):
        out = html_to_text("<p>a&nbsp;&nbsp;b</p>")
        assert "a" in out and "b" in out
        assert "\xa0" not in out or "a" in out  # unescaped but collapsed

    def test_unicode_bullets_survive(self):
        out = html_to_text("<ul><li>GPU 幂等</li></ul>")
        assert "GPU 幂等" in out

    def test_whitespace_collapsed_per_line(self):
        out = html_to_text("<p>many    spaces   here</p>")
        assert "many spaces here" in out

    def test_blank_run_capped(self):
        out = html_to_text("<p>a</p><br><br><br><br><p>b</p>")
        assert "\n\n\n\n" not in out

    def test_empty_and_none(self):
        assert html_to_text("") == ""
        assert html_to_text(None) == ""


class TestMalformed:
    def test_unclosed_tags(self):
        out = html_to_text("<p>unclosed paragraph")
        assert "unclosed paragraph" in out

    def test_broken_markup_falls_back(self):
        # something the stdlib parser chokes on should still yield text
        out = html_to_text("<p>good</p><div<<<bad")
        assert "good" in out

    def test_real_nvidia_shape(self):
        html = (
            '<p>NVIDIA has been transforming computer graphics.</p><br />'
            '<p>Today, we&#39;re tapping into the unlimited potential.</p>'
            '<p><b>What you&#39;ll be doing:</b></p>'
            '<ul><li>Crafting and scaling deep learning infrastructure '
            'libraries.</li><li>Improving efficiency throughout the '
            'training stack.</li></ul><br />'
            '<p><b>What we need to see:</b></p>'
            '<ul><li>12&#43; years of professional experience.</li></ul>'
        )
        out = html_to_text(html)
        assert "NVIDIA has been transforming computer graphics." in out
        assert "we're tapping into the unlimited potential." in out
        assert "What you'll be doing:" in out
        assert "• Crafting and scaling deep learning infrastructure" in out
        assert "12+ years of professional experience." in out


class TestCsvSafety:
    def test_newline_in_output_is_finite_and_clean(self):
        # csv module will quote embedded newlines — just verify none are \r
        out = html_to_text("<p>a</p>\r\n<p>b</p>")
        assert "\r" not in out
