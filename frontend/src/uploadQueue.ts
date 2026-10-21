export type UploadItem = {
  id: string; file: File; knowledgeBaseId: string
  status: 'waiting' | 'uploading' | 'success' | 'duplicate' | 'failed'
  attempts: number; error: string
}
export type UploadResult = { duplicate?: boolean }
type UploadFailure = { status?: number; message?: string; retryAfter?: number }

function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Paused', 'AbortError')) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, ms)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

export async function submitUploads(items: UploadItem[], send: (item: UploadItem, signal: AbortSignal) => Promise<UploadResult>,
  signal: AbortSignal, sleep = delay) {
  for (const item of items) {
    if (signal.aborted) break
    if (item.status !== 'waiting' && item.status !== 'failed') continue
    item.error = ''
    for (let attempt = 0; attempt < 3 && !signal.aborted; attempt++) {
      item.status = 'uploading'; item.attempts++
      try {
        const result = await send(item, signal)
        item.status = result.duplicate ? 'duplicate' : 'success'
        break
      } catch (error) {
        if (signal.aborted) { item.status = 'waiting'; item.error = '已暂停，可继续提交'; break }
        const failure = error as UploadFailure
        item.error = failure.message || '提交失败'
        if (failure.status === 429 && attempt < 2) {
          const seconds = Math.min(60, Math.max(1, failure.retryAfter || 2 ** (attempt + 1)))
          item.error = `任务额度暂满，${seconds} 秒后重试（${attempt + 1}/2）`
          try { await sleep(seconds * 1000, signal) }
          catch { item.status = 'waiting'; break }
          continue
        }
        item.status = 'failed'
        break
      }
    }
  }
}
