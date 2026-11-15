"""Per-category outcome memory (reply/win rates) and PDF CV text extraction."""
from __future__ import annotations

import base64

import pytest

from agent.memory.store import MemoryStore
from apps.api import portfolio as pf


def _pdf(text: str) -> bytes:
    """A minimal one-page text PDF with a correct xref table."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def test_outcomes_are_counted_per_category():
    m = MemoryStore(":memory:")
    m.record_outcome("o", "p", "k1", None, "won", category="web")
    m.record_outcome("o", "p", "k2", None, "replied", category="web")
    m.record_outcome("o", "p", "k3", None, "ignored", category="web")
    m.record_outcome("o", "p", "k4", None, "ignored", category="ml")
    m.record_outcome("o", "p", "k5", None, "replied")  # no category recorded
    m.record_outcome("other", "p", "k6", None, "won", category="web")  # another owner never leaks
    t = m.category_table("o")
    assert t["web"] == {"n": 3, "replied": 2, "won": 1}
    assert t["ml"] == {"n": 1, "replied": 0, "won": 0}
    assert t["uncategorised"] == {"n": 1, "replied": 1, "won": 0}
    assert m.category_table("o", "nobody") == {}
    m.record_outcome("o", "p", "k3", None, "replied", category="web")  # forward-only upgrade keeps the category
    assert m.category_table("o")["web"]["replied"] == 3


def test_old_outcomes_table_gets_the_category_column(tmp_path):
    import sqlite3
    db = tmp_path / "old.sqlite"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE outcomes(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, persona TEXT NOT NULL,"
                    " proposal_key TEXT NOT NULL, strategy TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL, source TEXT NOT NULL,"
                    " ts TEXT NOT NULL, UNIQUE(owner, persona, proposal_key));"
                    "INSERT INTO outcomes(owner, persona, proposal_key, outcome, source, ts) VALUES ('o','p','k','won','marketplace','t');")
    c.commit()
    c.close()
    m = MemoryStore(db)
    assert m.category_table("o") == {"uncategorised": {"n": 1, "replied": 1, "won": 1}}


def test_pdf_text_is_extracted():
    text = pf.pdf_to_text(base64.b64encode(_pdf("Senior engineer who built a RAG assistant with pgvector")).decode())
    assert "RAG assistant" in text


@pytest.mark.parametrize("payload,reason", [
    (base64.b64encode(b"not a pdf at all, just text").decode(), "not a PDF"),
    ("@@@ not base64 @@@", "base64"),
])
def test_bad_pdf_is_rejected(payload, reason):
    with pytest.raises(ValueError, match=reason):
        pf.pdf_to_text(payload)


def test_pdf_size_cap(monkeypatch):
    monkeypatch.setattr(pf, "MAX_PDF_BYTES", 10)
    with pytest.raises(ValueError, match="larger"):
        pf.pdf_to_text(base64.b64encode(_pdf("Senior engineer who built a RAG assistant")).decode())
