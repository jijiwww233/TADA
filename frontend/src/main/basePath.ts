/** 主站 base 是 /，2.0 快照用 /v2/ 构建；页面里的根路径都经过这里，主站行为不变。 */
export const BASE_PATH = import.meta.env.BASE_URL.replace(/\/$/, '')

/** 给以 / 开头的路径加前缀（已经在前缀下的不重复加）。 */
export function withBase(path: string): string {
  if (!BASE_PATH || !path.startsWith('/')) return path
  if (path === BASE_PATH || path.startsWith(BASE_PATH + '/') || path.startsWith(BASE_PATH + '?')) return path
  return BASE_PATH + path
}

/** 把浏览器地址里的前缀去掉，让 /v2/main 仍然按 /main 匹配路由。 */
export function stripBase(path: string): string {
  if (BASE_PATH && (path === BASE_PATH || path.startsWith(BASE_PATH + '/'))) {
    return path.slice(BASE_PATH.length) || '/'
  }
  return path
}
