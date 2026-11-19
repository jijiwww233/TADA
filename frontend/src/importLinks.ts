// xhslink links often carry the Chinese caption right after the slug, so stop at
// whitespace/CJK punctuation and trim any trailing non-ASCII text.
const URL_RE = /https?:\/\/[^\s，。；、！？"'<>【】（）()]+/g

export function extractUrls(text: string): string[] {
  const seen = new Set<string>()
  for (const raw of text.match(URL_RE) ?? []) {
    const url = raw.replace(/[^\x21-\x7e]+$/, '')
    if (url && !seen.has(url)) seen.add(url)
  }
  return [...seen]
}
