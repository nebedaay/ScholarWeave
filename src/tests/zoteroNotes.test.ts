jest.mock(
  'obsidian',
  () => ({
    TFile: class {},
    requestUrl: jest.fn(),
  }),
  { virtual: true }
);

import {
  zoteroHtmlToMarkdown,
  findNotesSection,
  recordedKeys,
} from '../zoteroNotes';

describe('zoteroHtmlToMarkdown', () => {
  it('keeps plain text', () => {
    expect(zoteroHtmlToMarkdown('Hello world')).toBe('Hello world');
  });

  it('converts inline bold and italics to Markdown', () => {
    expect(zoteroHtmlToMarkdown('<b>bold</b> and <i>it</i>')).toBe(
      '**bold** and *it*'
    );
    expect(zoteroHtmlToMarkdown('<strong>b</strong><em>i</em>')).toBe('**b***i*');
  });

  it('turns block tags into blank-line paragraph breaks', () => {
    expect(zoteroHtmlToMarkdown('<p>one</p><p>two</p>')).toBe('one\n\ntwo');
    expect(zoteroHtmlToMarkdown('a<br>b')).toBe('a\nb');
  });

  it('preserves sub/sup and decodes entities', () => {
    expect(zoteroHtmlToMarkdown('H<sub>2</sub>O')).toBe('H<sub>2</sub>O');
    expect(zoteroHtmlToMarkdown('x<sup>2</sup> &amp; y')).toBe('x<sup>2</sup> & y');
    expect(zoteroHtmlToMarkdown('a&nbsp;b')).toBe('a b');
  });

  it('strips unknown tags and collapses excess blank lines', () => {
    expect(zoteroHtmlToMarkdown('<div><span>keep</span></div>')).toBe('keep');
    expect(zoteroHtmlToMarkdown('a<br><br><br>b')).toBe('a\n\nb');
  });
});

describe('findNotesSection', () => {
  const emptyLit = [
    '---',
    'zotero-key: ABC123',
    '---',
    '## Notes',
    '%%zt-managed%%',
    '',
    '%%/zt-managed%%',
  ].join('\n');

  it('finds an empty Notes section and reports an empty body', () => {
    const slot = findNotesSection(emptyLit);
    expect(slot).not.toBeNull();
    expect(slot!.body).toBe('');
    // sectionEnd must sit at the opening managed marker.
    expect(emptyLit.slice(slot!.sectionEnd).startsWith('%%zt-managed%%')).toBe(true);
  });

  it('reports existing content as the body', () => {
    const withContent = emptyLit.replace(
      '## Notes\n%%zt-managed%%',
      '## Notes\n\nMy own note\n\n%%zt-managed%%'
    );
    expect(findNotesSection(withContent)!.body).toBe('My own note');
  });

  it('handles a Notes section with no managed region', () => {
    const noRegion = '## Notes\n\nsomething\n';
    const slot = findNotesSection(noRegion);
    expect(slot!.body).toBe('something');
    expect(slot!.sectionEnd).toBe(noRegion.length);
  });

  it('returns null when there is no Notes heading', () => {
    expect(findNotesSection('# Title\n\ntext')).toBeNull();
  });

  it('does not match "## Notes" as a prefix of another heading', () => {
    // "## Notes on method" is a different heading and must not match.
    expect(findNotesSection('## Notes on method\n\ntext')).toBeNull();
  });
});

describe('recordedKeys', () => {
  it('parses a marker with several keys', () => {
    const s = 'body\n<!-- sw-zn: ABCD1234 EFGH5678 -->\n';
    const keys = recordedKeys(s);
    expect(keys.has('ABCD1234')).toBe(true);
    expect(keys.has('EFGH5678')).toBe(true);
    expect(keys.size).toBe(2);
  });

  it('ignores a bare marker with no keys', () => {
    expect(recordedKeys('x\n<!-- sw-zn -->\n').size).toBe(0);
  });

  it('returns nothing when no marker is present', () => {
    expect(recordedKeys('nothing here').size).toBe(0);
  });
});
