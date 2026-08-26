import { getCitationSegments, getCitations } from '../../parser/parser';

describe('render parity with pandoc', () => {
  const cases = [
    '[[@smith2020|@, vol. I, p. 113]]',
    '[[@smith2020|@, p. 113]]',
    '[[@smith2020|@, vol. I]]',
  ];
  it('shows the citation structure for each', () => {
    for (const text of cases) {
      const segs = getCitationSegments(text, false, true);
      const c = getCitations(segs[0]);
      const item = c.citations[0];
      console.log(JSON.stringify(text), '=>', JSON.stringify({
        locator: item.locator, label: item.label, suffix: item.suffix,
      }));
    }
  });
});
