"""Postgres storage for the hosted version (Vercel + Neon).

Only used when DATABASE_URL is set. It mimics the small part of Python's sqlite3
module that app.py uses, so the rest of the app doesn't need to know which
database it is talking to. Requires the pure-Python driver pg8000 (requirements.txt).
"""

import ssl
from urllib.parse import unquote, urlparse

import pg8000.dbapi


class Row(dict):
    """A result row readable by column name (row["id"]) or position (row[0]), like sqlite3.Row."""

    def __init__(self, names, values):
        super().__init__(zip(names, values))
        self._values = values

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, cursor):
        if cursor.description:
            names = [column[0] for column in cursor.description]
            self._rows = [Row(names, list(values)) for values in cursor.fetchall()]
        else:
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class Connection:
    def __init__(self, url):
        parts = urlparse(url)
        self._conn = pg8000.dbapi.connect(
            user=unquote(parts.username or ""),
            password=unquote(parts.password or ""),
            host=parts.hostname,
            port=parts.port or 5432,
            database=parts.path.lstrip("/") or "postgres",
            ssl_context=ssl.create_default_context(),
        )

    def execute(self, sql, params=()):
        cursor = self._conn.cursor()
        cursor.execute(sql.replace("?", "%s"), tuple(params))  # sqlite placeholders -> pg8000's
        return Result(cursor)

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()
