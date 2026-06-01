import type { ScriptProgressItem } from './types'

export function formatDate(value?: string) {
  if (!value) return '未知'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function formatCount(value?: number) {
  if (value === undefined || value === null || Number.isNaN(value)) return '0'
  return new Intl.NumberFormat('zh-CN').format(value)
}

export function formatPercent(value?: number) {
  if (value === undefined || value === null || Number.isNaN(value)) return '-'
  return `${Math.round(value * 100)}%`
}

export function summarizeStatus(status?: string) {
  const key = String(status ?? '').toLowerCase()
  if (!key) return '未知'
  const map: Record<string, string> = {
    done: '已完成',
    complete: '已完成',
    completed: '已完成',
    pending: '待处理',
    missing: '缺失',
    running: '运行中',
    generating: '生成中',
    failed: '失败',
    error: '失败',
    cancelled: '已取消',
    interrupted: '已中断',
  }
  return map[key] ?? status ?? '未知'
}

export function normalizeScriptProgress(payload: unknown): ScriptProgressItem[] {
  if (!payload || typeof payload !== 'object') return []
  const record = payload as Record<string, unknown>
  const candidates = [
    record.chapters,
    record.items,
    record.progress,
    record.chapter_progress,
  ]
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) return candidate as ScriptProgressItem[]
  }
  if (record.chapters && typeof record.chapters === 'object') {
    return Object.values(record.chapters as Record<string, ScriptProgressItem>)
  }
  return []
}

export function statusColor(status?: string) {
  const key = String(status ?? '').toLowerCase()
  if (['done', 'complete', 'completed'].includes(key)) return 'success'
  if (['running', 'generating'].includes(key)) return 'processing'
  if (['failed', 'error'].includes(key)) return 'error'
  if (['cancelled', 'interrupted', 'missing'].includes(key)) return 'warning'
  return 'default'
}
