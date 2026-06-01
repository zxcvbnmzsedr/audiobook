import type {
  AppConfig,
  BooksResponse,
  Chapter,
  ChaptersManifest,
  ChapterTtsProgressResponse,
  CharactersResponse,
  Chunk,
  ChunkDeleteResponse,
  CloneVoice,
  DatasetProject,
  DatasetStatus,
  DesignedVoice,
  EdgeVoice,
  LoraDataset,
  LoraModel,
  ModulesResponse,
  SavedScript,
  TaskStatus,
  TaggedScriptResponse,
  VoiceConfigItem,
  VoiceItem,
  VolcengineVoicesResponse,
} from './types'

type RequestOptions = {
  method?: string
  body?: unknown
  headers?: HeadersInit
}

export type SpeakerSortOptions = {
  sortBy?: 'line_count' | 'char_count'
  sortOrder?: 'asc' | 'desc'
}

function buildQuery(params: Record<string, string | undefined>) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value)
  })
  const value = query.toString()
  return value ? `?${value}` : ''
}

async function parseError(response: Response) {
  try {
    const body = await response.json()
    if (body?.detail) return String(body.detail)
    return JSON.stringify(body)
  } catch {
    return response.statusText || `HTTP ${response.status}`
  }
}

export async function request<T>(url: string, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const init: RequestInit = {
    method: options.method ?? 'GET',
    headers,
  }

  if (options.body instanceof FormData) {
    init.body = options.body
  } else if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    init.body = JSON.stringify(options.body)
  }

  const response = await fetch(url, init)
  if (!response.ok) {
    throw new Error(await parseError(response))
  }

  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    return undefined as T
  }
  return response.json() as Promise<T>
}

export const api = {
  books: () => request<BooksResponse>('/api/books'),
  currentBook: () => request<Partial<BooksResponse['books'][number]>>('/api/books/current'),
  createBook: (title: string) => request<{ status: string; book: BooksResponse['books'][number] }>('/api/books', {
    method: 'POST',
    body: { title },
  }),
  selectBook: (bookId: string) => request<{ status: string; book: BooksResponse['books'][number] }>('/api/books/select', {
    method: 'POST',
    body: { book_id: bookId },
  }),
  deleteBook: (bookId: string) => request<{ status: string; current_book_id?: string | null }>(`/api/books/${encodeURIComponent(bookId)}`, {
    method: 'DELETE',
  }),
  exportBookConfigUrl: (bookId: string) => `/api/books/${encodeURIComponent(bookId)}/export_config`,
  importBookConfig: (bookId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string; file_count?: number }>(`/api/books/${encodeURIComponent(bookId)}/import_config`, {
      method: 'POST',
      body: form,
    })
  },

  config: () => request<AppConfig>('/api/config'),
  saveConfig: (config: AppConfig) => request<{ status: string }>('/api/config', {
    method: 'POST',
    body: config,
  }),
  defaultPrompts: () => request<Record<string, string>>('/api/default_prompts'),
  desktop: () => request<Record<string, unknown>>('/api/desktop'),
  modules: () => request<ModulesResponse>('/api/modules'),
  installModule: (moduleId: string) => request<{ status: string; module_id: string }>(`/api/modules/${encodeURIComponent(moduleId)}/install`, {
    method: 'POST',
  }),
  repairModule: (moduleId: string) => request<{ status: string; module_id: string }>(`/api/modules/${encodeURIComponent(moduleId)}/repair`, {
    method: 'POST',
  }),
  cancelModuleInstall: () => request<{ status: string; module_id?: string }>('/api/modules/install/cancel', {
    method: 'POST',
  }),

  uploadSource: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ filename: string; path: string; chapters: ChaptersManifest }>('/api/upload', {
      method: 'POST',
      body: form,
    })
  },
  chapters: () => request<ChaptersManifest>('/api/chapters'),
  chapter: (chapterId: string) => request<Chapter>(`/api/chapters/${encodeURIComponent(chapterId)}`),
  updateChapter: (chapterId: string, body: { title?: string; content?: string }) =>
    request(`/api/chapters/${encodeURIComponent(chapterId)}`, { method: 'POST', body }),
  splitChapter: (chapterId: string, body: { split_at?: number; title?: string; new_title?: string; content_before?: string; content_after?: string }) =>
    request(`/api/chapters/${encodeURIComponent(chapterId)}/split`, { method: 'POST', body }),
  mergeChapterNext: (chapterId: string, body: { title?: string; content?: string }) =>
    request(`/api/chapters/${encodeURIComponent(chapterId)}/merge_next`, { method: 'POST', body }),
  resplitChapters: (body: { dry_run?: boolean; confirm_invalidate?: boolean } = { dry_run: true }) => request('/api/chapters/resplit', {
    method: 'POST',
    body,
  }),
  appendChapters: (file: File, dryRun = true) => {
    const form = new FormData()
    form.append('file', file)
    form.append('dry_run', dryRun ? 'true' : 'false')
    return request('/api/chapters/append', { method: 'POST', body: form })
  },
  chapterProgress: () => request<ChapterTtsProgressResponse>('/api/chapters/progress'),
  chapterAudiobookUrl: (chapterId: string) => `/api/chapters/${encodeURIComponent(chapterId)}/audiobook`,

  scriptProgress: () => request<Record<string, unknown> | { chapters?: unknown[] }>('/api/script_progress'),
  scriptIssues: () => request<Record<string, unknown>>('/api/script_issues'),
  scriptIssue: (chapterId: string) => request<Record<string, unknown>>(`/api/script_issues?chapter_id=${encodeURIComponent(chapterId)}`),
  chapterMemory: (chapterId?: string) => {
    const query = chapterId ? `?chapter_id=${encodeURIComponent(chapterId)}` : ''
    return request<Record<string, unknown>>(`/api/chapter_memory${query}`)
  },
  scriptOutputs: () => request<Record<string, unknown>>('/api/script_outputs'),
  storyBible: () => request<Record<string, unknown>>('/api/story_bible'),
  rebuildStoryBible: () => request<Record<string, unknown>>('/api/story_bible/rebuild', { method: 'POST' }),
  scriptActionItems: () => request<Record<string, unknown>>('/api/script_action_items'),
  scriptGenerationSnapshot: () => request<Record<string, unknown>>('/api/script_generation_snapshot'),
  reviewScript: (body: Record<string, unknown>) => request<{ status: string }>('/api/review_script', {
    method: 'POST',
    body,
  }),
  annotatedScript: () => request<Record<string, unknown>[] | unknown[]>('/api/annotated_script'),
  taggedScript: (chapterId?: string) => {
    const query = chapterId ? `?chapter_id=${encodeURIComponent(chapterId)}` : ''
    return request<TaggedScriptResponse>(`/api/annotated_script/tagged${query}`)
  },
  importTaggedScript: (body: {
    content: string
    default_instruct?: string
    chapter_id?: string
    replace_scope?: 'all' | 'chapter'
    dry_run?: boolean
  }) => request<TaggedScriptResponse>('/api/annotated_script/tagged', {
    method: 'POST',
    body,
  }),
  generateScript: (body: Record<string, unknown>) => request<{ status: string }>('/api/generate_script', {
    method: 'POST',
    body,
  }),
  cancelScript: () => request<{ status: string }>('/api/cancel_script', { method: 'POST' }),

  characters: (options: SpeakerSortOptions = {}) => request<CharactersResponse>(`/api/characters${buildQuery({
    sort_by: options.sortBy,
    sort_order: options.sortOrder,
  })}`),
  saveCharacters: (body: Record<string, unknown>) => request('/api/characters', { method: 'POST', body }),
  compactCharacters: () => request<Record<string, unknown>>('/api/characters/compact', { method: 'POST' }),
  importCharacters: (body: Record<string, unknown>) => request<Record<string, unknown>>('/api/characters/import', {
    method: 'POST',
    body,
  }),
  voices: (options: SpeakerSortOptions = {}) => request<VoiceItem[]>(`/api/voices${buildQuery({
    sort_by: options.sortBy,
    sort_order: options.sortOrder,
  })}`),
  saveVoiceConfig: (config: Record<string, VoiceConfigItem>) => request('/api/save_voice_config', {
    method: 'POST',
    body: config,
  }),
  edgeVoices: () => request<EdgeVoice[]>('/api/edge_voices'),
  volcengineVoices: (refresh = false) => request<VolcengineVoicesResponse>(`/api/volcengine/voices${refresh ? '?refresh=true' : ''}`),
  designedVoices: () => request<DesignedVoice[]>('/api/voice_design/list'),
  voicePreview: (body: Record<string, unknown>) => request<{ status: string; audio_url: string }>('/api/voice/preview', {
    method: 'POST',
    body,
  }),
  voiceDesignPreview: (body: Record<string, unknown>) => request<{ status: string; audio_url: string }>('/api/voice_design/preview', {
    method: 'POST',
    body,
  }),
  saveDesignedVoice: (body: { name: string; description: string; sample_text: string; preview_file: string }) =>
    request<{ status: string; voice_id: string }>('/api/voice_design/save', {
      method: 'POST',
      body,
    }),
  deleteDesignedVoice: (voiceId: string) => request(`/api/voice_design/${encodeURIComponent(voiceId)}`, {
    method: 'DELETE',
  }),
  cloneVoices: () => request<CloneVoice[]>('/api/clone_voices/list'),
  uploadCloneVoice: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string; voice_id: string; filename: string }>('/api/clone_voices/upload', {
      method: 'POST',
      body: form,
    })
  },
  deleteCloneVoice: (voiceId: string) => request(`/api/clone_voices/${encodeURIComponent(voiceId)}`, {
    method: 'DELETE',
  }),

  chunks: () => request<Chunk[]>('/api/chunks'),
  updateChunk: (index: number, body: Record<string, unknown>) => request<Chunk>(`/api/chunks/${index}`, {
    method: 'POST',
    body,
  }),
  insertChunk: (index: number) => request(`/api/chunks/${index}/insert`, { method: 'POST' }),
  deleteChunk: (index: number) => request<ChunkDeleteResponse>(`/api/chunks/${index}`, { method: 'DELETE' }),
  restoreChunk: (body: { chunk: Record<string, unknown>; at_index: number }) => request<ChunkDeleteResponse>('/api/chunks/restore', {
    method: 'POST',
    body,
  }),
  generateChunk: (index: number) => request(`/api/chunks/${index}/generate`, { method: 'POST' }),
  renderPlan: (body: Record<string, unknown>) => request<Record<string, unknown>>('/api/render_plan', {
    method: 'POST',
    body,
  }),
  generateBatch: (body: Record<string, unknown>) => request<Record<string, unknown>>('/api/generate_batch', {
    method: 'POST',
    body,
  }),
  generateBatchFast: (body: Record<string, unknown>) => request<Record<string, unknown>>('/api/generate_batch_fast', {
    method: 'POST',
    body,
  }),
  cancelAudio: () => request('/api/cancel_audio', { method: 'POST' }),
  mergeAudio: () => request('/api/merge', { method: 'POST' }),
  mergeChapterAudio: (chapterId: string) => request<{ status: string; audio_url?: string; filename?: string }>(`/api/chapters/${encodeURIComponent(chapterId)}/merge_audio`, {
    method: 'POST',
  }),
  exportAudacity: () => request('/api/export_audacity', { method: 'POST' }),
  exportM4b: (body: Record<string, unknown>) => request('/api/merge_m4b', {
    method: 'POST',
    body,
  }),
  uploadM4bCover: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string; path: string }>('/api/m4b_cover', {
      method: 'POST',
      body: form,
    })
  },
  deleteM4bCover: () => request('/api/m4b_cover', { method: 'DELETE' }),

  scripts: () => request<SavedScript[]>('/api/scripts'),
  saveScript: (name: string) => request('/api/scripts/save', { method: 'POST', body: { name } }),
  loadScript: (name: string) => request('/api/scripts/load', { method: 'POST', body: { name } }),
  deleteScript: (name: string) => request(`/api/scripts/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  taskStatus: (task: string) => request<TaskStatus>(`/api/status/${encodeURIComponent(task)}`),

  loraDatasets: () => request<LoraDataset[]>('/api/lora/datasets'),
  uploadLoraDataset: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ status: string; dataset_id: string; sample_count: number }>('/api/lora/upload_dataset', {
      method: 'POST',
      body: form,
    })
  },
  deleteLoraDataset: (datasetId: string) => request(`/api/lora/datasets/${encodeURIComponent(datasetId)}`, {
    method: 'DELETE',
  }),
  loraModels: () => request<LoraModel[]>('/api/lora/models'),
  trainLora: (body: Record<string, unknown>) => request('/api/lora/train', {
    method: 'POST',
    body,
  }),
  generateLoraDataset: (body: Record<string, unknown>) => request('/api/lora/generate_dataset', {
    method: 'POST',
    body,
  }),
  deleteLoraModel: (adapterId: string) => request(`/api/lora/models/${encodeURIComponent(adapterId)}`, {
    method: 'DELETE',
  }),
  downloadLoraModel: (adapterId: string) => request(`/api/lora/download/${encodeURIComponent(adapterId)}`, {
    method: 'POST',
  }),
  previewLoraModel: (adapterId: string) => request<{ status: string; audio_url: string }>(`/api/lora/preview/${encodeURIComponent(adapterId)}`, {
    method: 'POST',
  }),
  testLoraModel: (body: Record<string, unknown>) => request<{ status: string; audio_url: string }>('/api/lora/test', {
    method: 'POST',
    body,
  }),

  datasetProjects: () => request<DatasetProject[]>('/api/dataset_builder/list'),
  createDatasetProject: (name: string) => request<DatasetProject>('/api/dataset_builder/create', {
    method: 'POST',
    body: { name },
  }),
  datasetStatus: (name: string) => request<DatasetStatus>(`/api/dataset_builder/status/${encodeURIComponent(name)}`),
  updateDatasetMeta: (body: Record<string, unknown>) => request('/api/dataset_builder/update_meta', {
    method: 'POST',
    body,
  }),
  updateDatasetRows: (body: Record<string, unknown>) => request('/api/dataset_builder/update_rows', {
    method: 'POST',
    body,
  }),
  generateDatasetSample: (body: { description: string; text: string; dataset_name: string; sample_index: number; seed?: number }) =>
    request('/api/dataset_builder/generate_sample', {
      method: 'POST',
      body,
    }),
  generateDatasetBatch: (body: Record<string, unknown>) => request('/api/dataset_builder/generate_batch', {
    method: 'POST',
    body,
  }),
  cancelDataset: () => request('/api/dataset_builder/cancel', { method: 'POST' }),
  saveDataset: (body: Record<string, unknown>) => request('/api/dataset_builder/save', {
    method: 'POST',
    body,
  }),
  deleteDatasetProject: (name: string) => request(`/api/dataset_builder/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  }),
}
