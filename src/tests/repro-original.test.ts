import { getCitationSegments, getCitations } from '../../parser/parser';

describe('legacy container + derived-filename regression (2026-08)', () => {
  it('parses the plain pandoc container with suppress-author and § locators', () => {
    const segs = getCitationSegments(
      '[-@sukayrijKashfAlhijab1961, §63; @kanunganunNisaTijaniyyat2010, §94]',
      false,
      true
    );
    expect(segs.length).toBe(1);
    const cites = getCitations(segs[0]).citations;
    expect(cites).toHaveLength(2);
    expect(cites[0].id).toBe('sukayrijKashfAlhijab1961');
    expect(cites[0].locator).toBe('63');
    expect(cites[0].label).toBe('section');
    expect(cites[0]['suppress-author']).toBe(true);
    expect(cites[1].id).toBe('kanunganunNisaTijaniyyat2010');
    expect(cites[1].locator).toBe('94');
    expect(cites[1].label).toBe('section');
  });

  it('parses the linked container form with locators inside the aliases', () => {
    const segs = getCitationSegments(
      '[ [[@sukayrijKashfAlhijab1961|-@ §63]]; [[@kanunganunNisaTijaniyyat2010|@, §94]]]',
      false,
      true
    );
    expect(segs.length).toBe(1);
    const cites = getCitations(segs[0]).citations;
    expect(cites).toHaveLength(2);
    expect(cites[0].locator).toBe('63');
    expect(cites[0].label).toBe('section');
    expect(cites[0]['suppress-author']).toBe(true);
    expect(cites[1].locator).toBe('94');
    expect(cites[1].label).toBe('section');
  });

  it('parses sect. and vol. labels with the @ proxy', () => {
    const segs = getCitationSegments(
      '[[@sukayrijKashfAlhijab1961|-@, sect. 121]]',
      false,
      true
    );
    expect(segs.length).toBe(1);
    const cites = getCitations(segs[0]).citations;
    expect(cites[0].locator).toBe('121');
    expect(cites[0].label).toBe('section');
    expect(cites[0]['suppress-author']).toBe(true);
  });

  it('combines vol. X, p. Y into the single Chicago locator X:Y', () => {
    // Roman volume → arabic, single page locator (Zotero allows one).
    const segs = getCitationSegments(
      '[[@niasseJawahirAlrasaail1969|@, vol. I, p. 113]]',
      false,
      true
    );
    expect(segs.length).toBe(1);
    const cites = getCitations(segs[0]).citations;
    expect(cites[0].locator).toBe('1:113');
    expect(cites[0].label).toBe('page');
    expect(cites[0].suffix).toBeUndefined();
  });

  it('combines arabic vol + page range, keeps standalone vol', () => {
    const segs = getCitationSegments(
      '[[@key|@, vol. 2, pp. 100-113]]',
      false,
      true
    );
    expect(getCitations(segs[0]).citations[0].locator).toBe('2:100-113');
    const volOnly = getCitationSegments('[[@key|@, vol. I]]', false, true);
    const volCite = getCitations(volOnly[0]).citations[0];
    expect(volCite.locator).toBe('I');
    expect(volCite.label).toBe('volume');
  });

  it('ignores derived-filename wikilinks (@key - transcription)', () => {
    const segs = getCitationSegments(
      'see [[@kanunganunNisaTijaniyyat2010 - transcription]] and [[@kanunganunNisaTijaniyyat2010 - English translation]] here',
      false,
      true
    );
    expect(segs).toEqual([]);
  });

  it('still parses plain wikilinks and aliased citations', () => {
    expect(getCitationSegments('[[@key]]', false, true).length).toBe(1);
    expect(getCitationSegments('[[@key|@, p. 5]]', false, true).length).toBe(1);
  });
});
