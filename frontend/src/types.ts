export type ApiRecord = Record<string, unknown>

export type Book = {
  id: string
  title: string
  created_at?: string
  updated_at?: string
  source_filename?: string
  chapter_count?: number
  char_count?: number
}

export type BooksResponse = {
  current_book_id?: string | null
  books: Book[]
}

export type AppConfig = {
  llm?: ApiRecord
  tts?: ApiRecord
  prompts?: ApiRecord
  generation?: ApiRecord
  current_file?: string
}

export type DesktopMetadata = {
  app_name?: string
  desktop?: boolean
  base_dir?: string
  source_dir?: string
  data_dir?: string
  config_dir?: string
  cache_dir?: string
  static_dir?: string
  python?: string
  platform?: string
  hf_home?: string
  hf_endpoint?: string
}

export type DesktopBackendStatus = {
  port?: number | null
  url?: string
  logs?: string[]
  ready?: boolean
  error?: string
  pid?: number | null
  command?: string
  cwd?: string
  startedAt?: string
  stopping?: boolean
  mode?: 'managed' | 'external' | string
  managed?: boolean
  dataDir?: string
  cacheDir?: string
  logDir?: string
}

export type DesktopDiagnostics = {
  appName?: string
  appVersion?: string
  electronVersion?: string
  chromeVersion?: string
  nodeVersion?: string
  platform?: string
  arch?: string
  packaged?: boolean
  backend?: DesktopBackendStatus
  runtime?: {
    checkedAt?: string
    commands?: Array<{
      name?: string
      args?: string[]
      required?: boolean
      purpose?: string
      available?: boolean
      code?: number | null
      version?: string
      error?: string
    }>
  }
}

export type VocStudioDesktopBridge = {
  backendStatus: () => Promise<DesktopBackendStatus>
  copyBackendLaunchCommand: () => Promise<string>
  copyDiagnostics: () => Promise<string>
  diagnostics: () => Promise<DesktopDiagnostics>
  openBackendUrl: () => Promise<unknown>
  openCacheDirectory: () => Promise<string>
  openDataDirectory: () => Promise<string>
  openLogDirectory: () => Promise<string>
}

export type ModuleRequirement = {
  import_name?: string
  package?: string
  section?: string
  key?: string
}

export type CapabilityModule = {
  id: string
  name: string
  category: string
  summary?: string
  state: 'ready' | 'needs_config' | 'not_installed' | 'unavailable' | 'installing' | string
  installed?: boolean
  installing?: boolean
  installable?: boolean
  install_kind?: string
  disk_estimate_gb?: number
  model_id?: string
  model_cached?: boolean
  model_path?: string
  missing_packages?: ModuleRequirement[]
  missing_executables?: string[]
  missing_config?: ModuleRequirement[]
  executable_versions?: Record<string, string>
  manual_hint?: string
  paths?: Record<string, string>
}

export type ModuleInstallTask = {
  running?: boolean
  module_id?: string | null
  logs?: string[]
  error?: string
  cancel?: boolean
  started_at?: string
  finished_at?: string
}

export type ModulesResponse = {
  app_name?: string
  desktop?: DesktopMetadata
  install_task?: ModuleInstallTask
  modules: CapabilityModule[]
}

export type Chapter = {
  chapter_id?: string
  id?: string
  index?: number
  title?: string
  chapter_title?: string
  char_count?: number
  path?: string
  content?: string
  needs_regeneration?: boolean
  next_chapter?: {
    chapter_id?: string
    chapter_index?: number
    chapter_title?: string
  } | null
  prev_chapter?: {
    chapter_id?: string
    chapter_index?: number
    chapter_title?: string
  } | null
}

export type ChaptersManifest = {
  chapter_count?: number
  total_chars?: number
  generated_at?: string
  chapters?: Chapter[]
}

export type TaskStatus = {
  running?: boolean
  cancel?: boolean
  logs?: string[]
  [key: string]: unknown
}

export type ScriptProgressItem = {
  chapter_id?: string
  chapter_title?: string
  title?: string
  status?: string
  entry_count?: number
  issue_count?: number
  unknown_speaker_count?: number
  coverage_ratio?: number
  [key: string]: unknown
}

export type CharacterItem = {
  name: string
  aliases?: string[]
  traits?: string
  voice_profile?: string
  confidence?: number | null
  source?: string
  line_count?: number
  char_count?: number
  is_narrator?: boolean
  config?: ApiRecord
  voice_status?: string
  voice_config_status?: 'missing' | 'customized' | 'confirmed' | string
  has_voice_config?: boolean
  has_custom_voice_config?: boolean
  has_confirmed_voice_config?: boolean
  configured?: boolean
  [key: string]: unknown
}

export type CharactersResponse = {
  source?: string
  total?: number
  characters?: CharacterItem[]
  narrator_style?: string
  genre?: string
  key_terms?: string[]
}

export type VoiceItem = {
  name: string
  config?: ApiRecord
  metadata?: ApiRecord
  source?: string
  line_count?: number
  char_count?: number
  is_narrator?: boolean
  aliases?: string[]
  raw_speakers?: string[]
  voice_config_status?: 'missing' | 'customized' | 'confirmed' | string
  has_voice_config?: boolean
  has_custom_voice_config?: boolean
  has_confirmed_voice_config?: boolean
  configured?: boolean
  [key: string]: unknown
}

export type VoiceConfigItem = {
  type?: string
  voice?: string
  character_style?: string
  default_style?: string
  seed?: string
  confirmed?: boolean
  ref_audio?: string
  ref_text?: string
  adapter_id?: string
  adapter_path?: string
  description?: string
  edge_voice?: string
  edge_rate?: string
  edge_pitch?: string
  dashscope_model?: string
  dashscope_voice?: string
  language_type?: string
  volcengine_resource_id?: string
  volcengine_speaker?: string
  volcengine_sample_rate?: number
  volcengine_speech_rate?: number
  volcengine_loudness_rate?: number
  volcengine_emotion?: string
  volcengine_emotion_scale?: number
}

export type EdgeVoice = {
  id: string
  name?: string
  locale?: string
  gender?: string
}

export type VolcengineVoiceOption = {
  value: string
  label?: string
  name?: string
  scene?: string
}

export type VolcengineVoicesResponse = {
  voices?: Record<string, VolcengineVoiceOption[]>
  source?: string
  cache_hit?: boolean
  error?: string
  updated_at?: string
  [key: string]: unknown
}

export type Chunk = {
  speaker?: string
  type?: string
  text?: string
  instruct?: string
  emotion?: string
  status?: string
  audio_path?: string | null
  audio_url?: string | null
  chapter_id?: string
  pause_after?: number
  [key: string]: unknown
}

export type ChunkDeleteResponse = {
  status: string
  deleted?: Chunk
  total?: number
  script_entry_count?: number
  [key: string]: unknown
}

export type ChapterTtsProgressItem = {
  chapter_id?: string
  chapter_index?: number
  chapter_title?: string
  char_count?: number
  total_chunks?: number
  done_chunks?: number
  pending_chunks?: number
  generating_chunks?: number
  error_chunks?: number
  audio_chunks?: number
  missing_audio_chunks?: number
  complete?: boolean
  [key: string]: unknown
}

export type ChapterTtsProgressResponse = {
  summary?: {
    total_chapters?: number
    complete_chapters?: number
    incomplete_chapters?: number
    total_chunks?: number
    done_chunks?: number
    audio_chunks?: number
    pending_chunks?: number
    generating_chunks?: number
    error_chunks?: number
    missing_audio_chunks?: number
  }
  chapters?: ChapterTtsProgressItem[]
}

export type SavedScript = {
  name: string
  created?: string
  saved_at?: string
  book_title?: string
  source_book_title?: string
  entry_count?: number
  chunk_count?: number
  chapter_count?: number
  has_voice_config?: boolean
  has_chunks?: boolean
  has_character_book?: boolean
  [key: string]: unknown
}

export type DesignedVoice = {
  id: string
  name: string
  description?: string
  sample_text?: string
  filename?: string
}

export type CloneVoice = {
  id: string
  name: string
  filename?: string
}

export type LoraDataset = {
  dataset_id: string
  sample_count?: number
}

export type LoraModel = {
  id: string
  name?: string
  description?: string
  gender?: string
  dataset_id?: string
  sample_count?: number
  final_loss?: number
  builtin?: boolean
  downloaded?: boolean
  adapter_path?: string
  preview_audio_url?: string | null
  [key: string]: unknown
}

export type DatasetProject = {
  name: string
  description?: string
  sample_count?: number
  done_count?: number
}

export type DatasetStatus = {
  description?: string
  global_seed?: string
  samples?: DatasetSample[]
  running?: boolean
  logs?: string[]
}

export type DatasetSample = {
  emotion?: string
  text?: string
  seed?: string | number
  status?: string
  audio_url?: string
  error?: string
  description?: string
}

export type TaggedScriptResponse = {
  chapter_id?: string
  entry_count?: number
  content?: string
  status?: string
  replace_scope?: string
  imported_entries?: number
  total_entries?: number
  total_chunks?: number
  speaker_updates?: number
  preview?: ApiRecord
  [key: string]: unknown
}
