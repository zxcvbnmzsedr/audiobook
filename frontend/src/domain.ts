import type { CharacterItem, Chunk, VoiceItem } from './types'
import { formatPercent, formatCount } from './utils'

export type UnknownSpeakerRow = {
  speaker: string
  chapterCount: number
  chapters: string[]
}

export function normalizeSpeakerName(value?: unknown) {
  return String(value ?? '').trim().toLowerCase()
}

export function collectSpeakerNames(item: CharacterItem | VoiceItem) {
  return [
    item.name,
    ...(Array.isArray(item.aliases) ? item.aliases : []),
    ...(Array.isArray(item.raw_speakers) ? item.raw_speakers : []),
    ...(Array.isArray(item.inherited_speakers) ? item.inherited_speakers as string[] : []),
  ]
}

export function matchesSpeaker(item: CharacterItem | VoiceItem, speaker?: string) {
  const key = normalizeSpeakerName(speaker)
  if (!key) return false
  return collectSpeakerNames(item).some((name) => normalizeSpeakerName(name) === key)
}

export function fallbackVoiceForSpeaker(speaker?: string): VoiceItem {
  const name = String(speaker ?? '').trim()
  return {
    name,
    aliases: [],
    raw_speakers: name ? [name] : [],
    line_count: 0,
    char_count: 0,
    source: 'script',
    configured: false,
    voice_config_status: 'missing',
    has_voice_config: false,
    has_custom_voice_config: false,
    has_confirmed_voice_config: false,
    config: {},
  }
}

export type VoiceConfigStatus = 'missing' | 'customized' | 'confirmed'

export function voiceConfigStatus(item: CharacterItem | VoiceItem): VoiceConfigStatus {
  const status = String(item.voice_config_status ?? '')
  if (status === 'confirmed' || status === 'customized' || status === 'missing') {
    return status
  }
  if (item.config?.confirmed === true) {
    return 'confirmed'
  }
  if (item.has_custom_voice_config === true) {
    return 'customized'
  }
  if (item.has_confirmed_voice_config === true || item.configured === true) {
    return 'confirmed'
  }
  return 'missing'
}

export function hasConfirmedVoiceConfig(item: CharacterItem | VoiceItem) {
  return voiceConfigStatus(item) === 'confirmed'
}

export function hasCustomizedVoiceConfig(item: CharacterItem | VoiceItem) {
  return voiceConfigStatus(item) === 'customized'
}

export function voiceConfigLabel(config?: Record<string, unknown>) {
  if (!config || !Object.keys(config).length) return '未设置'
  const type = String(config.type ?? 'custom')
  if (type === 'edge') return 'Edge'
  if (type === 'dashscope') return `DashScope ${String(config.dashscope_voice ?? '')}`.trim()
  if (type === 'volcengine') return `火山 ${String(config.volcengine_speaker ?? '')}`.trim()
  if (type === 'builtin_lora') return `内置 ${String(config.adapter_id ?? '未选')}`
  if (type === 'lora') return `LoRA ${String(config.adapter_id ?? '未选')}`
  if (type === 'clone') return '克隆'
  if (type === 'design') return '设计'
  if (type === 'custom') return String(config.voice ?? '未选择')
  return type
}

export function bookFileUrl(bookId: string | null | undefined, path: unknown) {
  const cleanPath = String(path ?? '').replace(/^\/+/, '')
  if (!bookId || !cleanPath) return ''
  return `/books/${encodeURIComponent(bookId)}/${cleanPath}`
}

export function chunkAudioSrc(chunk: Chunk, bookId?: string | null) {
  return chunk.audio_url ?? bookFileUrl(bookId, chunk.audio_path)
}

export function unknownSpeakersFromIssues(payload: unknown): UnknownSpeakerRow[] {
  if (!payload || typeof payload !== 'object') return []
  const chapters = (payload as Record<string, unknown>).chapters
  if (!chapters || typeof chapters !== 'object') return []

  const bySpeaker = new Map<string, Set<string>>()
  Object.values(chapters as Record<string, Record<string, unknown>>).forEach((chapter) => {
    if (!chapter || typeof chapter !== 'object') return
    const chapterLabel = String(chapter.chapter_title ?? chapter.chapter_id ?? '未命名章节')
    const unknownSpeakers = Array.isArray(chapter.unknown_speakers) ? chapter.unknown_speakers : []
    unknownSpeakers.forEach((value) => {
      const speaker = String(value ?? '').trim()
      if (!speaker) return
      const current = bySpeaker.get(speaker) ?? new Set<string>()
      current.add(chapterLabel)
      bySpeaker.set(speaker, current)
    })
  })

  return [...bySpeaker.entries()]
    .map(([speaker, chaptersForSpeaker]) => ({
      speaker,
      chapterCount: chaptersForSpeaker.size,
      chapters: [...chaptersForSpeaker],
    }))
    .sort((a, b) => b.chapterCount - a.chapterCount || a.speaker.localeCompare(b.speaker, 'zh-CN'))
}

export function parseSeed(value: unknown, fallback: number) {
  const parsed = Number.parseInt(String(value ?? '').trim(), 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function splitList(value: unknown) {
  return String(value ?? '')
    .split(/[、,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function firstDoneIndex<T extends { status?: string }>(rows: T[]) {
  const index = rows.findIndex((row) => row.status === 'done')
  return index >= 0 ? index : 0
}

export function formatCoverageRatio(value: unknown) {
  return value != null ? `，原文覆盖 ${formatPercent(Number(value))}` : ''
}

export function formatEventCount(value: unknown) {
  return formatCount(Number(value) || 0)
}
