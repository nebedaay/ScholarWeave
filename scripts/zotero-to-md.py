#!/usr/bin/env python3
"""
zotero_to_md.py — Convert DOCX or ODT (with Zotero citation fields) to Markdown.

Uses the same Zotero local REST API as Scannable-to-Pandoc.py to resolve citekeys:
    GET http://127.0.0.1:23119/api/users/{id}/items?itemKey={keys}
    GET http://127.0.0.1:23119/api/groups/{id}/items?itemKey={keys}

For ODT files the content.xml is parsed directly (no LibreOffice needed).
Zotero stores citations as text:reference-mark-start elements whose text:name
attribute holds the full CSL_CITATION JSON.

For DOCX files the document.xml ADDIN ZOTERO_ITEM fields are replaced directly,
then pandoc converts the result.

Requirements:
    pip install lxml requests
    pandoc on PATH
    Zotero running with Better BibTeX plugin active

Usage:
    python zotero_to_md.py input.docx [output.md]
    python zotero_to_md.py input.odt  [output.md]
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from copy import deepcopy

import requests
from lxml import etree

# ── XML namespaces ─────────────────────────────────────────────────────────────
WNS      = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XNML     = 'http://www.w3.org/XML/1998/namespace'
TEXT_NS  = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
STYLE_NS = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
FO_NS    = 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'

def W(tag):  return f'{{{WNS}}}{tag}'
def T(tag):  return f'{{{TEXT_NS}}}{tag}'

T_NAME     = f'{{{TEXT_NS}}}name'
T_STYLNAME = f'{{{TEXT_NS}}}style-name'
S_NAME     = f'{{{STYLE_NS}}}name'
S_FAMILY   = f'{{{STYLE_NS}}}family'
FO_WEIGHT  = f'{{{FO_NS}}}font-weight'
FO_FSTYLE  = f'{{{FO_NS}}}font-style'

# ── Zotero local REST API ──────────────────────────────────────────────────────
ZOTERO_API = 'http://127.0.0.1:23119/api'

_URI_RE = re.compile(
    r'https?://zotero\.org/(users|groups)/(\d+)/items/([A-Z0-9]+)'
)


def _parse_uri(uri: str):
    m = _URI_RE.search(uri)
    return (m.group(1), m.group(2), m.group(3)) if m else None


def fetch_citekeys(uris: list) -> dict:
    """
    Given Zotero item URIs, return {uri: citationKey}.
    Batches by library, chunk size 50 — same pattern as Scannable-to-Pandoc.py.
    """
    groups = defaultdict(dict)
    for uri in uris:
        parsed = _parse_uri(uri)
        if parsed:
            lib_type, lib_id, item_key = parsed
            groups[(lib_type, lib_id)][item_key] = uri

    result = {}
    for (lib_type, lib_id), key_to_uri in groups.items():
        keys = list(key_to_uri)
        for i in range(0, len(keys), 50):
            chunk = keys[i:i + 50]
            url   = f'{ZOTERO_API}/{lib_type}/{lib_id}/items?itemKey={",".join(chunk)}'
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code != 200:
                    print(f'  ⚠  Zotero API {resp.status_code}: {url}', file=sys.stderr)
                    for k in chunk:
                        result[key_to_uri[k]] = None
                    continue
                for item in resp.json():
                    key     = item['key']
                    citekey = item['data'].get('citationKey')
                    if key in key_to_uri:
                        result[key_to_uri[key]] = citekey
            except Exception as exc:
                print(f'  ⚠  Zotero API error: {exc}', file=sys.stderr)
                for k in chunk:
                    result[key_to_uri[k]] = None

    return result


# ── Citation string builder ────────────────────────────────────────────────────
LABEL_MAP = {
    'page': 'p.', 'pages': 'pp.', 'chapter': 'chap.',
    'section': 'sec.', 'paragraph': 'para.', 'verse': 'v.',
    'line': 'l.', 'figure': 'fig.',
}


def build_citation(zotero_json: dict, citekey_map: dict) -> str:
    """
    Build a pandoc citation string from a Zotero CSL_CITATION JSON object.

    Examples:
        Single:           [@hill2018, p. 45]
        Suppressed:       [-@hill2018]
        Multiple:         [@hill2018; @jones2020, p. 12]
        Prefix/suffix:    [see @hill2018, p. 45, emphasis mine]
    """
    parts = []
    for item in zotero_json.get('citationItems', []):
        uris = item.get('uris') or []
        uri  = uris[0] if uris else item.get('id', '')
        key  = citekey_map.get(uri)
        if not key:
            key = uri.rstrip('/').split('/')[-1]
            print(f'  ⚠  No citekey found; using raw key: {key}', file=sys.stderr)

        suppress = '-' if item.get('suppress-author') else ''
        ref      = f'{suppress}@{key}'

        locator = (item.get('locator') or '').strip()
        label   = LABEL_MAP.get(item.get('label', 'page'), item.get('label', 'p.'))
        prefix  = (item.get('prefix') or '').strip()
        suffix  = (item.get('suffix') or '').strip()

        inline = ref
        if locator:
            inline += f', {label} {locator}'
        if suffix:
            inline += f', {suffix}'
        if prefix:
            inline = f'{prefix} {inline}'
        parts.append(inline)

    return '[' + '; '.join(parts) + ']'


# ── ODT span normalization ────────────────────────────────────────────────────
# LibreOffice sometimes nests a bare automatic character style (e.g. T66, which
# carries only an rsid revision marker and no formatting) INSIDE a named style
# like ArabicRom (italic).  pandoc wraps each level separately, producing
# double-em → **bold** instead of *italic*.  We unwrap these no-op spans before
# handing the ODT to pandoc.

def _find_bare_auto_styles(root: etree._Element) -> set:
    """Return names of automatic character styles that carry no font formatting."""
    bare = set()
    for s in root.findall(f'.//{{{STYLE_NS}}}style'):
        if s.get(S_FAMILY) != 'text':
            continue
        name  = s.get(S_NAME, '')
        props = s.find(f'{{{STYLE_NS}}}text-properties')
        if props is None:
            bare.add(name)
        elif not props.get(FO_WEIGHT) and not props.get(FO_FSTYLE):
            bare.add(name)
    return bare


def _unwrap(el: etree._Element) -> None:
    """Replace an element with its children/text in its parent."""
    parent = el.getparent()
    if parent is None:
        return
    siblings = list(parent)
    idx = siblings.index(el)

    # Text before first child goes to prev-sibling tail or parent text
    if el.text:
        if idx > 0:
            prev = siblings[idx - 1]
            prev.tail = (prev.tail or '') + el.text
        else:
            parent.text = (parent.text or '') + el.text

    # Move children into parent
    for i, child in enumerate(list(el)):
        parent.insert(idx + i, child)

    n_inserted = len(list(el))  # count AFTER move (already moved)

    # Tail of the removed element goes after the last inserted child (or prev)
    if el.tail:
        if n_inserted:
            last = list(parent)[idx + n_inserted - 1]
            last.tail = (last.tail or '') + el.tail
        elif idx > 0:
            list(parent)[idx - 1].tail = (list(parent)[idx - 1].tail or '') + el.tail
        else:
            parent.text = (parent.text or '') + el.tail

    parent.remove(el)


def unwrap_bare_spans(root: etree._Element) -> int:
    """
    Unwrap text:span elements whose style adds no font formatting.
    Returns the number of spans removed.
    """
    bare = _find_bare_auto_styles(root)
    T_SPAN = T('span')
    # Collect first, then process in reverse document order
    to_unwrap = [
        el for el in root.iter(T_SPAN)
        if el.get(T_STYLNAME, '') in bare
    ]
    for el in reversed(to_unwrap):
        _unwrap(el)
    return len(to_unwrap)


def strip_footnote_para_styles(root: etree._Element) -> int:
    """
    Remove text:style-name from <text:p> elements inside <text:note-body>.

    Footnote paragraphs often carry a named paragraph style (e.g. "Footnote")
    that carries a fo:margin-left indent.  Pandoc interprets that indent as a
    blockquote and wraps the footnote body in "> ".  Since the paragraph is
    already inside a <text:note> element, the style is redundant — stripping it
    lets pandoc emit the footnote as plain [^n]: text.

    Returns the number of paragraphs modified.
    """
    T_NOTE_BODY = T('note-body')
    T_P         = T('p')
    T_STYLNAME_ATTR = f'{{{TEXT_NS}}}style-name'
    count = 0
    for note_body in root.iter(T_NOTE_BODY):
        for para in note_body.iter(T_P):
            if T_STYLNAME_ATTR in para.attrib:
                del para.attrib[T_STYLNAME_ATTR]
                count += 1
    return count


# ── Post-processing: fix pandoc-escaped citation brackets ─────────────────────
# pandoc escapes [ as \[ in markdown output because [ normally starts a link.
# Our injected citations look like [@citekey] but arrive as \[@citekey\].

_CITE_ESCAPE_RE = re.compile(r'\\\[(?=[^\]]*\\?@)((?:[^\]\\]|\\[^\]])+)\\\]')

def fix_escaped_citations(text: str) -> str:
    """
    Un-escape \\[@citekey\\] → [@citekey] in pandoc markdown output.

    Handles all pandoc citation forms:
      Standard:    \\[@hill2018, p. 45\\]
      Compound:    \\[@key1; \\@key2; \\@key3\\]
      Suppressed:  \\[-@hill2018\\]
      With prefix: \\[for example, \\@key1; \\@key2\\]

    The lookahead (?=[^\\]]*\\\\?@) ensures we only match bracket pairs that
    contain at least one @.  Inside, \\\\[^\\]] allows \\\\@ (the only character
    pandoc escapes inside citations) but not \\\\] (which would greedily swallow
    the closing bracket of the next citation).
    """
    def _sub(m: re.Match) -> str:
        inner = m.group(1).replace('\\@', '@')
        return f'[{inner}]'
    return _CITE_ESCAPE_RE.sub(_sub, text)


# ── ODT processing (native — no LibreOffice) ───────────────────────────────────
# Zotero stores citations in ODT as:
#   <text:reference-mark-start text:name="ZOTERO_ITEM CSL_CITATION {...json...} RNDxxxxxxx"/>
#   <text:span ...>(Author Year)</text:span>
#   <text:reference-mark-end text:name="ZOTERO_ITEM CSL_CITATION {...json...} RNDxxxxxxx"/>
#
# The JSON in the name attribute is entity-encoded by lxml when the attribute
# is read, so lxml gives us the decoded string directly.

_ZOTERO_NAME_RE = re.compile(
    r'^ZOTERO_ITEM CSL_CITATION (\{.+\})\s+RND\w+$', re.DOTALL
)
# Fallback: name may not end with RNDxxx (older Zotero versions)
_ZOTERO_NAME_BARE_RE = re.compile(
    r'^ZOTERO_ITEM CSL_CITATION (\{.+\})$', re.DOTALL
)


def _parse_zotero_name(name: str):
    """Return parsed JSON dict from a reference-mark name, or None."""
    for pat in (_ZOTERO_NAME_RE, _ZOTERO_NAME_BARE_RE):
        m = pat.match(name)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                return None
    return None


def collect_uris_from_odt(content_xml: bytes) -> list:
    """Scan raw content.xml bytes for Zotero item URIs."""
    text = content_xml.decode('utf-8', errors='replace')
    # Decode XML entities so URIs are readable
    text = text.replace('&quot;', '"').replace('&amp;', '&')
    uris = []
    for m in _URI_RE.finditer(text):
        uris.append(f'http://zotero.org/{m.group(1)}/{m.group(2)}/items/{m.group(3)}')
    return list(set(uris))


def process_odt_xml(content_xml_bytes: bytes, citekey_map: dict) -> tuple:
    """
    Parse content.xml, replace all Zotero citation marks with pandoc cites.
    Returns (modified_bytes, n_replacements).
    """
    root = etree.fromstring(content_xml_bytes)

    # Index: name → end-mark element
    end_marks = {}
    for el in root.iter(T('reference-mark-end')):
        name = el.get(T_NAME, '')
        if 'ZOTERO' in name:
            end_marks[name] = el

    count = 0
    # Iterate over start marks; process in document order, then reverse-splice
    # per-parent so indices stay valid.
    # Collect (parent, start_i, end_i, cite_text) grouped by parent.
    from collections import defaultdict as _dd
    parent_ops = _dd(list)  # parent_el → [(start_i, end_i, cite_text)]

    for start_el in list(root.iter(T('reference-mark-start'))):
        name = start_el.get(T_NAME, '')
        if 'ZOTERO' not in name:
            continue
        cite_json = _parse_zotero_name(name)
        if cite_json is None:
            print(f'  ⚠  Could not parse JSON from: {name[:80]}', file=sys.stderr)
            continue

        end_el = end_marks.get(name)
        if end_el is None:
            print(f'  ⚠  No matching end mark for: {name[:80]}', file=sys.stderr)
            continue

        start_parent = start_el.getparent()
        end_parent   = end_el.getparent()

        if start_parent is None or end_parent is None:
            continue
        if start_parent is not end_parent:
            # Cross-paragraph citation — rare, handle by operating on the
            # paragraph that contains the start mark only.
            print(f'  ⚠  Cross-paragraph citation; only start-mark paragraph will be modified.',
                  file=sys.stderr)
            # Fall through; end mark won't be found in start_parent's children
            # and the operation will be skipped below.
            continue

        parent   = start_parent
        children = list(parent)
        try:
            si = children.index(start_el)
            ei = children.index(end_el)
        except ValueError:
            continue
        if si > ei:
            continue

        cite_text = build_citation(cite_json, citekey_map)
        parent_ops[id(parent)].append((parent, si, ei, cite_text))

    # Apply replacements in reverse order within each parent
    for ops in parent_ops.values():
        # Sort descending by start index so removal doesn't shift later ops
        for parent, si, ei, cite_text in sorted(ops, key=lambda x: -x[1]):
            children = list(parent)
            # Preserve tail of end mark (text that follows the citation)
            end_tail = children[ei].tail or ''

            # Remove all elements in [si, ei]
            for el in children[si:ei + 1]:
                parent.remove(el)

            # Insert a text:span with the pandoc citation
            span = etree.Element(T('span'))
            span.text = cite_text
            span.tail = end_tail
            parent.insert(si, span)
            count += 1

    out_bytes = etree.tostring(root, xml_declaration=True, encoding='UTF-8')
    return out_bytes, count


def convert_odt(input_path: str, output_md: str) -> None:
    print(f'Reading {os.path.basename(input_path)} …')
    with zipfile.ZipFile(input_path) as z:
        files = {name: z.read(name) for name in z.namelist()}

    content_xml = files.get('content.xml')
    if content_xml is None:
        sys.exit('content.xml not found — is this a valid ODT?')

    print('  Scanning for Zotero item URIs …')
    uris = collect_uris_from_odt(content_xml)
    if not uris:
        print('  ⚠  No Zotero URIs found in content.xml.')
        print('     Proceeding with pandoc directly (citations will be plain text).')
    else:
        print(f'  Looking up {len(uris)} unique item(s) via Zotero API …')
        citekey_map = fetch_citekeys(uris)
        found = sum(1 for v in citekey_map.values() if v)
        print(f'  Resolved {found}/{len(uris)} citekey(s).')

        print('Processing Zotero citation fields …')
        new_content, n = process_odt_xml(content_xml, citekey_map)
        print(f'  {n} field(s) replaced.')
        files['content.xml'] = new_content

    # Normalise ODT spans: remove bare auto-style wrappers that cause
    # pandoc to double-wrap italic text as bold.
    print('Normalising span formatting …')
    root = etree.fromstring(files['content.xml'])
    n_unwrapped = unwrap_bare_spans(root)
    if n_unwrapped:
        print(f'  {n_unwrapped} bare span(s) unwrapped.')
    n_footnote_styles = strip_footnote_para_styles(root)
    if n_footnote_styles:
        print(f'  {n_footnote_styles} footnote paragraph style(s) stripped.')
    files['content.xml'] = etree.tostring(root, xml_declaration=True, encoding='UTF-8')

    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_odt = os.path.join(tmp_dir, 'processed.odt')
        with zipfile.ZipFile(tmp_odt, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)

        print('Running pandoc …')
        result = subprocess.run(
            [os.environ.get('SW_PANDOC', 'pandoc'), tmp_odt, '-t', 'markdown-smart', '--wrap=none'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f'pandoc error:\n{result.stderr}')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    md_text = fix_escaped_citations(result.stdout)
    with open(output_md, 'w', encoding='utf-8') as f:
        f.write(md_text)

    print(f'Done → {output_md}')


# ── DOCX processing ────────────────────────────────────────────────────────────
ZOTERO_FIELD_RE = re.compile(r'ADDIN ZOTERO_ITEM CSL_CITATION\s+(\{.+\})', re.DOTALL)


def collect_uris_from_docx(doc_xml: bytes) -> list:
    text = doc_xml.decode('utf-8', errors='replace')
    text = text.replace('&quot;', '"').replace('&amp;', '&')
    uris = []
    for m in _URI_RE.finditer(text):
        uris.append(f'http://zotero.org/{m.group(1)}/{m.group(2)}/items/{m.group(3)}')
    return list(set(uris))


def replace_fields_in_para(para: etree._Element, citekey_map: dict) -> int:
    """Replace Zotero citation fields in a paragraph. Returns number replaced."""
    children   = list(para)
    regions    = []
    depth      = 0
    begin_i    = None
    collecting = False
    instr_buf  = []

    for i, child in enumerate(children):
        for fc in child.iter(W('fldChar')):
            ftype = fc.get(W('fldCharType'))
            if ftype == 'begin':
                if depth == 0:
                    begin_i    = i
                    instr_buf  = []
                    collecting = True
                depth += 1
            elif ftype == 'separate':
                collecting = False
            elif ftype == 'end':
                depth -= 1
                if depth == 0 and begin_i is not None:
                    regions.append((begin_i, i, ''.join(instr_buf)))
                    begin_i = None
        if collecting:
            for it in child.iter(W('instrText')):
                instr_buf.append(it.text or '')

    count = 0
    for begin_i, end_i, instr in reversed(regions):
        m = ZOTERO_FIELD_RE.search(instr)
        if not m:
            continue
        try:
            cite_json = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            print(f'  ⚠  JSON parse error: {exc}', file=sys.stderr)
            continue

        cite_text = build_citation(cite_json, citekey_map)

        new_run   = etree.Element(W('r'))
        first_rpr = children[begin_i].find(W('rPr'))
        if first_rpr is not None:
            new_run.append(deepcopy(first_rpr))
        t_el = etree.SubElement(new_run, W('t'))
        t_el.set(f'{{{XNML}}}space', 'preserve')
        t_el.text = cite_text

        for el in children[begin_i:end_i + 1]:
            para.remove(el)
        para.insert(begin_i, new_run)
        count += 1

    return count


def convert_docx(input_path: str, output_md: str) -> None:
    print(f'Reading {os.path.basename(input_path)} …')
    with zipfile.ZipFile(input_path) as z:
        files = {name: z.read(name) for name in z.namelist()}

    doc_xml = files.get('word/document.xml')
    if doc_xml is None:
        sys.exit('word/document.xml not found — is this a valid DOCX?')

    print('  Scanning for Zotero item URIs …')
    uris = collect_uris_from_docx(doc_xml)
    if not uris:
        print('  ⚠  No Zotero URIs found — is Zotero running? Are these Zotero fields?')
        return

    print(f'  Looking up {len(uris)} unique item(s) via Zotero API …')
    citekey_map = fetch_citekeys(uris)
    found = sum(1 for v in citekey_map.values() if v)
    print(f'  Resolved {found}/{len(uris)} citekey(s).')

    print('Processing Zotero citation fields …')
    root  = etree.fromstring(doc_xml)
    total = 0
    for para in root.iter(W('p')):
        total += replace_fields_in_para(para, citekey_map)
    print(f'  {total} field(s) replaced.')

    files['word/document.xml'] = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', standalone=True
    )

    tmp_dir = tempfile.mkdtemp()
    try:
        tmp_docx = os.path.join(tmp_dir, 'processed.docx')
        with zipfile.ZipFile(tmp_docx, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in files.items():
                z.writestr(name, data)

        print('Running pandoc …')
        result = subprocess.run(
            [os.environ.get('SW_PANDOC', 'pandoc'), tmp_docx, '-t', 'markdown-smart', '--wrap=none'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            sys.exit(f'pandoc error:\n{result.stderr}')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    md_text = fix_escaped_citations(result.stdout)
    with open(output_md, 'w', encoding='utf-8') as f:
        f.write(md_text)

    print(f'Done → {output_md}')


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    input_path = sys.argv[1]
    if not os.path.isfile(input_path):
        sys.exit(f'File not found: {input_path}')

    output_path = (
        sys.argv[2] if len(sys.argv) > 2
        else re.sub(r'\.(docx|odt)$', '.md', input_path, flags=re.IGNORECASE)
    )

    suffix = os.path.splitext(input_path)[1].lower()
    if suffix == '.odt':
        convert_odt(input_path, output_path)
    elif suffix == '.docx':
        convert_docx(input_path, output_path)
    else:
        sys.exit(f'Unsupported format: {suffix}  (accepted: .docx, .odt)')


if __name__ == '__main__':
    main()
