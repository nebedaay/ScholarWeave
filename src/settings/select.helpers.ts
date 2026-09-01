import { SelectOption } from 'src/settings/SearchSelect';
import { cslListRaw } from 'src/bib/cslList';
import { langListRaw } from 'src/bib/cslLangList';

// Normalize a string for fuzzy comparison: lowercase + collapse whitespace.
function normalize(s: string): string {
  return s.toLowerCase().replace(/\s+/g, ' ').trim();
}

function scoreMatch(query: string, target: string): number {
  const q = normalize(query);
  const t = normalize(target);
  if (t === q) return 3;
  if (t.startsWith(q)) return 2;
  if (t.includes(q)) return 1;
  return 0;
}

function search(
  query: string,
  list: { value: string; label: string }[]
): SelectOption[] {
  if (!query.trim()) return list.slice(0, 20);

  const scored = list
    .map((item) => {
      const s = Math.max(
        scoreMatch(query, item.label),
        scoreMatch(query, item.value)
      );
      return { item, s };
    })
    .filter(({ s }) => s > 0)
    .sort((a, b) => b.s - a.s);

  return scored.slice(0, 20).map(({ item }) => item);
}

export function searchCSL(query: string): SelectOption[] {
  return search(query, cslListRaw);
}

export function searchCSLLangs(query: string): SelectOption[] {
  return search(query, langListRaw);
}
