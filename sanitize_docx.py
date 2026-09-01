#!/usr/bin/env python3
"""
sanitize_docx.py — Strip personal metadata from .docx (and .odt) template files
before committing to version control.

What it scrubs in .docx (word/docx XML inside the zip):
  docProps/core.xml:
    dc:creator           → "Author Name"
    cp:lastModifiedBy    → "Author Name"
    dcterms:created      → "2000-01-01T00:00:00Z"
    dcterms:modified     → "2000-01-01T00:00:00Z"
    cp:revision          → "1"
  word/document.xml (and header/footer XMLs):
    Cached text inside fldSimple/fldChar fields whose instruction reads
    DOCPROPERTY "Author" is reset to "Author Name" so the placeholder is
    visible when the template is opened directly (Word replaces it on export).

What it does NOT touch:
  - Custom properties (docProps/custom.xml) — these contain ScholarWeave's
    "Short Title" placeholder and should stay as-is.
  - Any content in word/document.xml beyond field caches.

Usage:
  python3 sanitize_docx.py templates/book.docx templates/article.docx
  python3 sanitize_docx.py templates/*.docx          # shell glob
  python3 sanitize_docx.py --dry-run templates/*.docx

The script edits files in-place and prints a one-line summary per file.
"""

import argparse
import copy
import io
import os
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

# ── Namespace maps ────────────────────────────────────────────────────────────

CORE_NS = {
    'cp':      'http://schemas.openxmlformats.org/package/2006/metadata/core-properties',
    'dc':      'http://purl.org/dc/elements/1.1/',
    'dcterms': 'http://purl.org/dc/terms/',
    'xsi':     'http://www.w3.org/2001/XMLSchema-instance',
}

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/'

for prefix, uri in CORE_NS.items():
    ET.register_namespace(prefix, uri)

# ── Helpers ───────────────────────────────────────────────────────────────────

PLACEHOLDER_AUTHOR   = 'Author Name'
PLACEHOLDER_DATE     = '2000-01-01T00:00:00Z'
PLACEHOLDER_REVISION = '1'


def _q(ns_map: dict, prefix: str, local: str) -> str:
    return f'{{{ns_map[prefix]}}}{local}'


def _sanitize_core_xml(xml_bytes: bytes) -> tuple[bytes, bool]:
    """Replace personal fields in docProps/core.xml. Returns (new_bytes, changed)."""
    tree = ET.fromstring(xml_bytes)
    changed = False

    targets = {
        _q(CORE_NS, 'dc',      'creator'):          PLACEHOLDER_AUTHOR,
        _q(CORE_NS, 'cp',      'lastModifiedBy'):   PLACEHOLDER_AUTHOR,
        _q(CORE_NS, 'dcterms', 'created'):          PLACEHOLDER_DATE,
        _q(CORE_NS, 'dcterms', 'modified'):         PLACEHOLDER_DATE,
        _q(CORE_NS, 'cp',      'revision'):         PLACEHOLDER_REVISION,
    }

    for el in tree.iter():
        if el.tag in targets:
            want = targets[el.tag]
            if el.text != want:
                el.text = want
                changed = True

    if not changed:
        return xml_bytes, False

    out = io.BytesIO()
    tree = ET.ElementTree(tree)
    tree.write(out, xml_declaration=True, encoding='UTF-8', short_empty_elements=False)
    return out.getvalue(), True


def _reset_author_field_caches(xml_bytes: bytes) -> tuple[bytes, bool]:
    """
    In word/document.xml (and header/footer XMLs), find fldSimple elements
    whose instr attribute is DOCPROPERTY "Author" and reset their cached text
    runs to the placeholder so the template looks right when opened directly.

    Word re-evaluates and overwrites this cache at export time, so changing
    it here is purely cosmetic for the template viewer.
    """
    text = xml_bytes.decode('utf-8', errors='replace')

    # Match: <w:fldSimple w:instr='...DOCPROPERTY "Author"...'>…text…</w:fldSimple>
    # We only touch the cached <w:t> text inside, not the instruction.
    pattern = re.compile(
        r'(<w:fldSimple\b[^>]*\bw:instr=["\'][^"\']*DOCPROPERTY\s+"Author"[^"\']*["\'][^>]*>)'
        r'(.*?)'
        r'(</w:fldSimple>)',
        re.DOTALL,
    )

    changed = False

    def replace_cached(m: re.Match) -> str:
        nonlocal changed
        inner = m.group(2)
        # Replace every <w:t ...>...</w:t> inside with the placeholder text.
        new_inner, n = re.subn(
            r'(<w:t(?:\s[^>]*)?>)([^<]*)(</w:t>)',
            lambda wt: wt.group(1) + PLACEHOLDER_AUTHOR + wt.group(3),
            inner,
        )
        if n:
            changed = True
            return m.group(1) + new_inner + m.group(3)
        return m.group(0)

    new_text = pattern.sub(replace_cached, text)
    if not changed:
        return xml_bytes, False
    return new_text.encode('utf-8'), True


# ── .docx processing ──────────────────────────────────────────────────────────

_WORD_XML_PARTS = re.compile(
    r'^word/(document|header\d*|footer\d*)\.xml$'
)


def sanitize_docx(path: str, dry_run: bool = False) -> str:
    """Sanitize a .docx file in-place. Returns a human-readable status line."""
    with open(path, 'rb') as fh:
        original = fh.read()

    in_buf  = io.BytesIO(original)
    out_buf = io.BytesIO()
    changes: list[str] = []

    with zipfile.ZipFile(in_buf, 'r') as zin, \
         zipfile.ZipFile(out_buf, 'w', compression=zipfile.ZIP_DEFLATED) as zout:

        for item in zin.infolist():
            data = zin.read(item.filename)

            if item.filename == 'docProps/core.xml':
                new_data, changed = _sanitize_core_xml(data)
                if changed:
                    changes.append('core.xml')
                data = new_data

            elif _WORD_XML_PARTS.match(item.filename):
                new_data, changed = _reset_author_field_caches(data)
                if changed:
                    changes.append(item.filename)
                data = new_data

            zout.writestr(item, data)

    if not changes:
        return f'  {os.path.basename(path)}: already clean'

    new_bytes = out_buf.getvalue()
    if not dry_run:
        with open(path, 'wb') as fh:
            fh.write(new_bytes)

    tag = '[dry-run] would patch' if dry_run else 'patched'
    return f'  {os.path.basename(path)}: {tag} — {", ".join(changes)}'


# ── .odt processing (basic) ───────────────────────────────────────────────────

ODT_META_NS = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'meta':   'urn:oasis:names:tc:opendocument:xmlns:meta:1.0',
    'dc':     'http://purl.org/dc/elements/1.1/',
}
for prefix, uri in ODT_META_NS.items():
    ET.register_namespace(prefix, uri)


def _sanitize_odt_meta(xml_bytes: bytes) -> tuple[bytes, bool]:
    tree = ET.fromstring(xml_bytes)
    changed = False
    targets = {
        _q(ODT_META_NS, 'dc',   'creator'):         PLACEHOLDER_AUTHOR,
        _q(ODT_META_NS, 'dc',   'date'):             PLACEHOLDER_DATE,
        _q(ODT_META_NS, 'meta', 'initial-creator'):  PLACEHOLDER_AUTHOR,
        _q(ODT_META_NS, 'meta', 'creation-date'):    PLACEHOLDER_DATE,
        _q(ODT_META_NS, 'meta', 'editing-cycles'):   PLACEHOLDER_REVISION,
    }
    for el in tree.iter():
        if el.tag in targets:
            want = targets[el.tag]
            if el.text != want:
                el.text = want
                changed = True
    if not changed:
        return xml_bytes, False
    out = io.BytesIO()
    ET.ElementTree(tree).write(out, xml_declaration=True, encoding='UTF-8',
                               short_empty_elements=False)
    return out.getvalue(), True


def sanitize_odt(path: str, dry_run: bool = False) -> str:
    with open(path, 'rb') as fh:
        original = fh.read()
    in_buf  = io.BytesIO(original)
    out_buf = io.BytesIO()
    changes: list[str] = []

    with zipfile.ZipFile(in_buf, 'r') as zin, \
         zipfile.ZipFile(out_buf, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == 'meta.xml':
                new_data, changed = _sanitize_odt_meta(data)
                if changed:
                    changes.append('meta.xml')
                data = new_data
            zout.writestr(item, data)

    if not changes:
        return f'  {os.path.basename(path)}: already clean'
    new_bytes = out_buf.getvalue()
    if not dry_run:
        with open(path, 'wb') as fh:
            fh.write(new_bytes)
    tag = '[dry-run] would patch' if dry_run else 'patched'
    return f'  {os.path.basename(path)}: {tag} — {", ".join(changes)}'


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description='Strip personal metadata from .docx/.odt template files.'
    )
    ap.add_argument('files', nargs='+', help='.docx or .odt file paths')
    ap.add_argument(
        '--dry-run', action='store_true',
        help='Show what would change without writing any files.'
    )
    args = ap.parse_args()

    errors = 0
    for path in args.files:
        if not os.path.isfile(path):
            print(f'  {path}: not found — skipped', file=sys.stderr)
            errors += 1
            continue
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == '.docx':
                print(sanitize_docx(path, dry_run=args.dry_run))
            elif ext == '.odt':
                print(sanitize_odt(path, dry_run=args.dry_run))
            else:
                print(f'  {path}: unsupported extension — skipped')
        except Exception as exc:
            print(f'  {path}: ERROR — {exc}', file=sys.stderr)
            errors += 1

    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
