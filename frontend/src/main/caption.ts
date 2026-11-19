/**
 * 详情页和瀑布流图注共用的短标题：从描述里截一句（本地规则，不花接口钱），截不到就用前两个标签。
 */
export function shortTitle(description: string | null | undefined, tags: (string | null | undefined)[] | null | undefined): string {
  const list = (tags ?? []).filter((tag): tag is string => !!tag)
  const classifier = /^(?:一|两|几)[张幅份个只位件组款](?=\S)/u
  const phrase = (description ?? '').trim()
    .replace(/^(?:这[是张幅个]|图中|画面(?:中|里|中央|主体)?)[，、：:\s]*/u, '')
    .replace(classifier, '')
    .replace(/^(?:是|为|展示|呈现|描绘|可以看到|主要是)(?:了|着)?\s*/u, '')
    .replace(classifier, '')
    .split(/[，。；：！？、,.!?;:\n]|(?:上|中|里)?(?:呈现|描绘|展示|包含|采用|使用|形成|构成|位于|坐在|出现在)|(?:在)(?=[^\s，。]{2,})/u, 1)[0]
    .trim()
    .replace(/[的和与及]$/u, '')
  if (!phrase || /^(?:无法|不明|未知|没有|未见|图中|画面)/u.test(phrase)) return list.slice(0, 2).join(' · ')
  return cutCaption(phrase, 10)
}


function cutCaption(text: string, length: number) {
  const chars = [...text]
  const prefix = chars.slice(0, length).join('').trimEnd()
  if (!/[A-Za-z]$/u.test(prefix) || !/^[A-Za-z]/u.test(chars.slice(length).join(''))) return prefix
  return prefix.replace(/[A-Za-z][A-Za-z'’-]*$/u, '').trimEnd() || prefix
}

export function fitCaption(title: string, suffix: string, width: number, measure: (text: string) => number) {
  if (!width || measure(title + suffix) <= width) return title
  const length = [...title].length
  for (let n = length - 1; n > 0; n--) {
    const prefix = cutCaption(title, n)
    if (prefix && measure(prefix + '…' + suffix) <= width) return prefix + '…'
  }
  return '…'
}
