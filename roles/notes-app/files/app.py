#!/usr/bin/env python3
import os
import sqlite3
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

DB_PATH = os.environ.get("APP_DB_PATH", "/opt/notes-app/notes.db")
PORT = int(os.environ.get("APP_PORT", "5000"))

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def get_notes():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT content, created_at FROM notes ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return rows

def add_note(content):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO notes (content, created_at) VALUES (?, ?)",
        (content, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

def render_page():
    notes_html = ""
    for content, created_at in get_notes():
        safe_content = (content.replace("&", "&amp;")
                                .replace("<", "&lt;")
                                .replace(">", "&gt;"))
        notes_html += f"""
        <div class="note">
            <div class="note-time">🕒 {created_at}</div>
            <div class="note-content">📌 {safe_content}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
    <title>Notes App</title>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 40px auto; background: #f4f4f4; }}
        textarea {{ width: 100%; height: 80px; padding: 10px; font-size: 14px; }}
        button {{ padding: 10px 20px; background: #2563eb; color: white; border: none; border-radius: 4px; cursor: pointer; margin-top: 8px; }}
        .note {{ background: white; padding: 12px 16px; margin-top: 12px; border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .note-time {{ color: #666; font-size: 12px; }}
        .note-content {{ margin-top: 4px; }}
    </style>
</head>
<body>
    <h2>📝 Notes App</h2>
    <form method="POST" action="/">
        <textarea name="note" placeholder="Write your note here..." required></textarea><br>
        <button type="submit">Save Note</button>
    </form>
    {notes_html}
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(render_page().encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        fields = parse_qs(body)
        note = fields.get("note", [""])[0].strip()
        if note:
            add_note(note)
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # keep systemd journal clean; rely on journalctl -u for real debugging

if __name__ == "__main__":
    init_db()
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Serving on 127.0.0.1:{PORT}")
    server.serve_forever()
