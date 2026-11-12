"""Documents: check a file, extract its text, chunk it, embed it, store it in Qdrant,
and find the passages that answer a question. Design: docs/design/rag.md.

    detect.py    what a file is, read from its bytes; the limits
    extract.py   PDF, DOCX, TXT and MD to pages of text
    chunk.py     pages to chunks of about 500 tokens
    ingest.py    extract -> chunk -> embed -> Qdrant, and the record's status
    retrieve.py  a question to the passages of the caller's own company
"""
