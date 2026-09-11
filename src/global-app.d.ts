// Obsidian exposes a global `app` singleton at runtime; several modules here
// (bib/helpers.ts, bib/bibManager.ts) rely on it directly rather than
// threading an App instance through every call. Older `obsidian` package
// versions shipped an ambient `declare const app: App` for this; newer
// versions dropped it to discourage the pattern, even though the runtime
// global itself hasn't gone away. Restoring the declaration locally keeps
// these call sites type-checking without changing any runtime behavior.
declare const app: import('obsidian').App;
