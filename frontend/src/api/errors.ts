/** 全站统一的 HTTP 错误文案：403/413 说真实原因，其余才归为网络或服务出错。 */
export const DEMO_FORBIDDEN = '演示版不提供此功能'
export const IMAGE_TOO_LARGE = '图片太大，单张最多 20MB'
export const NETWORK_OR_SERVICE = '网络或服务出错，请稍后重试'

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
  return fallback
}

/** 只把状态码翻译成固定文案；不把后端原始 body / 堆栈暴露给用户。 */
export function responseError(response: Response, fallback = NETWORK_OR_SERVICE): HttpError {
  return new HttpError(response.status, httpErrorMessage(response.status, fallback))
}

export function networkError(): HttpError {
  return new HttpError(0, NETWORK_OR_SERVICE)
}

/** 展示错误时优先用我们已经翻译过的固定文案，未知异常才落到默认文案。 */
export function errorText(error: unknown, fallback = NETWORK_OR_SERVICE): string {
  if (error instanceof HttpError) return error.message
  if (error instanceof Error && [DEMO_FORBIDDEN, IMAGE_TOO_LARGE, NETWORK_OR_SERVICE].includes(error.message)) return error.message
  return fallback
}

