/** 全站统一的 HTTP 错误文案：403/413/429/502/504/500 说真实原因，其余才归为网络或服务出错。 */
export const DEMO_FORBIDDEN = '演示版不提供此功能'
export const IMAGE_TOO_LARGE = '图片太大，单张最多 20MB'
export const NETWORK_OR_SERVICE = '网络或服务出错，请稍后重试'
/** 公网演示版每天 200 张的额度用完了（后端 429） */
export const DEMO_QUOTA_FULL = '演示库今天收满 200 张了，明天再试'
/** 公网链路慢，图传不完就被服务器断开（nginx 504） */
export const UPLOAD_TIMEOUT = '上传超时：图片太大或网络太慢，压缩后再试'
export const SERVER_ERROR = '服务器出错，请稍后再试'

export class HttpError extends Error {
  status: number
  constructor(status: number, message: string = httpErrorMessage(status)) {
    super(message)
    this.name = 'HttpError'
    this.status = status
  }
}

export function httpErrorMessage(status: number, fallback = NETWORK_OR_SERVICE): string {
  if (status === 403) return DEMO_FORBIDDEN
  if (status === 413) return IMAGE_TOO_LARGE
  if (status === 429) return DEMO_QUOTA_FULL
  if (status === 504 || status === 502) return UPLOAD_TIMEOUT
  if (status === 500) return SERVER_ERROR
  return fallback
}

/** 只把状态码翻译成固定文案；不把后端原始 body / 堆栈暴露给用户。 */
export function responseError(response: Response, fallback = NETWORK_OR_SERVICE): HttpError {
  return new HttpError(response.status, httpErrorMessage(response.status, fallback))
}

export function networkError(): HttpError {
  return new HttpError(0, NETWORK_OR_SERVICE)
}

/** 浏览器自己等不下去（AbortController 超时）的文案：和断网分开说，用户才知道该压图 */
export function uploadTimeoutError(): HttpError {
  return new HttpError(0, UPLOAD_TIMEOUT)
}

/** 展示错误时优先用我们已经翻译过的固定文案，未知异常才落到默认文案。 */
export function errorText(error: unknown, fallback = NETWORK_OR_SERVICE): string {
  if (error instanceof HttpError) return error.message
  if (error instanceof Error && [DEMO_FORBIDDEN, IMAGE_TOO_LARGE, NETWORK_OR_SERVICE, DEMO_QUOTA_FULL, UPLOAD_TIMEOUT, SERVER_ERROR].includes(error.message)) return error.message
  return fallback
}
