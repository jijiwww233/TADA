import type { LocalImportFailure, LocalImportProgress, LocalImportStatus } from '../localImport/contract'

/** 后端给的失败原因 → 用户看得懂的「为什么 + 怎么改」 */
export function explainImportFailure(reason: string): { why: string; fix: string } {
  // 先认后端 / 代理给的真实原因，别被下面的「太大」泛化掉
  if (/演示版不提供此功能/.test(reason)) return { why: '演示版不提供此功能', fix: '这个共用演示版没有开放该功能，请在本机版使用' }
  if (/20MB|图片太大/.test(reason)) return { why: '图片太大，单张最多 20MB', fix: '换一张小于 20MB 的图片再试' }
  if (/网络或服务出错/.test(reason)) return { why: '网络或服务出错', fix: '检查网络后重新导入这几张' }
  if (/50MB|太大|too large/i.test(reason)) return { why: '文件太大（超过 50MB）', fix: '压缩或截图后再导入' }
  if (/HEIC/i.test(reason)) return { why: 'HEIC 照片读不出来', fix: '在相册里导出为 JPG，或把相机格式改成「兼容性最佳」' }
  if (/空文件/.test(reason)) return { why: '文件是空的', fix: '重新选一次这张图' }
  if (/不支持的图片格式/.test(reason)) return { why: reason, fix: '换成 JPG、PNG 或 WebP 再导入' }
  if (/不是可识别的图片/.test(reason)) return { why: '读不出这是一张图片', fix: '确认文件没损坏，或换成 JPG、PNG' }
  if (/失败|网络|fetch|\(\d{3}\)|（\d{3}）/i.test(reason)) return { why: '上传没成功（网络或服务暂时不可用）', fix: '检查网络后重新导入这几张' }
  return { why: reason, fix: '重新导入试试' }
}

/** 同一原因合并：「2 张失败：文件太大」 */
function groupFailures(failed: LocalImportFailure[]) {
  const groups = new Map<string, { why: string; fix: string; names: string[] }>()
  for (const item of failed) {
    const { why, fix } = explainImportFailure(item.reason)
    const group = groups.get(why) ?? { why, fix, names: [] }
    group.names.push(item.name)
    groups.set(why, group)
  }
  return [...groups.values()]
}

/** 「已收藏 3 张 · 1 张已在库里 · 1 张失败：文件太大」 */
export function importSummary(progress: Pick<LocalImportProgress, 'added' | 'duplicates' | 'failed'>) {
  const groups = groupFailures(progress.failed)
  const parts = [
    progress.added ? `已收藏 ${progress.added} 张` : '',
    progress.duplicates ? `${progress.duplicates} 张已在库里` : '',
    progress.failed.length ? `${progress.failed.length} 张失败：${groups.map(g => g.why).join('、')}` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : '没有导入任何图片'
}

/**
 * 导入结果（第 5 条）：导入开始就出现，导入完不自动消失，等用户看完再关。
 * 进行中：正在导入 x / y 张 + 停止；结束：一行汇总 + 失败原因和改法 + 「查看刚导入的」。
 */
export function ImportResult({ status, progress, cancelled, error, onView, onClose, onCancel }: {
  status: LocalImportStatus
  progress: LocalImportProgress
  cancelled: boolean
  error: string | null
  onView: () => void
  onClose: () => void
  onCancel: () => void
}) {
  if (status === 'idle') return null
  const importing = status === 'importing'
  const groups = groupFailures(progress.failed)
  const nothing = !importing && progress.total === 0
  const tone = importing ? 'is-busy' : progress.failed.length || status === 'error' ? 'is-warn' : 'is-ok'
  return (
    <section className={`ds-import-result ${tone}`} role="status" aria-live="polite" aria-label="导入结果">
      <div className="ds-import-result-main">
        <span className="ds-import-result-dot" aria-hidden="true" />
        <p className="ds-import-result-line">
          {importing
            ? <>正在导入 {progress.processed} / {progress.total} 张{cancelled ? '，这一批传完就停' : '…'}</>
            : nothing ? (error ?? '没有可导入的图片文件')
            : <>{cancelled ? '已停止 · ' : ''}{importSummary(progress)}</>}
        </p>
      </div>
      {!importing && groups.length > 0 && (
        <ul className="ds-import-result-fails">
          {groups.map(group => (
            <li key={group.why}>
              <b>{group.names.length} 张 · {group.why}</b>
              <span>怎么办：{group.fix}</span>
              <small title={group.names.join('、')}>{group.names.slice(0, 3).join('、')}{group.names.length > 3 ? ` 等 ${group.names.length} 个文件` : ''}</small>
            </li>
          ))}
        </ul>
      )}
      <div className="ds-import-result-actions">
        {importing ? (
          <button type="button" className="ds-btn ds-btn--ghost" disabled={cancelled} onClick={onCancel}>停止导入</button>
        ) : (
          <>
            {progress.added > 0 && <button type="button" className="ds-btn ds-btn--primary" onClick={onView}>查看刚导入的</button>}
            <button type="button" className="ds-btn ds-btn--ghost" onClick={onClose}>关闭</button>
          </>
        )}
      </div>
    </section>
  )
}
