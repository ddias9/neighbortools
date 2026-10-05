"""Vercel entry point: every URL is routed here (see vercel.json) and handled by app.py."""

import os
import sys
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


class handler(app.Handler):  # Vercel looks for a class named "handler"
    def handle_any(self, method):
        # Vercel's rewrite delivers requests as /api/index?__path=/original/path&...
        # Restore the address the visitor actually used before routing.
        url = urlparse(self.path)
        if url.path.rstrip("/") == "/api/index":
            query = parse_qs(url.query, keep_blank_values=True)
            original = query.pop("__path", ["/"])[0] or "/"
            if not original.startswith("/"):
                original = "/" + original
            self.path = original + ("?" + urlencode(query, doseq=True) if query else "")
        super().handle_any(method)
