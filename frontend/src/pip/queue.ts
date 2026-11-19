export interface TaskQueue {
  run<T>(task: () => Promise<T>): Promise<T>
  pending(): number
  active(): number
}

/** Small FIFO queue with a hard concurrency limit (image decode / texture builds). */
export function createTaskQueue(concurrency: number): TaskQueue {
  const limit = Math.max(1, Math.floor(concurrency))
  let running = 0
  const waiting: (() => void)[] = []
  const next = () => {
    if (running >= limit) return
    const start = waiting.shift()
    if (start) start()
  }
  return {
    run<T>(task: () => Promise<T>): Promise<T> {
      return new Promise<T>((resolve, reject) => {
        const start = () => {
          running += 1
          Promise.resolve()
            .then(task)
            .then(resolve, reject)
            .finally(() => {
              running -= 1
              next()
            })
        }
        waiting.push(start)
        next()
      })
    },
    pending: () => waiting.length,
    active: () => running,
  }
}
