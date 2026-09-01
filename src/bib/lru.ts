/**
 * Minimal LRU cache backed by a Map (insertion-order iteration guaranteed by
 * the ECMAScript spec — oldest entry is first).  Only the operations used by
 * BibManager are implemented.
 */
export class SimpleLRU<K, V> {
  private readonly max: number;
  private readonly cache: Map<K, V> = new Map();

  constructor({ max }: { max: number }) {
    this.max = max;
  }

  has(key: K): boolean {
    return this.cache.has(key);
  }

  get(key: K): V | undefined {
    if (!this.cache.has(key)) return undefined;
    // Refresh entry to mark it as recently used.
    const value = this.cache.get(key)!;
    this.cache.delete(key);
    this.cache.set(key, value);
    return value;
  }

  set(key: K, value: V): this {
    if (this.cache.has(key)) this.cache.delete(key);
    else if (this.cache.size >= this.max) {
      // Evict the oldest entry (first key in the Map).
      const oldest = this.cache.keys().next().value;
      if (oldest !== undefined) this.cache.delete(oldest);
    }
    this.cache.set(key, value);
    return this;
  }

  delete(key: K): boolean {
    return this.cache.delete(key);
  }

  clear(): void {
    this.cache.clear();
  }

  get size(): number {
    return this.cache.size;
  }
}
