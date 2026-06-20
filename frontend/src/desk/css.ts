import type { CSSProperties } from "react";

// The .dc prototype this app is ported from is built entirely on inline-style strings driven by
// CSS custom properties (the theme tokens). React's `style` prop wants an object, not a CSS string,
// so this helper parses the prototype's declaration strings ("a:b;c:d") into a React style object —
// preserving custom properties (--foo) verbatim and camelCasing standard property names. Keeping the
// strings 1:1 is what makes the port faithful (and far less error-prone than hand-converting each).
const cache = new Map<string, CSSProperties>();

export function css(input: string): CSSProperties {
  const hit = cache.get(input);
  if (hit) return hit;

  const out: Record<string, string> = {};
  for (const decl of input.split(";")) {
    const colon = decl.indexOf(":");
    if (colon < 0) continue;
    const rawKey = decl.slice(0, colon).trim();
    const value = decl.slice(colon + 1).trim();
    if (!rawKey || !value) continue;
    const key = rawKey.startsWith("--")
      ? rawKey // custom property — leave exactly as written
      : rawKey.replace(/-([a-z])/g, (_, c: string) => c.toUpperCase());
    out[key] = value;
  }

  const style = out as unknown as CSSProperties;
  cache.set(input, style);
  return style;
}
