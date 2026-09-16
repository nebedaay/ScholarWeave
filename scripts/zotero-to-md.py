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
import datetime

import requests
from lxml import etree

# ── XML namespaces ─────────────────────────────────────────────────────────────
WNS      = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XNML     = 'http://www.w3.org/XML/1998/namespace'
TEXT_NS  = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
STYLE_NS = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
FO_NS    = 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'
META_NS  = 'urn:oasis:names:tc:opendocument:xmlns:meta:1.0'
DC_NS    = 'http://purl.org/dc/elements/1.1/'
DCTERMS_NS = 'http://purl.org/dc/terms/'

def W(tag):  return f'{{{WNS}}}{tag}'
def T(tag):  return f'{{{TEXT_NS}}}{tag}'

T_NAME     = f'{{{TEXT_NS}}}name'
T_STYLNAME = f'{{{TEXT_NS}}}style-name'
S_NAME     = f'{{{STYLE_NS}}}name'
S_FAMILY   = f'{{{STYLE_NS}}}family'
S_DISPLAY  = f'{{{STYLE_NS}}}display-name'
T_OUTLINE  = f'{{{TEXT_NS}}}outline-level'
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


def _canonical_item_uri(uri: str):
    """Normalise a Zotero item URI to http://zotero.org/<type>/<id>/items/<key>,
    or None. `_URI_RE` is digits-only, so it skips the `users/local/<8char>`
    form Zotero often lists FIRST — canonicalising each URI lets us match
    whichever form the API resolution used."""
    m = _URI_RE.search(uri or '')
    if m:
        return f'http://zotero.org/{m.group(1)}/{m.group(2)}/items/{m.group(3)}'
    return None


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
        # Newer Zotero: citationItems[].uris (array). Older (pre-5): `uri`
        # (string or array). Fall back to `id`.
        uris = item.get('uris') or item.get('uri') or []
        if isinstance(uris, str):
            uris = [uris]
        uri  = uris[0] if uris else item.get('id', '')

        key = None
        # 1. Zotero embeds the citekey in itemData — most reliable: it needs
        #    neither the Zotero/BBT API nor a URI form we can index.
        item_data = item.get('itemData') or {}
        key = item_data.get('citation-key') or item_data.get('citationKey')
        # 2. Resolve any of the item's URIs via the API. The first URI is often
        #    `users/local/<8char>` (which _URI_RE skips), so try them all.
        if not key:
            for u in uris:
                cu = _canonical_item_uri(u)
                if cu and citekey_map.get(cu):
                    key = citekey_map[cu]
                    break
        if not key and citekey_map.get(uri):
            key = citekey_map[uri]
        # 3. Fall back to the raw item key (last resort — NOT a citekey, so it
        #    will not resolve in Obsidian; warn loudly).
        if not key:
            key = uri.rstrip('/').split('/')[-1]
            print(f'  ⚠  No citekey for item {uri or item.get("id")!r}; '
                  f'using raw item key: {key}', file=sys.stderr)

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
    r'^(?:ZOTERO_ITEM|ZOTERO_CITATION)(?:\s+CSL_CITATION)?\s+(\{.+\})(?:\s+RND\w+)?$',
    re.DOTALL
)
# Fallback: name without the trailing RNDxxx id (older Zotero versions).
_ZOTERO_NAME_BARE_RE = re.compile(
    r'^(?:ZOTERO_ITEM|ZOTERO_CITATION)(?:\s+CSL_CITATION)?\s+(\{.+\})$', re.DOTALL
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


def _heading_level(style_name):
    """Outline level from a style name / display name, or None. Handles ODF's
    `_20_` space encoding: 'Heading_20_1' / 'Heading 1' → 1."""
    if not style_name:
        return None
    s = style_name.replace('_20_', ' ').strip()
    m = re.match(r'^Heading\s+(\d+)\b', s)
    return int(m.group(1)) if m else None


def promote_heading_paragraphs(root, styles_xml=None) -> int:
    """Some ODTs (e.g. older Zotero/LibreOffice exports) mark headings with a
    paragraph STYLE — `<text:p text:style-name="Heading_20_1">` — instead of a
    real heading element `<text:h text:outline-level="1">`. Pandoc's ODT reader
    only maps `<text:h>`, so those paragraphs flatten to body text. Rewrite them
    in place before pandoc runs. Returns the number converted."""
    level_by_style = {}

    def scan(rt):
        for st in rt.iter(f'{{{STYLE_NS}}}style'):
            if st.get(S_FAMILY) != 'paragraph':
                continue
            name = st.get(S_NAME)
            if not name:
                continue
            lvl = _heading_level(name) or _heading_level(st.get(S_DISPLAY) or '')
            if lvl:
                level_by_style[name] = lvl

    if styles_xml:
        try:
            scan(etree.fromstring(styles_xml))
        except etree.XMLSyntaxError:
            pass
    scan(root)  # automatic styles live in content.xml

    n = 0
    for p in list(root.iter(T('p'))):
        style = p.get(T_STYLNAME)
        lvl = level_by_style.get(style) or _heading_level(style)
        if not lvl:
            continue
        h = etree.Element(T('h'))
        for k, v in p.attrib.items():
            h.set(k, v)
        h.set(T_OUTLINE, str(lvl))
        h.text = p.text
        for child in list(p):
            h.append(child)
        p.getparent().replace(p, h)
        n += 1
    return n


def _style_chain(name, styles):
    chain, seen = [], set()
    while name and name not in seen:
        seen.add(name)
        chain.append(name)
        name = styles.get(name)
    return chain


def _resolve_style_parents(root, styles_xml):
    """Map style name → parent-style-name from styles.xml + content auto-styles."""
    styles = {}
    for src in (styles_xml, None):
        rt = etree.fromstring(src) if src else root
        for st in rt.iter(f'{{{STYLE_NS}}}style'):
            name = st.get(S_NAME)
            if name:
                styles[name] = st.get(f'{{{STYLE_NS}}}parent-style-name')
    return styles


def _chain_has(chain, target):
    return any(
        (c or '').replace('_20_', ' ').strip().lower() == target for c in chain
    )


def _drop_element(el) -> None:
    """Remove an element, keeping its tail text (so removing an inline field
    doesn't swallow the text that followed it)."""
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or '') + tail
        else:
            parent.text = (parent.text or '') + tail
    parent.remove(el)


_ODT_DATE_FIELDS = {
    'date', 'time', 'date-time', 'creation-date', 'creation-time',
    'modification-date', 'modification-time', 'print-date', 'print-time',
}


def strip_date_fields_odt(root) -> int:
    """Remove ODT date/time FIELDS (`<text:date>`, `<text:modification-date>`,
    …) from the body — they render 'today', which is redundant with `created`
    (and a date is never part of an author's name)."""
    n = 0
    for el in list(root.iter()):
        q = etree.QName(el)
        if q.namespace == TEXT_NS and q.localname in _ODT_DATE_FIELDS:
            _drop_element(el)
            n += 1
    return n


def _strip_preceding_bibliography_heading(el) -> int:
    """Remove an immediately-preceding (bar blank) paragraph whose text is
    exactly 'Bibliography' — the label above the generated list."""
    prev = el.getprevious()
    while (prev is not None and etree.QName(prev).localname == 'p'
           and not ''.join(prev.itertext()).strip()):
        prev = prev.getprevious()
    if prev is not None and ''.join(prev.itertext()).strip().lower() == 'bibliography':
        prev.getparent().remove(prev)
        return 1
    return 0


def strip_bibliography_odt(root) -> int:
    """Remove the Zotero-generated bibliography: a <text:section> named
    `… CSL_BIBLIOGRAPHY …` (and its 'Bibliography' heading). Only the generated
    field is touched — a hand-written bibliography has no such marker and is
    left alone. Regenerable from the converted citations, so dropping it keeps
    ODT and DOCX imports identical."""
    n = 0
    for sect in list(root.iter(T('section'))):
        name = sect.get(T('name')) or ''
        if 'CSL_BIBLIOGRAPHY' not in name and 'ZOTERO_BIBL' not in name:
            continue
        n += _strip_preceding_bibliography_heading(sect)
        _drop_element(sect)
        n += 1
    return n


def strip_bibliography_docx(root) -> int:
    """Remove the Zotero-generated bibliography from a DOCX part (the
    `CSL_BIBLIOGRAPHY` field spans paragraphs, from its fldChar begin to its
    end) and its 'Bibliography' heading. The DOCX twin of
    strip_bibliography_odt()."""
    paras = list(root.iter(W('p')))
    for idx, p in enumerate(paras):
        instr = ''.join(t.text or '' for t in p.iter(W('instrText')))
        if 'CSL_BIBLIOGRAPHY' not in instr and 'ZOTERO_BIBL' not in instr:
            continue
        n = _strip_preceding_bibliography_heading(p)
        depth = 0
        for q in paras[idx:]:
            for fc in q.iter(W('fldChar')):
                ft = fc.get(W('fldCharType'))
                if ft == 'begin':
                    depth += 1
                elif ft == 'end':
                    depth -= 1
            parent = q.getparent()
            if parent is not None:
                parent.remove(q)
                n += 1
            if depth <= 0:
                break
        return n
    return 0


def _element_text(el) -> str:
    """ODT paragraph text, turning <text:line-break> into newlines (so
    line-break-separated author/affiliation lines don't run together)."""
    parts = []
    for node in el.iter():
        if node.tag == T('line-break'):
            parts.append('\n')
        if node.text:
            parts.append(node.text)
        if node is not el and node.tail:
            parts.append(node.tail)
    return ''.join(parts)


def _docx_text(p) -> str:
    """DOCX paragraph text, turning <w:br>/<w:cr> into newlines and <w:tab>
    into spaces (so line-separated author/affiliation lines don't run together).
    i.e. the DOCX twin of _element_text()."""
    parts = []
    for node in p.iter():
        if node.tag in (W('t'), W('delText')):
            if node.text:
                parts.append(node.text)
        elif node.tag in (W('br'), W('cr')):
            parts.append('\n')
        elif node.tag == W('tab'):
            parts.append(' ')
        elif node.tag == W('noBreakHyphen'):
            parts.append('-')
    return ''.join(parts)


def _chain_own_is_heading(chain):
    """Whether a paragraph's OWN style is a heading (first chain entry), not an
    ancestor — Word bases Title/Subtitle on Heading, so scanning the whole chain
    would misclassify a Title paragraph as a heading."""
    n = ((chain[0] if chain else '') or '').replace('_20_', ' ').strip().lower()
    return n == 'heading' or bool(re.match(r'^heading\s*\d+$', n))


def odt_paragraphs(root, styles_xml=None):
    """Neutral paragraph items from a parsed ODT body, in document order:
    {'el', 'kind', 'is_para', 'text', 'chain'}. The ODT walker for
    collect_import_metadata; the DOCX walker yields the identical shape."""
    styles = _resolve_style_parents(root, styles_xml)
    for el in root.iter():
        if el.tag == T('h'):
            yield {'el': el, 'kind': 'heading', 'is_para': False,
                   'text': _element_text(el), 'chain': []}
        elif el.tag == T('p'):
            chain = _style_chain(el.get(T_STYLNAME), styles)
            yield {'el': el, 'is_para': True,
                   'kind': 'heading' if _chain_own_is_heading(chain) else 'para',
                   'text': _element_text(el), 'chain': chain}


def _docx_styles(styles_xml):
    """styleId → (name, basedOn) from word/styles.xml."""
    out = {}
    if not styles_xml:
        return out
    try:
        root = etree.fromstring(styles_xml)
    except etree.XMLSyntaxError:
        return out
    for st in root.iter(W('style')):
        sid = st.get(W('styleId'))
        if not sid:
            continue
        name_el, base_el = st.find(W('name')), st.find(W('basedOn'))
        out[sid] = (
            name_el.get(W('val')) if name_el is not None else None,
            base_el.get(W('val')) if base_el is not None else None,
        )
    return out


def _docx_style_chain(p, styles):
    """The paragraph's style chain (styleId + w:name at each level), via
    w:pStyle → w:basedOn; the DOCX twin of _style_chain()/_resolve_style_parents()."""
    ppr = p.find(W('pPr'))
    st = ppr.find(W('pStyle')) if ppr is not None else None
    sid = st.get(W('val')) if st is not None else None
    chain, seen = [], set()
    while sid and sid not in seen:
        seen.add(sid)
        chain.append(sid)
        name, base = styles.get(sid, (None, None))
        if name:
            chain.append(name)
        sid = base
    return chain


def _docx_outline_level(p):
    ppr = p.find(W('pPr'))
    lvl = ppr.find(W('outlineLvl')) if ppr is not None else None
    return lvl.get(W('val')) if lvl is not None else None


def docx_paragraphs(root, styles_xml=None):
    """Neutral paragraph items from a parsed DOCX body (same shape as
    odt_paragraphs())."""
    styles = _docx_styles(styles_xml)
    for p in root.iter(W('p')):
        chain = _docx_style_chain(p, styles)
        kind = 'heading' if (_chain_own_is_heading(chain)
                             or _docx_outline_level(p) is not None) else 'para'
        yield {'el': p, 'is_para': True, 'kind': kind,
               'text': _docx_text(p), 'chain': chain}


def collect_import_metadata(paragraphs, original_filename):
    """Import frontmatter fields from the neutral paragraph walk
    (odt_paragraphs() / docx_paragraphs()), so ODT and DOCX share one flow.

    - title:  STYLE-CHAIN Title/Subtitle paragraph(s), max 2 joined with ": ",
              and removed from the body; when there is no Title/Subtitle style,
              the first non-empty paragraph (kept in the body).
    - author: every Author-styled paragraph's text, blank-line separated.
    - abstract: up to 3 paragraphs following a paragraph reading "Abstract",
              stopping at a heading.
    - aliases: the title plus the part before its first ":".
    Title/Subtitle, Author and Abstract paragraphs are all REMOVED from the body
    (they now live in the frontmatter, so keeping them would duplicate them on
    re-export); every other paragraph is preserved. Returns a dict; `created`/
    `original-filename` are filled by the caller.
    """
    items = list(paragraphs)

    title_parts, author_parts, abstract_parts, remove = [], [], [], []
    in_abstract = False
    for item in items:
        raw = item['text'].strip()
        flat = ' '.join(raw.split())
        chain = item['chain']
        is_heading = item['kind'] == 'heading'
        if item.get('is_para') and (_chain_has(chain, 'title') or _chain_has(chain, 'subtitle')):
            if flat:
                title_parts.append(flat)
            remove.append(item['el'])
            continue
        if item.get('is_para') and _chain_has(chain, 'author'):
            if raw:
                author_parts.append(raw)
            remove.append(item['el'])
            continue
        if not in_abstract and flat.lower() == 'abstract':
            in_abstract = True
            remove.append(item['el'])
            continue
        if in_abstract:
            if is_heading:
                in_abstract = False
            elif flat and len(abstract_parts) < 3:
                abstract_parts.append(flat)
                remove.append(item['el'])

    for el in remove:
        if el.getparent() is not None:
            el.getparent().remove(el)

    title = ': '.join(title_parts[:2]) if title_parts else ''
    if not title:
        for item in items:
            if not item.get('is_para'):
                continue
            t = ' '.join(item['text'].split())
            if t:
                title = t
                break
    aliases = []
    if title:
        aliases.append(title)
        short = title.split(':', 1)[0].strip()
        if short and short != title:
            aliases.append(short)

    return {
        'title': title,
        'aliases': aliases,
        'author': '\n\n'.join(author_parts),
        'abstract': '\n\n'.join(abstract_parts),
        'original_filename': original_filename,
    }


def _normalize_dt(s: str):
    """'2011-01-19T10:33:44' → '2011-01-19 10:33'. Timezone-aware values (DOCX
    writes UTC, e.g. `…T19:44:00Z`) are converted to LOCAL time (Edmonton) so
    `original-created` matches `created`'s clock; a bare date is kept as-is;
    anything unrecognised is returned unchanged."""
    s = s.strip()
    try:
        dt = datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError:
        dt = None
    if dt is not None:
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime('%Y-%m-%d %H:%M')
    m = re.match(r'(\d{4}-\d{2}-\d{2})', s)
    return m.group(1) if m else s


def extract_source_created(xml_bytes, date_tags):
    """The source document's own creation date — the first non-empty among
    `date_tags` (fully-qualified lxml tags) — normalised to 'YYYY-MM-DD HH:MM',
    or None. ODT passes meta:creation-date/dc:date; DOCX passes
    dcterms:created/dcterms:modified (so both go through this one function)."""
    if not xml_bytes:
        return None
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return None
    for tag in date_tags:
        el = root.find('.//' + tag)
        if el is not None and el.text and el.text.strip():
            return _normalize_dt(el.text.strip())
    return None


def build_import_frontmatter(meta, created) -> str:
    """Render the enriched import frontmatter (see docs/import-export.md)."""
    out = ['---', f'created: {created}']
    out += ['up:', '  - "[[sw imports]]"', 'related:', 'aliases:']
    for a in meta.get('aliases') or []:
        out.append(f'  - {_yaml_quote(a)}')
    if meta.get('title'):
        out.append(f'title: {_yaml_quote(meta["title"])}')
    if meta.get('author'):
        out.append('author: |-')
        for i, para in enumerate(meta['author'].split('\n\n')):
            if i:
                out.append('')
            for line in para.split('\n'):
                out.append(f'  {line}')
    if meta.get('abstract'):
        out.append('abstract:')
        out.append('  - |-')
        for i, para in enumerate(meta['abstract'].split('\n\n')):
            if i:
                out.append('')
            for line in para.split('\n'):
                out.append(f'    {line}')
    if meta.get('original_created'):
        out.append(f"original-created: {meta['original_created']}")
    out.append(f'original-filename: {_yaml_quote(meta["original_filename"])}')
    out.append('---')
    return '\n'.join(out)
def _yaml_quote(s: str) -> str:
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


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
    n_headings = promote_heading_paragraphs(root, files.get('styles.xml'))
    if n_headings:
        print(f'  {n_headings} style-based heading(s) promoted to real headings.')
    n_dates = strip_date_fields_odt(root)
    if n_dates:
        print(f'  {n_dates} date field(s) removed.')
    n_bib = strip_bibliography_odt(root)
    if n_bib:
        print('  Generated Zotero bibliography removed.')
    meta = collect_import_metadata(
        odt_paragraphs(root, files.get('styles.xml')), os.path.basename(input_path))
    meta['original_created'] = extract_source_created(
        files.get('meta.xml'),
        [f'{{{META_NS}}}creation-date', f'{{{DC_NS}}}date'])
    created = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    if meta['title']:
        print(f"  Title detected: {meta['title'][:70]}")
    if meta['author']:
        print(f"  Author block: {meta['author'].splitlines()[0][:50]}")
    if meta['original_created']:
        print(f"  Source-created date: {meta['original_created']}")
    frontmatter = build_import_frontmatter(meta, created)
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
    md_text = frontmatter + '\n\n' + md_text
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


def _docx_field_regions(para):
    """Split a DOCX paragraph into (children, [(begin_i, end_i, instr), …]) for
    every complex field (fldChar begin…end). Shared by the date-field stripper
    and the Zotero-field replacer."""
    children = list(para)
    regions = []
    depth = 0
    begin_i = None
    collecting = False
    instr_buf = []

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

    return children, regions


_DOCX_DATE_INSTRS = {
    'DATE', 'TIME', 'CREATEDATE', 'CREATETIME', 'SAVEDATE', 'SAVETIME',
    'PRINTDATE', 'PRINTTIME', 'EDITTIME',
}


def _is_date_instr(instr: str) -> bool:
    m = re.match(r'\s*([A-Za-z]+)', instr or '')
    return bool(m) and m.group(1).upper() in _DOCX_DATE_INSTRS


def strip_date_fields_docx(root) -> int:
    """Remove Word date/time FIELDs (DATE, CREATEDATE, SAVEDATE, …) from a DOCX
    part — they render 'today', redundant with `created` (and a date is never
    part of an author's name). The DOCX twin of strip_date_fields_odt()."""
    n = 0
    for para in list(root.iter(W('p'))):
        children, regions = _docx_field_regions(para)
        for begin_i, end_i, instr in reversed(regions):
            if _is_date_instr(instr):
                for el in children[begin_i:end_i + 1]:
                    para.remove(el)
                n += 1
        for fs in list(para.iter(W('fldSimple'))):
            if _is_date_instr(fs.get(W('instr')) or ''):
                fs.getparent().remove(fs)
                n += 1
    return n


def replace_fields_in_para(para: etree._Element, citekey_map: dict) -> int:
    """Replace Zotero citation fields in a paragraph. Returns number replaced."""
    children, regions = _docx_field_regions(para)

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

    # Zotero citation fields can live in document.xml AND in the separate
    # footnotes/endnotes parts (pandoc reads each part independently). Scan and
    # process all of them, or footnote citations arrive as the field's plain
    # cached text ("footnotes are a separate part" — same trap as export).
    citation_parts = [
        p for p in ('word/document.xml', 'word/footnotes.xml', 'word/endnotes.xml')
        if p in files
    ]

    print('  Scanning for Zotero item URIs …')
    uris = sorted({u for part in citation_parts for u in collect_uris_from_docx(files[part])})
    citekey_map = {}
    if uris:
        print(f'  Looking up {len(uris)} unique item(s) via Zotero API …')
        citekey_map = fetch_citekeys(uris)
        found = sum(1 for v in citekey_map.values() if v)
        print(f'  Resolved {found}/{len(uris)} citekey(s).')
    else:
        print('  ⚠  No Zotero URIs found — importing without citation conversion.')

    print('Processing Zotero citation fields …')
    root  = etree.fromstring(doc_xml)
    n_dates = strip_date_fields_docx(root)
    if n_dates:
        print(f'  {n_dates} date field(s) removed.')
    n_bib = strip_bibliography_docx(root)
    if n_bib:
        print('  Generated Zotero bibliography removed.')
    total = 0
    for para in root.iter(W('p')):
        total += replace_fields_in_para(para, citekey_map)

    meta = collect_import_metadata(
        docx_paragraphs(root, files.get('word/styles.xml')), os.path.basename(input_path))
    meta['original_created'] = extract_source_created(
        files.get('docProps/core.xml'),
        [f'{{{DCTERMS_NS}}}created', f'{{{DCTERMS_NS}}}modified'])
    created = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    if meta['title']:
        print(f"  Title detected: {meta['title'][:70]}")
    if meta['author']:
        print(f"  Author block: {meta['author'].splitlines()[0][:50]}")
    if meta['original_created']:
        print(f"  Source-created date: {meta['original_created']}")
    frontmatter = build_import_frontmatter(meta, created)

    for part in citation_parts:
        if part == 'word/document.xml':
            continue
        sub = etree.fromstring(files[part])
        strip_date_fields_docx(sub)
        strip_bibliography_docx(sub)
        n = 0
        for para in sub.iter(W('p')):
            n += replace_fields_in_para(para, citekey_map)
        files[part] = etree.tostring(
            sub, xml_declaration=True, encoding='UTF-8', standalone=True)
        total += n
        print(f'  {n} field(s) replaced in {part.split("/")[-1]}.')
    print(f'  {total} field(s) replaced total.')
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
    md_text = frontmatter + '\n\n' + md_text
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
