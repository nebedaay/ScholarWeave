"""lc_merge_helpers.py — low-level OOXML helpers for the Linked Citations
export merge (lc_export_merge.py).

Self-contained copy of the helpers the merge needs, extracted from the
vault's iafr_template_pipeline.py so the plugin is portable outside the
vault. Only the functions actually used by lc_export_merge are included.
"""

import random
from lxml import etree

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
W14 = 'http://schemas.microsoft.com/office/word/2010/wordml'
XML = 'http://www.w3.org/XML/1998/namespace'


def tag(n):
    return '{%s}%s' % (W, n)


def w14(n):
    return '{%s}%s' % (W14, n)


def get_style(p):
    """Return the pStyle val of a paragraph, or None."""
    pPr = p.find(tag('pPr'))
    if pPr is None:
        return None
    pStyle = pPr.find(tag('pStyle'))
    if pStyle is None:
        return None
    return pStyle.get(tag('val'))


def set_style(p, style_name):
    """Set (or create) the pStyle val on a paragraph."""
    pPr = p.find(tag('pPr'))
    if pPr is None:
        pPr = etree.Element(tag('pPr'))
        p.insert(0, pPr)
    pStyle = pPr.find(tag('pStyle'))
    if pStyle is None:
        pStyle = etree.Element(tag('pStyle'))
        pPr.insert(0, pStyle)
    pStyle.set(tag('val'), style_name)


def collect_ids(doc):
    """Return a set of all w14:paraId values (upper-cased) in a document tree."""
    ids = set()
    for el in doc.iter():
        pid = el.get(w14('paraId'))
        if pid:
            ids.add(pid.upper())
    return ids


def mint_id(used):
    """Return a unique 8-hex paraId not in `used`, and add it to `used`."""
    while True:
        c = '%08X' % random.randint(0x10000000, 0xFFFFFFFE)
        if c not in used:
            used.add(c)
            return c


def ensure_para_id(p, used):
    """
    If p already has a paraId that is not in `used`, register it and return.
    Otherwise mint a new one and assign it (along with textId="77777777").
    """
    pid = p.get(w14('paraId'))
    if pid:
        up = pid.upper()
        if up not in used:
            used.add(up)
            return
    new_id = mint_id(used)
    p.set(w14('paraId'), new_id)
    p.set(w14('textId'), '77777777')
