# -*- coding: utf-8 -*-
"""Tests for agent_control_fabric.capture — offscreen PrintWindow + PNG encoder."""
from __future__ import annotations

import unittest

from agent_control_fabric.capture import _encode_rgb_png


class PngEncoderTests(unittest.TestCase):
    def test_signature_and_structure(self):
        bgra = bytes([
            255, 0, 0, 255,    0, 255, 0, 255,
            0, 0, 255, 255,    255, 255, 255, 255,
        ])
        png = _encode_rgb_png(2, 2, bgra)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", png)
        self.assertIn(b"IDAT", png)
        self.assertTrue(png.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82"))

    def test_single_pixel(self):
        png = _encode_rgb_png(1, 1, bytes([1, 2, 3, 255]))
        self.assertGreater(len(png), 20)


if __name__ == "__main__":
    unittest.main()
