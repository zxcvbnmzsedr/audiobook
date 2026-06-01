import { formatCount, formatPercent } from './utils'

export type ScriptStreamEntry = {
  speaker?: string
  text?: string
  instruct?: string
}

export type ScriptStageMetrics = Partial<Record<'characters' | 'script' | 'memory' | 'review', {
  calls: number
  elapsedMs: number
  maxElapsedMs: number
  lastElapsedMs: number
}>>

export type ScriptStreamState = {
  connected: boolean
  status: 'idle' | 'running' | 'success' | 'warning' | 'error'
  stage: string
  current: number
  total: number
  logs: string[]
  entries: ScriptStreamEntry[]
  activeStage?: 'split' | 'characters' | 'script' | 'memory' | 'review' | 'done'
  currentChapterId?: string
  currentChapterTitle?: string
  currentChapterCharCount?: number
  currentAttempt?: number
  currentLabel?: string
  retryCount: number
  failedBatches?: number
  inputTokens: number
  outputTokens: number
  totalTokens: number
  cacheReadTokens: number
  llmElapsedMs: number
  llmCallCount: number
  stageMetrics: ScriptStageMetrics
  currentLlmStartedAt?: number
  currentLlmStage?: 'characters' | 'script' | 'memory' | 'review'
  lastEventAt?: number
  lastLlmAt?: number
  stageStartedAt?: number
  mode?: string
  reuseCharacterBook?: boolean
  enableChapterMemory?: boolean
  task?: 'script' | 'review'
}

export const scriptEventTypes = [
  'init',
  'chapter_start',
  'character_book_done',
  'character_book_skipped',
  'llm_attempt',
  'llm_stream',
  'llm_usage',
  'llm_retry',
  'chapter_memory_done',
  'chapter_memory_error',
  'chapter_done',
  'story_bible_done',
  'story_bible_error',
  'interrupted',
  'rollback_done',
  'rollback_error',
  'done',
  'cancelled',
  'error',
  'stream_end',
]

export const reviewEventTypes = [
  'init',
  'review_batch_start',
  'llm_attempt',
  'llm_stream',
  'llm_usage',
  'llm_retry',
  'review_batch_done',
  'done',
  'cancelled',
  'error',
  'stream_end',
]

export const terminalScriptEvents = new Set(['done', 'cancelled', 'error', 'interrupted', 'rollback_done', 'rollback_error', 'stream_end'])

export function emptyScriptStreamState(): ScriptStreamState {
  return {
    connected: false,
    status: 'idle',
    stage: '',
    current: 0,
    total: 0,
    logs: [],
    entries: [],
    retryCount: 0,
    inputTokens: 0,
    outputTokens: 0,
    totalTokens: 0,
    cacheReadTokens: 0,
    llmElapsedMs: 0,
    llmCallCount: 0,
    stageMetrics: {},
  }
}

export function appendStreamLog(logs: string[], line?: string) {
  if (!line) return logs
  return [...logs, line].slice(-80)
}

function scriptEventNumber(value: unknown, fallback = 0) {
  const numberValue = Number(value)
  return Number.isFinite(numberValue) ? numberValue : fallback
}

function scriptEventText(data: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = data[key]
    if (value !== undefined && value !== null && String(value).trim()) return String(value)
  }
  return ''
}

function llmStageFromLabel(label: string, fallback?: ScriptStreamState['activeStage']) {
  if (label.startsWith('review_')) return 'review'
  if (label.startsWith('tagged_')) return 'script'
  if (label.startsWith('json_object')) return fallback ?? 'characters'
  return fallback
}

function llmStageFromEvent(data: Record<string, unknown>, fallback?: ScriptStreamState['activeStage']) {
  const stage = String(data.stage || '')
  if (stage === 'review') return 'review'
  if (stage === 'tagged') return 'script'
  if (stage === 'memory') return 'memory'
  if (stage === 'characters') return 'characters'
  return llmStageFromLabel(scriptEventText(data, 'label'), fallback)
}

function stageLabel(stage?: ScriptStreamState['activeStage']) {
  if (stage === 'script') return '脚本生成'
  if (stage === 'memory') return '章节记忆'
  if (stage === 'review') return '脚本审校'
  if (stage === 'characters') return '人物池分析'
  if (stage === 'split') return '章节拆分'
  return '任务'
}

function addStageMetric(metrics: ScriptStageMetrics, stage: ScriptStreamState['activeStage'], elapsedMs: number) {
  if (!stage || !['characters', 'script', 'memory', 'review'].includes(stage) || elapsedMs <= 0) return metrics
  const key = stage as keyof ScriptStageMetrics
  const current = metrics[key] ?? { calls: 0, elapsedMs: 0, maxElapsedMs: 0, lastElapsedMs: 0 }
  return {
    ...metrics,
    [key]: {
      calls: current.calls + 1,
      elapsedMs: current.elapsedMs + elapsedMs,
      maxElapsedMs: Math.max(current.maxElapsedMs, elapsedMs),
      lastElapsedMs: elapsedMs,
    },
  }
}

export function reduceScriptStreamState(current: ScriptStreamState, event: { type?: string; data?: unknown }): ScriptStreamState {
  const type = event.type || 'message'
  const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {}
  const now = Date.now()
  const mode = typeof data.mode === 'string' ? data.mode : current.mode
  const reuseCharacterBook = typeof data.reuse_character_book === 'boolean' ? data.reuse_character_book : current.reuseCharacterBook
  const enableChapterMemory = typeof data.enable_chapter_memory === 'boolean' ? data.enable_chapter_memory : current.enableChapterMemory
  const isCharacterMode = mode === 'characters'
  const isReuseCharacterBook = Boolean(reuseCharacterBook && !isCharacterMode)
  const chapterLabel = scriptEventText(data, 'title', 'chapter_id')
  const nextBase = {
    ...current,
    connected: type !== 'stream_end',
    mode,
    reuseCharacterBook,
    enableChapterMemory,
    lastEventAt: now,
  }

  if (type === 'init') {
    if (data.task === 'review') {
      return {
        ...nextBase,
        task: 'review',
        status: 'running',
        activeStage: 'review',
        stage: `脚本审校：${formatCount(scriptEventNumber(data.entry_count))} 条，${formatCount(scriptEventNumber(data.batch_count))} 批，批量 ${formatCount(scriptEventNumber(data.batch_size))}，模型 ${String(data.model ?? '-')}`,
        current: 0,
        total: scriptEventNumber(data.batch_count),
        entries: [],
        stageStartedAt: now,
        logs: appendStreamLog(current.logs, `Review init: ${formatCount(scriptEventNumber(data.entry_count))} entries, ${formatCount(scriptEventNumber(data.batch_count))} batches`),
      }
    }
    const label = isCharacterMode ? '人物池分析' : isReuseCharacterBook ? '复用人物池生成脚本' : '脚本生成'
    return {
      ...nextBase,
      status: 'running',
      activeStage: 'split',
      stage: `${label}：共 ${formatCount(scriptEventNumber(data.char_count))} 字符，${formatCount(scriptEventNumber(data.chapter_count))} 章，模型 ${String(data.model ?? '-')}`,
      current: 0,
      total: scriptEventNumber(data.chapter_count),
      entries: [],
      logs: appendStreamLog(current.logs, `初始化：${scriptEventText(data, 'input_file') || '已启动'}`),
    }
  }

  if (type === 'chapter_start') {
    const index = scriptEventNumber(data.index, 1)
    const total = scriptEventNumber(data.total)
    return {
      ...nextBase,
      status: 'running',
      activeStage: isCharacterMode ? 'characters' : isReuseCharacterBook ? 'script' : 'characters',
      stage: `${isCharacterMode ? '分析' : isReuseCharacterBook ? '标注' : '处理'}第 ${index}/${total || '?'} 章：${chapterLabel || '未命名章节'}`,
      current: Math.max(index - 1, 0),
      total,
      currentChapterId: scriptEventText(data, 'chapter_id'),
      currentChapterTitle: chapterLabel,
      currentChapterCharCount: scriptEventNumber(data.char_count),
      currentAttempt: undefined,
      currentLabel: undefined,
      currentLlmStartedAt: undefined,
      currentLlmStage: undefined,
      stageStartedAt: now,
      logs: appendStreamLog(current.logs, `Chapter ${index}/${total || '?'}: ${chapterLabel || '未命名章节'} (${formatCount(scriptEventNumber(data.char_count))} chars)`),
    }
  }

  if (type === 'review_batch_start') {
    const batch = scriptEventNumber(data.batch)
    const total = scriptEventNumber(data.total)
    return {
      ...nextBase,
      task: 'review',
      status: 'running',
      activeStage: 'review',
      stage: `审校第 ${batch}/${total || '?'} 批：${formatCount(scriptEventNumber(data.entries))} 条脚本`,
      current: Math.max(batch - 1, 0),
      total,
      currentChapterId: undefined,
      currentChapterTitle: `第 ${batch}/${total || '?'} 批`,
      currentChapterCharCount: scriptEventNumber(data.entries),
      currentAttempt: undefined,
      currentLabel: undefined,
      currentLlmStartedAt: undefined,
      currentLlmStage: undefined,
      stageStartedAt: now,
      logs: appendStreamLog(current.logs, `Review batch ${batch}/${total || '?'}: ${formatCount(scriptEventNumber(data.entries))} entries`),
    }
  }

  if (type === 'llm_attempt') {
    const label = scriptEventText(data, 'label')
    const attempt = scriptEventNumber(data.attempt)
    const activeStage = llmStageFromEvent(data, current.activeStage) ?? 'characters'
    return {
      ...nextBase,
      status: 'running',
      activeStage,
      currentAttempt: attempt,
      currentLabel: label || current.currentLabel,
      currentLlmStartedAt: now,
      currentLlmStage: activeStage && ['characters', 'script', 'memory', 'review'].includes(activeStage) ? activeStage as ScriptStreamState['currentLlmStage'] : undefined,
      lastLlmAt: now,
      stageStartedAt: current.stageStartedAt ?? now,
      stage: `${stageLabel(activeStage)}：等待 LLM 响应，第 ${attempt || 1}/3 次`,
      logs: appendStreamLog(current.logs, `${stageLabel(activeStage)} LLM attempt ${attempt || 1}/3`),
    }
  }

  if (type === 'character_book_done' || type === 'character_book_skipped') {
    const reused = type === 'character_book_skipped'
    return {
      ...nextBase,
      status: 'running',
      activeStage: isCharacterMode ? 'characters' : 'script',
      currentAttempt: undefined,
      currentLabel: undefined,
      currentLlmStartedAt: undefined,
      currentLlmStage: undefined,
      stageStartedAt: now,
      stage: reused ? `复用当前人物池：${formatCount(scriptEventNumber(data.characters))} 个角色` : `人物池已更新：${formatCount(scriptEventNumber(data.characters))} 个角色`,
      logs: appendStreamLog(current.logs, reused ? `Character book reused: ${formatCount(scriptEventNumber(data.characters))} characters` : `Character book updated: ${formatCount(scriptEventNumber(data.characters))} characters`),
    }
  }

  if (type === 'llm_stream') {
    const label = scriptEventText(data, 'label') || 'stream'
    const activeStage = llmStageFromLabel(label, current.activeStage)
    const done = Boolean(data.done)
    const elapsedMs = done ? scriptEventNumber(data.elapsed_ms) : 0
    const line = data.done
      ? `[LLM:${label}] done (${formatCount(scriptEventNumber(data.chars))} chars${elapsedMs ? `, ${(elapsedMs / 1000).toFixed(1)}s` : ''})`
      : data.text ? `[LLM:${label}] ${String(data.text)}` : ''
    return {
      ...nextBase,
      activeStage,
      lastLlmAt: now,
      currentLabel: label,
      llmElapsedMs: current.llmElapsedMs + elapsedMs,
      llmCallCount: current.llmCallCount + (done ? 1 : 0),
      stageMetrics: addStageMetric(current.stageMetrics, activeStage, elapsedMs),
      currentLlmStartedAt: done ? undefined : current.currentLlmStartedAt,
      currentLlmStage: done ? undefined : current.currentLlmStage,
      stage: data.done
        ? current.stage
        : `${stageLabel(activeStage)}：LLM 流式输出中`,
      logs: appendStreamLog(current.logs, line),
    }
  }

  if (type === 'llm_usage') {
    const label = scriptEventText(data, 'label')
    const activeStage = llmStageFromEvent(data, current.activeStage)
    return {
      ...nextBase,
      activeStage,
      currentLabel: label || current.currentLabel,
      inputTokens: current.inputTokens + scriptEventNumber(data.input_tokens),
      outputTokens: current.outputTokens + scriptEventNumber(data.output_tokens),
      totalTokens: current.totalTokens + scriptEventNumber(data.total_tokens),
      cacheReadTokens: current.cacheReadTokens + scriptEventNumber(data.cache_read),
      logs: appendStreamLog(current.logs, `LLM usage ${label || '-'}: in ${formatCount(scriptEventNumber(data.input_tokens))}, out ${formatCount(scriptEventNumber(data.output_tokens))}, cache ${formatCount(scriptEventNumber(data.cache_read))}`),
    }
  }

  if (type === 'llm_retry') {
    const label = scriptEventText(data, 'label')
    const activeStage = llmStageFromEvent(data, current.activeStage)
    return {
      ...nextBase,
      activeStage,
      status: 'warning',
      retryCount: current.retryCount + 1,
      currentAttempt: scriptEventNumber(data.attempt),
      currentLabel: label || current.currentLabel,
      currentLlmStartedAt: undefined,
      currentLlmStage: undefined,
      stage: `${stageLabel(activeStage)}：输出未通过解析，准备重试`,
      logs: appendStreamLog(current.logs, `LLM retry ${label || '-'}: ${scriptEventText(data, 'error') || JSON.stringify(data.issues ?? '')}`),
    }
  }

  if (type === 'chapter_memory_done' || type === 'chapter_memory_error') {
    const failed = type === 'chapter_memory_error'
    return {
      ...nextBase,
      status: failed ? 'warning' : current.status,
      activeStage: 'memory',
      currentAttempt: undefined,
      currentLabel: undefined,
      currentLlmStartedAt: undefined,
      currentLlmStage: undefined,
      stage: failed ? `章节记忆生成失败：${chapterLabel || '未命名章节'}` : `章节记忆已更新：${chapterLabel || '未命名章节'}`,
      logs: appendStreamLog(current.logs, failed ? `Chapter memory error: ${scriptEventText(data, 'message')}` : `Chapter memory updated: ${chapterLabel || '未命名章节'}`),
    }
  }

  if (type === 'chapter_done') {
    const index = scriptEventNumber(data.index)
    const total = scriptEventNumber(data.total)
    const coverage = data.source_coverage_ratio != null ? `，原文覆盖 ${formatPercent(scriptEventNumber(data.source_coverage_ratio))}` : ''
    const sample = Array.isArray(data.sample) ? data.sample as ScriptStreamEntry[] : []
    return {
      ...nextBase,
      status: 'running',
      activeStage: current.enableChapterMemory ? 'memory' : 'script',
      stage: isCharacterMode
        ? `第 ${index}/${total || '?'} 章人物池分析完成：${formatCount(scriptEventNumber(data.characters))} 个角色`
        : `第 ${index}/${total || '?'} 章完成：${formatCount(scriptEventNumber(data.entries))} 条，问题 ${formatCount(scriptEventNumber(data.issue_count))} 个，累计 ${formatCount(scriptEventNumber(data.total_entries))} 条${coverage}`,
      current: index,
      total,
      currentAttempt: undefined,
      currentLabel: undefined,
      currentLlmStartedAt: undefined,
      currentLlmStage: undefined,
      stageStartedAt: now,
      entries: [...current.entries, ...sample].slice(-20),
      logs: appendStreamLog(current.logs, `Chapter ${index}/${total || '?'} done: ${formatCount(scriptEventNumber(data.entries))} entries, ${formatCount(scriptEventNumber(data.issue_count))} issues${coverage}`),
    }
  }

  if (type === 'review_batch_done') {
    const batch = scriptEventNumber(data.batch)
    const total = scriptEventNumber(data.total)
    const fallback = Boolean(data.fallback)
    const changes = scriptEventNumber(data.total_changes)
    return {
      ...nextBase,
      task: 'review',
      status: fallback ? 'warning' : 'running',
      activeStage: 'review',
      stage: fallback
        ? `审校第 ${batch}/${total || '?'} 批失败，已保留原条目`
        : `审校第 ${batch}/${total || '?'} 批完成：${formatCount(changes)} 处变化`,
      current: batch,
      total,
      currentAttempt: undefined,
      currentLabel: undefined,
      currentLlmStartedAt: undefined,
      currentLlmStage: undefined,
      failedBatches: scriptEventNumber(data.failed_batches, current.failedBatches ?? 0),
      stageStartedAt: now,
      logs: appendStreamLog(current.logs, fallback
        ? `Review batch ${batch}/${total || '?'} fallback: ${scriptEventText(data, 'error') || 'failed'}`
        : `Review batch ${batch}/${total || '?'} done: ${formatCount(changes)} changes`),
    }
  }

  if (type === 'story_bible_done' || type === 'story_bible_error') {
    const failed = type === 'story_bible_error'
    return {
      ...nextBase,
      status: failed ? 'warning' : current.status,
      stage: failed ? `Story Bible 建立失败：${scriptEventText(data, 'message') || '未知错误'}` : `Story Bible 已建立：${formatCount(scriptEventNumber(data.chapter_count))} 章，${formatCount(scriptEventNumber(data.character_count))} 个角色`,
      logs: appendStreamLog(current.logs, failed ? `Story Bible error: ${scriptEventText(data, 'message')}` : `Story Bible done: ${scriptEventText(data, 'path') || 'story_bible.json'}`),
    }
  }

  if (type === 'done') {
    if (data.task === 'review' || current.task === 'review') {
      return {
        ...nextBase,
        connected: false,
        task: 'review',
        status: 'success',
        activeStage: 'done',
        stage: `审校完成：${formatCount(scriptEventNumber(data.input_entries))} -> ${formatCount(scriptEventNumber(data.output_entries))} 条，变化 ${formatCount(scriptEventNumber(data.total_changes))} 处`,
        current: current.total || current.current,
        failedBatches: scriptEventNumber(data.batches_failed, current.failedBatches ?? 0),
        logs: appendStreamLog(current.logs, `Review done: ${formatCount(scriptEventNumber(data.total_changes))} changes, failed batches ${formatCount(scriptEventNumber(data.batches_failed))}`),
      }
    }
    const totalEntries = scriptEventNumber(data.total_entries)
    const speakerCount = Array.isArray(data.speakers) ? data.speakers.length : 0
    return {
      ...nextBase,
      connected: false,
      status: 'success',
      activeStage: 'done',
      stage: isCharacterMode ? `人物池分析完成：${formatCount(scriptEventNumber(data.characters))} 个角色` : `生成完成：${formatCount(totalEntries)} 条，${formatCount(speakerCount)} 个说话人`,
      current: current.total || current.current,
      logs: appendStreamLog(current.logs, isCharacterMode ? '人物池分析完成。' : `生成完成：${formatCount(totalEntries)} 条目，${formatCount(speakerCount)} 个说话人。`),
    }
  }

  if (type === 'cancelled' || type === 'interrupted') {
    const reason = scriptEventText(data, 'reason', 'message')
    return {
      ...nextBase,
      connected: false,
      status: 'warning',
      stage: type === 'cancelled' ? `已取消，保留 ${formatCount(scriptEventNumber(data.total_entries))} 条已生成条目` : `生成中断：${reason || '后台任务中断'}。已完成章节已保留。`,
      logs: appendStreamLog(current.logs, type === 'cancelled' ? '脚本任务已取消。' : `Interrupted: ${reason || '后台任务中断'}`),
    }
  }

  if (type === 'rollback_done' || type === 'rollback_error' || type === 'error') {
    const failedRollback = type === 'rollback_error'
    return {
      ...nextBase,
      connected: false,
      status: type === 'rollback_done' ? 'warning' : 'error',
      stage: type === 'rollback_done'
        ? `生成失败，已回滚 ${formatCount(Array.isArray(data.restored_files) ? data.restored_files.length : 0)} 个文件。`
        : failedRollback ? `生成失败，且快照回滚失败：${scriptEventText(data, 'message') || '未知错误'}` : `脚本任务失败：${scriptEventText(data, 'message') || '查看日志'}`,
      logs: appendStreamLog(current.logs, `${type}: ${JSON.stringify(data)}`),
    }
  }

  if (type === 'stream_end') {
    return {
      ...nextBase,
      connected: false,
      logs: appendStreamLog(current.logs, '事件流已结束。'),
    }
  }

  return {
    ...nextBase,
    logs: appendStreamLog(current.logs, JSON.stringify({ type, data })),
  }
}
