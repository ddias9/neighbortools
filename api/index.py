"""Vercel entry point: every URL is routed here (see vercel.json) and handled by app.py.

Only the name "handler" is exposed, because Vercel treats a module-level name "app"
as a WSGI/ASGI app and would skip the handler class.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import Handler as handler  # noqa: E402,F401
