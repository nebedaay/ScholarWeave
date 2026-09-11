// `citeproc` (citeproc-js) ships no type declarations of its own. Ambient
// `any`-typed shim so imports type-check; the actual API surface used here is
// documented at https://citeproc-js.readthedocs.io/, not in TS types.
declare module 'citeproc';
