/**
 * Detect search queries from URL patterns.
 * Returns { engine, query } or null.
 */
export function detectSearch(url) {
  const patterns = [
    { pattern: /google\.[a-z.]+\/search\?/, param: "q", engine: "google" },
    { pattern: /duckduckgo\.com\/\?/, param: "q", engine: "duckduckgo" },
    { pattern: /bing\.com\/search\?/, param: "q", engine: "bing" },
    { pattern: /search\.yahoo\.com\/search\?/, param: "p", engine: "yahoo" },
    { pattern: /stackoverflow\.com\/search\?/, param: "q", engine: "stackoverflow" },
    { pattern: /github\.com\/search\?/, param: "q", engine: "github" },
    { pattern: /reddit\.com\/search\/\?/, param: "q", engine: "reddit" },
    { pattern: /youtube\.com\/results\?/, param: "search_query", engine: "youtube" },
    { pattern: /npmjs\.com\/search\?/, param: "q", engine: "npm" },
    { pattern: /pypi\.org\/search\/\?/, param: "q", engine: "pypi" },
    { pattern: /docs\.anthropic\.com\/.*search/, param: "q", engine: "anthropic-docs" },
  ];

  try {
    const parsed = new URL(url);
    for (const { pattern, param, engine } of patterns) {
      if (pattern.test(url)) {
        const query = parsed.searchParams.get(param);
        if (query) return { engine, query };
      }
    }
  } catch (_) {}
  return null;
}
