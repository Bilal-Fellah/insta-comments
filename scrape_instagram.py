#!/usr/bin/env python3
"""Backwards-compatible shim -> scrape.py with platform fixed to instagram."""

from __future__ import annotations

import sys

from scrape import main

if __name__ == "__main__":
    sys.exit(main(default_platform="instagram"))
