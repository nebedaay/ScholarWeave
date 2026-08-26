/**
 * Regression tests for outer-bracket multi-citation containers:
 *
 *   [ [[@a]]; [[@b]] ]          ->  [@a; @b]
 *   [ [[@a]] [[@b]] ]           ->  [@a; @b]        (semicolons auto-added)
 *   [ [[@a]] ]                  ->  [@a]            (single — container disregarded)
 *   [see [[@a]] and [[@b]] here] -> [@a; @b]       (text between ignored)
 *
 * Must NOT affect normal bracket citations ([@a; @b], [@a, p. 5]) or markdown
 * links ([text](url)).
 */
jest.mock('obsidian', () => ({ parseYaml: (s: string): any => JSON.parse(s) }), {
  virtual: true,
});
jest.mock('../zotlit', () => ({
  getLitNoteForCitekey: jest.fn((): undefined => undefined),
}));
import { getCitationSegments } from '../parser/parser';

const valTypes = (segs: any[][]): string[] =>
  segs.map((g: any[]) => g.map((s: any) => s.type + ':' + s.val).join(','));

describe('outer-bracket multi-citation containers', () => {
  it('merges [ [[@a]]; [[@b]] ] into one [@a; @b] group', () => {
    const segs = getCitationSegments('[ [[@a]]; [[@b]] ]', false, true);
    expect(valTypes(segs)).toEqual(['bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,bracket:]']);
  });

  it('adds semicolons when missing: [ [[@a]] [[@b]] ]', () => {
    const segs = getCitationSegments('[ [[@a]] [[@b]] ]', false, true);
    expect(valTypes(segs)).toEqual(['bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,bracket:]']);
  });

  it('merges three with mixed separators', () => {
    const segs = getCitationSegments('[ [[@a]]; [[@b]] [[@c]] ]', false, true);
    expect(valTypes(segs)).toEqual([
      'bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,separator:;,prefix: ,at:@,key:c,bracket:]',
    ]);
  });

  it('disregards the container for a single wikilink: [ [[@a]] ]', () => {
    const segs = getCitationSegments('[ [[@a]] ]', false, true);
    expect(valTypes(segs)).toEqual(['bracket:[,at:@,key:a,bracket:]']);
  });

  it('ignores text between outer brackets: [see [[@a]] and [[@b]] here]', () => {
    const segs = getCitationSegments('[see [[@a]] and [[@b]] here]', false, true);
    expect(valTypes(segs)).toEqual(['bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,bracket:]']);
  });

  it('applies alias rules inside the container', () => {
    const segs = getCitationSegments(
      '[ [[@a|see also @@, 6]]; [[@b]] ]',
      false,
      true
    );
    expect(valTypes(segs)).toEqual([
      'bracket:[,prefix:see also ,at:@,key:a,locatorSuffix:, ,locator:6,locatorLabel:page,separator:;,prefix: ,at:@,key:b,bracket:]',
    ]);
  });

  it('leaves plain pandoc multi-cites alone: [@a; @b]', () => {
    const segs = getCitationSegments('[@a; @b]', false, true);
    expect(valTypes(segs)).toEqual(['bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,bracket:]']);
  });

  it('parses comma-prefixed locators: [@a, p. 5] (pandoc semantics)', () => {
    const segs = getCitationSegments('[@a, p. 5]', false, true);
    expect(valTypes(segs)).toEqual([
      'bracket:[,at:@,key:a,locatorSuffix:, ,locatorLabel:p.,locatorSuffix: ,locator:5,bracket:]',
    ]);
  });

  it('parses bare-number locators: [@a, 5] (implicit page)', () => {
    const segs = getCitationSegments('[@a, 5]', false, true);
    expect(valTypes(segs)).toEqual([
      'bracket:[,at:@,key:a,locatorSuffix:, ,locator:5,locatorLabel:page,bracket:]',
    ]);
  });

  it('leaves markdown links alone: [text](url)', () => {
    expect(getCitationSegments('[text](url)', false, true)).toEqual([]);
  });

  it('merges a mixed container: [ [[@a]]; [@b] ] (wikilink + plain)', () => {
    const segs = getCitationSegments('[ [[@a]]; [@b] ]', false, true);
    expect(valTypes(segs)).toEqual(['bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,bracket:]']);
  });

  it('merges an all-plain container: [ [@a]; [@b] ] (no stray inner brackets)', () => {
    const segs = getCitationSegments('[ [@a]; [@b] ]', false, true);
    expect(valTypes(segs)).toEqual(['bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,bracket:]']);
  });

  it('merges a mixed container with a locator: [ [[@a]]; [@b, p. 5] ]', () => {
    const segs = getCitationSegments('[ [[@a]]; [@b, p. 5] ]', false, true);
    expect(valTypes(segs)).toEqual([
      'bracket:[,at:@,key:a,separator:;,prefix: ,at:@,key:b,locatorSuffix:, ,locatorLabel:p.,locatorSuffix: ,locator:5,bracket:]',
    ]);
  });
});
