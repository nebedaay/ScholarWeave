import en from './locale/en';

// Active locale — swappable at runtime if additional locales are added.
const locale: Record<string, string> = en as Record<string, string>;

/**
 * Translate a UI string.  Falls back to the key itself when no translation
 * exists, so missing entries degrade gracefully rather than crashing.
 */
export function t(key: string): string {
  return locale[key] ?? key;
}
