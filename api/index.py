"""Vercel entry point: every URL is routed here (see vercel.json) and handled by app.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


class handler(app.Handler):  # Vercel looks for a class named "handler"
    pass
