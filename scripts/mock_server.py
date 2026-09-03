#!/usr/bin/env python3
"""Mock Potok API для демо без живого тенанта. См. SKILL.md / README.

Запуск: python3 scripts/mock_server.py [порт, по умолчанию 8765]
Затем: POTOK_BASE_URL=http://localhost:8765 POTOK_API_TOKEN=demo python3 scripts/talent_pool.py reserve

Отдаёт также v2 (declination_reasons, events), открытый Career API
(/open/constructor/:id) и файлы резюме (/files/cv/:id.docx) — все на одном
порту, см. SDD-C08-DELIVERY-EXTENSIONS.md §3.1, §10.
"""
import io
import json
import re
import sys
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load(name):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


JOBS = _load("jobs.json")
APPLICANTS = _load("applicants.json")
AJS_JOINS = _load("ajs_joins.json")
FINALISTS = _load("finalists.json")
OPEN_JOBS = _load("open_jobs.json")
CV_TEXTS = _load("cv_texts.json")
DECLINATION_REASONS = _load("declination_reasons.json")
EVENTS = _load("events.json")

JOBS_BY_ID = {j["id"]: j for j in JOBS}
APPLICANTS_BY_ID = {a["id"]: a for a in APPLICANTS}

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _escape_xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _build_docx(text):
    paragraphs = "".join(f'<w:p><w:r><w:t xml:space="preserve">{_escape_xml(line)}</w:t></w:r></w:p>' for line in text.split("\n"))
    document_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{WORD_NS}"><w:body>{paragraphs}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        z.writestr("_rels/.rels", _RELS_XML)
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, body):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _binary(self, data, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _not_found(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/jobs.json":
            return self._json({"data": JOBS, "page": 1, "pages": 1, "per_page": len(JOBS)})

        if path == "/applicants.json":
            return self._json({"data": APPLICANTS, "page": 1, "pages": 1, "per_page": len(APPLICANTS)})

        if path == "/finalists.json":
            return self._json({"objects": FINALISTS, "has_next_page": False, "page_next_cursor": None})

        m = re.match(r"^/jobs/(\d+)/ajs_joins\.json$", path)
        if m:
            joins = AJS_JOINS.get(m.group(1), [])
            return self._json({"objects": joins, "has_next_page": False, "page_next_cursor": None})

        m = re.match(r"^/jobs/(\d+)\.json$", path)
        if m:
            job = JOBS_BY_ID.get(int(m.group(1)))
            if job is None:
                return self._not_found()
            return self._json(job)

        m = re.match(r"^/applicants/(\d+)\.json$", path)
        if m:
            applicant = APPLICANTS_BY_ID.get(int(m.group(1)))
            if applicant is None:
                return self._not_found()
            return self._json(applicant)

        if path == "/declination_reasons.json":
            return self._json(DECLINATION_REASONS)

        if path == "/events.json":
            applicant_id = (qs.get("applicant_id") or [None])[0]
            data = EVENTS.get(applicant_id, [])
            return self._json({"data": data, "page": 1, "pages": 1, "per_page": 50})

        m = re.match(r"^/open/constructor/(\d+)$", path)
        if m:
            return self._json(OPEN_JOBS)

        m = re.match(r"^/files/cv/(\d+)\.docx$", path)
        if m:
            text = CV_TEXTS.get(m.group(1))
            if text is None:
                return self._not_found()
            return self._binary(
                _build_docx(text), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )

        return self._not_found()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    try:
        server = HTTPServer(("localhost", port), Handler)
    except OSError:
        sys.exit(f"Порт {port} занят — возможно, mock-сервер уже запущен (другой порт: python3 scripts/mock_server.py {port + 1})")
    print(f"Mock Potok API на http://localhost:{port} (фикстуры: {FIXTURES})")
    server.serve_forever()
