import {
  App as AntApp,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Flex,
  Input,
  List,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { TableColumnsType } from 'antd'
import {
  DeleteOutlined,
  FolderOpenOutlined,
  HighlightOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SaveOutlined,
  StopOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { Key } from 'react'
import { api } from '../api'
import { ResourceList, ScriptDiagnosticsPanel, ScriptStreamPanel, TaskStatusCard } from '../components/common'
import {
  collectSpeakerNames,
  normalizeSpeakerName,
  unknownSpeakersFromIssues,
  type UnknownSpeakerRow,
} from '../domain'
import {
  appendStreamLog,
  emptyScriptStreamState,
  reduceScriptStreamState,
  reviewEventTypes,
  scriptEventTypes,
  terminalScriptEvents,
  type ScriptStreamState,
} from '../scriptStream'
import type { CharacterItem, CharactersResponse, SavedScript, ScriptProgressItem, TaskStatus } from '../types'
import { formatCount, formatPercent, normalizeScriptProgress, statusColor, summarizeStatus } from '../utils'

const { Text } = Typography

type ScriptMode = 'script' | 'characters'

type ResourcePreview = {
  title: string
  data: unknown
} | null

type ScriptStepState = {
  mode: ScriptMode
  reuseCharacterBook: boolean
  selectedChapterIds: Key[]
  issueChapterId: string | null
  estimate: Record<string, unknown> | null
  resourcePreview: ResourcePreview
  annotatedScriptLoading: boolean
}

type TaggedScope = 'all' | 'chapter'

type TaggedEditorState = {
  scope: TaggedScope
  chapterId: string
  defaultInstruct: string
  content: string
  preview: Record<string, unknown> | null
  loadingTaggedScript: boolean
}

type TaggedEditorAction =
  | { type: 'setScope'; value: TaggedScope }
  | { type: 'setChapterId'; value: string }
  | { type: 'setDefaultInstruct'; value: string }
  | { type: 'setContent'; value: string }
  | { type: 'setPreview'; value: Record<string, unknown> | null }
  | { type: 'setLoadingTaggedScript'; value: boolean }
  | { type: 'loadedTaggedScript'; content: string; entryCount: number }

type ScriptStepAction =
  | { type: 'setMode'; value: ScriptMode }
  | { type: 'setReuseCharacterBook'; value: boolean }
  | { type: 'setSelectedChapterIds'; value: Key[] }
  | { type: 'setIssueChapterId'; value: string | null }
  | { type: 'setEstimate'; value: Record<string, unknown> | null }
  | { type: 'setResourcePreview'; value: ResourcePreview }
  | { type: 'setAnnotatedScriptLoading'; value: boolean }

const INITIAL_SCRIPT_STEP_STATE: ScriptStepState = {
  mode: 'script',
  reuseCharacterBook: true,
  selectedChapterIds: [],
  issueChapterId: null,
  estimate: null,
  resourcePreview: null,
  annotatedScriptLoading: false,
}

const INITIAL_TAGGED_EDITOR_STATE: TaggedEditorState = {
  scope: 'all',
  chapterId: '',
  defaultInstruct: '',
  content: '',
  preview: null,
  loadingTaggedScript: false,
}

function scriptStepReducer(state: ScriptStepState, action: ScriptStepAction): ScriptStepState {
  switch (action.type) {
    case 'setMode':
      return { ...state, mode: action.value }
    case 'setReuseCharacterBook':
      return { ...state, reuseCharacterBook: action.value }
    case 'setSelectedChapterIds':
      return { ...state, selectedChapterIds: action.value }
    case 'setIssueChapterId':
      return { ...state, issueChapterId: action.value }
    case 'setEstimate':
      return { ...state, estimate: action.value }
    case 'setResourcePreview':
      return { ...state, resourcePreview: action.value }
    case 'setAnnotatedScriptLoading':
      return { ...state, annotatedScriptLoading: action.value }
    default:
      return state
  }
}

function taggedEditorReducer(state: TaggedEditorState, action: TaggedEditorAction): TaggedEditorState {
  switch (action.type) {
    case 'setScope':
      return { ...state, scope: action.value }
    case 'setChapterId':
      return { ...state, chapterId: action.value }
    case 'setDefaultInstruct':
      return { ...state, defaultInstruct: action.value }
    case 'setContent':
      return { ...state, content: action.value }
    case 'setPreview':
      return { ...state, preview: action.value }
    case 'setLoadingTaggedScript':
      return { ...state, loadingTaggedScript: action.value }
    case 'loadedTaggedScript':
      return {
        ...state,
        content: action.content,
        preview: { entry_count: action.entryCount },
      }
    default:
      return state
  }
}

function selectedChapterIdsFrom(items: ScriptProgressItem[], filter: (item: ScriptProgressItem) => boolean) {
  return items.flatMap((item) => item.chapter_id && filter(item) ? [String(item.chapter_id)] : [])
}

function useScriptEventStreams(scriptRunning?: boolean, reviewRunning?: boolean) {
  const [scriptStream, setScriptStream] = useState<ScriptStreamState>(() => emptyScriptStreamState())
  const [reviewStream, setReviewStream] = useState<ScriptStreamState>(() => emptyScriptStreamState())
  const scriptEventSourceRef = useRef<EventSource | null>(null)
  const reviewEventSourceRef = useRef<EventSource | null>(null)

  const startScriptEventStream = useCallback(() => {
    scriptEventSourceRef.current?.close()
    const eventSource = new EventSource('/api/events/script')
    scriptEventSourceRef.current = eventSource

    const handleEvent = (event: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(event.data) as { type?: string; data?: unknown }
        setScriptStream((current) => reduceScriptStreamState(current, parsed))
        if (terminalScriptEvents.has(parsed.type || '')) {
          eventSource.close()
          if (scriptEventSourceRef.current === eventSource) {
            scriptEventSourceRef.current = null
          }
        }
      } catch (error) {
        setScriptStream((current) => ({
          ...current,
          status: 'warning',
          logs: appendStreamLog(current.logs, `SSE parse error: ${error instanceof Error ? error.message : String(error)}`),
        }))
      }
    }

    eventSource.onopen = () => {
      setScriptStream({ ...emptyScriptStreamState(), connected: true, status: 'running', stage: '等待脚本事件...' })
    }
    eventSource.onmessage = handleEvent
    scriptEventTypes.forEach((type) => eventSource.addEventListener(type, handleEvent))
    eventSource.onerror = () => {
      eventSource.close()
      if (scriptEventSourceRef.current === eventSource) {
        scriptEventSourceRef.current = null
      }
      setScriptStream((current) => ({
        ...current,
        connected: false,
        status: current.status === 'running' ? 'warning' : current.status,
        logs: appendStreamLog(current.logs, 'SSE 连接中断，已回退到状态轮询。'),
      }))
    }
  }, [])

  const startReviewEventStream = useCallback(() => {
    reviewEventSourceRef.current?.close()
    const eventSource = new EventSource('/api/events/review')
    reviewEventSourceRef.current = eventSource

    const handleEvent = (event: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(event.data) as { type?: string; data?: unknown }
        setReviewStream((current) => reduceScriptStreamState(current, parsed))
        if (terminalScriptEvents.has(parsed.type || '')) {
          eventSource.close()
          if (reviewEventSourceRef.current === eventSource) {
            reviewEventSourceRef.current = null
          }
        }
      } catch (error) {
        setReviewStream((current) => ({
          ...current,
          status: 'warning',
          logs: appendStreamLog(current.logs, `SSE parse error: ${error instanceof Error ? error.message : String(error)}`),
        }))
      }
    }

    eventSource.onopen = () => {
      setReviewStream({ ...emptyScriptStreamState(), connected: true, status: 'running', stage: '等待审校事件...', task: 'review', activeStage: 'review' })
    }
    eventSource.onmessage = handleEvent
    reviewEventTypes.forEach((type) => eventSource.addEventListener(type, handleEvent))
    eventSource.onerror = () => {
      eventSource.close()
      if (reviewEventSourceRef.current === eventSource) {
        reviewEventSourceRef.current = null
      }
      setReviewStream((current) => ({
        ...current,
        connected: false,
        status: current.status === 'running' ? 'warning' : current.status,
        logs: appendStreamLog(current.logs, '审校 SSE 连接中断，已回退到状态轮询。'),
      }))
    }
  }, [])

  useEffect(() => {
    if (scriptRunning && !scriptEventSourceRef.current) {
      startScriptEventStream()
    }
  }, [scriptRunning, startScriptEventStream])

  useEffect(() => {
    if (reviewRunning && !reviewEventSourceRef.current) {
      startReviewEventStream()
    }
  }, [reviewRunning, startReviewEventStream])

  useEffect(() => () => {
    scriptEventSourceRef.current?.close()
    scriptEventSourceRef.current = null
    reviewEventSourceRef.current?.close()
    reviewEventSourceRef.current = null
  }, [])

  return { scriptStream, reviewStream }
}

function useScriptTaskActions({
  mode,
  reuseCharacterBook,
  selectedChapterIds,
  progressItems,
  dispatch,
}: {
  mode: ScriptMode
  reuseCharacterBook: boolean
  selectedChapterIds: Key[]
  progressItems: ScriptProgressItem[]
  dispatch: (action: ScriptStepAction) => void
}) {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()

  const generateMutation = useMutation({
    mutationFn: (body?: Record<string, unknown>) => api.generateScript(body ?? {
      mode,
      reuse_character_book: reuseCharacterBook,
      chapter_ids: selectedChapterIds.map(String),
    }),
    onSuccess: async (result) => {
      if (result.status === 'dry_run') {
        dispatch({ type: 'setEstimate', value: (result as Record<string, unknown>).estimate as Record<string, unknown> })
        message.success('脚本任务预估已生成')
        return
      }
      message.success('脚本任务已启动')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['task-status', 'script'] }),
        queryClient.invalidateQueries({ queryKey: ['script-generation-snapshot'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['script-outputs'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const reviewMutation = useMutation({
    mutationFn: () => api.reviewScript({ dry_run: false }),
    onSuccess: async () => {
      message.success('脚本审校已启动')
      await queryClient.invalidateQueries({ queryKey: ['task-status', 'review'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const rebuildBibleMutation = useMutation({
    mutationFn: api.rebuildStoryBible,
    onSuccess: async () => {
      message.success('Story Bible 已重建')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['story-bible'] }),
        queryClient.invalidateQueries({ queryKey: ['script-outputs'] }),
        queryClient.invalidateQueries({ queryKey: ['script-action-items'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const cancelMutation = useMutation({
    mutationFn: api.cancelScript,
    onSuccess: async () => {
      message.success('已请求取消脚本任务')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['task-status', 'script'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['script-generation-snapshot'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const startScriptTask = (body: Record<string, unknown>, title: string, content: string) => {
    modal.confirm({
      title,
      content,
      okText: '启动',
      cancelText: '取消',
      onOk: () => generateMutation.mutate(body),
    })
  }

  const generateSelected = (reuse = reuseCharacterBook) => {
    const chapterIds = selectedChapterIds.map(String)
    if (!chapterIds.length) {
      message.warning('请先选择章节')
      return
    }
    startScriptTask(
      { mode: 'script', reuse_character_book: reuse, chapter_ids: chapterIds },
      '重跑选中章节？',
      `会处理选中的 ${formatCount(chapterIds.length)} 个章节。`,
    )
  }

  const generateMissingSelected = () => {
    const chapterIds = selectedChapterIds.map(String)
    if (!chapterIds.length) {
      message.warning('请先选择要补生成的章节')
      return
    }
    startScriptTask(
      { mode: 'script', missing_only: true, reuse_character_book: reuseCharacterBook, chapter_ids: chapterIds },
      '补生成选中章节的缺失脚本？',
      '只会处理选中范围内后端判断为缺失的章节。',
    )
  }

  const generateAllMissing = () => {
    startScriptTask(
      { mode: 'script', missing_only: true, reuse_character_book: reuseCharacterBook },
      '补全所有缺失章节？',
      '后端会自动筛选全书缺失脚本的章节。',
    )
  }

  const generateFailed = () => {
    const failedIds = selectedChapterIdsFrom(
      progressItems,
      (item) => !item.generated && Boolean(item.failed || item.cancelled || item.interrupted || item.status === 'failed' || item.status === 'error'),
    )
    if (!failedIds.length) {
      message.info('没有失败、取消或中断的章节需要重跑')
      return
    }
    startScriptTask(
      { mode: 'script', reuse_character_book: reuseCharacterBook, chapter_ids: failedIds },
      '重跑失败/中断章节？',
      `会处理 ${formatCount(failedIds.length)} 个失败、取消或中断章节。`,
    )
  }

  const generateFromFirstSelected = () => {
    const selected = new Set(selectedChapterIds.map(String))
    const firstIndex = progressItems.findIndex((item) => item.chapter_id && selected.has(String(item.chapter_id)))
    if (firstIndex < 0) {
      message.warning('请先选择起始章节')
      return
    }
    const chapterIds = progressItems.slice(firstIndex).flatMap((item) => item.chapter_id ? [String(item.chapter_id)] : [])
    startScriptTask(
      { mode: 'script', reuse_character_book: reuseCharacterBook, chapter_ids: chapterIds },
      '从选中章节起重跑？',
      `会从第一个选中章节开始，处理后续 ${formatCount(chapterIds.length)} 个章节。`,
    )
  }

  const analyzeSelectedCharacters = () => {
    const chapterIds = selectedChapterIds.map(String)
    if (!chapterIds.length) {
      message.warning('请先选择要分析人物池的章节')
      return
    }
    startScriptTask(
      { mode: 'characters', chapter_ids: chapterIds },
      '分析选中章节人物池？',
      '这只会更新人物池和音色参考，不会生成标注脚本。',
    )
  }

  const analyzeMissingCharacters = () => {
    const chapterIds = selectedChapterIdsFrom(
      progressItems,
      (item) => !item.character_analyzed || Boolean(item.character_analysis_failed),
    )
    if (!chapterIds.length) {
      message.info('没有缺失人物池分析的章节')
      return
    }
    startScriptTask(
      { mode: 'characters', chapter_ids: chapterIds },
      '补分析缺失人物池？',
      `会处理 ${formatCount(chapterIds.length)} 个缺失人物池分析的章节。`,
    )
  }

  const selectProgressRows = (filter: (item: ScriptProgressItem) => boolean) => {
    const nextIds = selectedChapterIdsFrom(progressItems, filter)
    dispatch({ type: 'setSelectedChapterIds', value: nextIds })
    message.info(`已选择 ${formatCount(nextIds.length)} 个章节`)
  }

  const startCurrentTask = () => {
    startScriptTask({
      mode,
      reuse_character_book: reuseCharacterBook,
      chapter_ids: selectedChapterIds.map(String),
    }, '启动脚本任务？', selectedChapterIds.length
      ? `会处理选中的 ${formatCount(selectedChapterIds.length)} 个章节。`
      : mode === 'characters' ? '只会更新人物池与音色参考。' : '会按章节生成或补全标注脚本。')
  }

  const estimateCurrentTask = () => generateMutation.mutate({
    mode,
    reuse_character_book: reuseCharacterBook,
    chapter_ids: selectedChapterIds.map(String),
    dry_run: true,
  })

  const rerunChapter = (chapterId: string) => generateMutation.mutate({
    mode: 'script',
    reuse_character_book: true,
    chapter_ids: [chapterId],
  })

  return {
    generateMutation,
    reviewMutation,
    rebuildBibleMutation,
    cancelMutation,
    selectProgressRows,
    generateSelected,
    generateMissingSelected,
    generateAllMissing,
    generateFailed,
    generateFromFirstSelected,
    analyzeSelectedCharacters,
    analyzeMissingCharacters,
    startCurrentTask,
    estimateCurrentTask,
    rerunChapter,
  }
}

function useUnknownSpeakerActions(characterPool?: CharactersResponse) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const characters = characterPool?.characters ?? []

  const saveCharacterPool = (nextCharacters: CharacterItem[]) => api.saveCharacters({
    narrator_style: characterPool?.narrator_style ?? '',
    genre: characterPool?.genre ?? '',
    key_terms: characterPool?.key_terms ?? [],
    normalize_script_speakers: true,
    characters: nextCharacters.map((character) => ({
      name: character.name,
      aliases: character.aliases ?? [],
      traits: character.traits ?? '',
      voice_profile: character.voice_profile ?? '',
      confidence: typeof character.confidence === 'number' ? character.confidence : undefined,
    })),
  })

  const addUnknownSpeakersMutation = useMutation({
    mutationFn: async (speakers: string[]) => {
      const existingNames = new Set(characters.flatMap((character) => collectSpeakerNames(character).map(normalizeSpeakerName)))
      const additions: CharacterItem[] = speakers.flatMap((rawSpeaker) => {
        const speaker = rawSpeaker.trim()
        if (!speaker || existingNames.has(normalizeSpeakerName(speaker))) return []
        return [{
          name: speaker,
          aliases: [],
          traits: '从标注脚本中发现的说话人，待补充人物设定。',
          voice_profile: '',
          confidence: 0.5,
          source: 'script_issues',
        }]
      })
      if (!additions.length) return { status: 'skipped', added: 0 }
      await saveCharacterPool([...characters, ...additions])
      return { status: 'saved', added: additions.length }
    },
    onSuccess: async (result) => {
      if (result.added) {
        message.success(`已加入 ${formatCount(result.added)} 个未知说话人`)
      } else {
        message.info('没有新的未知说话人需要加入')
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['script-issues'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['chunks'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const mergeUnknownSpeakerMutation = useMutation({
    mutationFn: async ({ speaker, targetName }: { speaker: string; targetName: string }) => {
      const nextCharacters = characters.map((character) => {
        if (character.name !== targetName) return character
        const aliases = [
          ...(character.aliases ?? []),
          speaker,
        ].filter((alias, index, values) => values.findIndex((item) => normalizeSpeakerName(item) === normalizeSpeakerName(alias)) === index)
        return { ...character, aliases }
      })
      await saveCharacterPool(nextCharacters)
      return { speaker, targetName }
    },
    onSuccess: async ({ speaker, targetName }) => {
      message.success(`已将「${speaker}」合并为「${targetName}」的别名`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['script-issues'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['chunks'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  return { addUnknownSpeakersMutation, mergeUnknownSpeakerMutation }
}

export function ScriptStep() {
  const { message } = AntApp.useApp()
  const [state, dispatch] = useReducer(scriptStepReducer, INITIAL_SCRIPT_STEP_STATE)
  const {
    mode,
    reuseCharacterBook,
    selectedChapterIds,
    issueChapterId,
    estimate,
    resourcePreview,
    annotatedScriptLoading,
  } = state
  const scriptStatus = useQuery({
    queryKey: ['task-status', 'script'],
    queryFn: () => api.taskStatus('script'),
    refetchInterval: (query) => query.state.data?.running ? 1500 : false,
  })
  const reviewStatus = useQuery({
    queryKey: ['task-status', 'review'],
    queryFn: () => api.taskStatus('review'),
    refetchInterval: (query) => query.state.data?.running ? 1500 : false,
  })
  const progressQuery = useQuery({
    queryKey: ['script-progress'],
    queryFn: api.scriptProgress,
    refetchInterval: scriptStatus.data?.running ? 2000 : false,
  })
  const issuesQuery = useQuery({ queryKey: ['script-issues'], queryFn: api.scriptIssues })
  const scriptsQuery = useQuery({ queryKey: ['saved-scripts'], queryFn: api.scripts })
  const outputsQuery = useQuery({ queryKey: ['script-outputs'], queryFn: api.scriptOutputs })
  const actionItemsQuery = useQuery({ queryKey: ['script-action-items'], queryFn: api.scriptActionItems })
  const storyBibleQuery = useQuery({ queryKey: ['story-bible'], queryFn: api.storyBible })
  const chapterMemoryQuery = useQuery({ queryKey: ['chapter-memory'], queryFn: () => api.chapterMemory() })
  const snapshotQuery = useQuery({ queryKey: ['script-generation-snapshot'], queryFn: api.scriptGenerationSnapshot })
  const charactersQuery = useQuery({ queryKey: ['characters'], queryFn: () => api.characters() })
  const issueDetailQuery = useQuery({
    queryKey: ['script-issue', issueChapterId],
    queryFn: () => api.scriptIssue(issueChapterId ?? ''),
    enabled: !!issueChapterId,
  })
  const { scriptStream, reviewStream } = useScriptEventStreams(scriptStatus.data?.running, reviewStatus.data?.running)

  const showAnnotatedScript = async () => {
    dispatch({ type: 'setAnnotatedScriptLoading', value: true })
    try {
      const data = await api.annotatedScript()
      dispatch({ type: 'setResourcePreview', value: { title: 'Annotated Script', data } })
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      dispatch({ type: 'setAnnotatedScriptLoading', value: false })
    }
  }

  const progressItems = normalizeScriptProgress(progressQuery.data)
  const issueSummary = (issuesQuery.data?.summary ?? {}) as Record<string, number>
  const unknownSpeakers = unknownSpeakersFromIssues(issuesQuery.data)
  const characters = charactersQuery.data?.characters ?? []
  const status = scriptStatus.data
  const outputFiles = Object.entries(outputsQuery.data ?? {})
  const actionItems = Array.isArray(actionItemsQuery.data?.items) ? actionItemsQuery.data.items as Record<string, unknown>[] : []
  const memorySummary = (chapterMemoryQuery.data?.summary ?? {}) as Record<string, number>
  const selectedCount = selectedChapterIds.length
  const dispatchSelectedChapterIds = (value: Key[]) => dispatch({ type: 'setSelectedChapterIds', value })
  const {
    generateMutation,
    reviewMutation,
    rebuildBibleMutation,
    cancelMutation,
    selectProgressRows,
    generateSelected,
    generateMissingSelected,
    generateAllMissing,
    generateFailed,
    generateFromFirstSelected,
    analyzeSelectedCharacters,
    analyzeMissingCharacters,
    startCurrentTask,
    estimateCurrentTask,
    rerunChapter,
  } = useScriptTaskActions({
    mode,
    reuseCharacterBook,
    selectedChapterIds,
    progressItems,
    dispatch,
  })
  const { addUnknownSpeakersMutation, mergeUnknownSpeakerMutation } = useUnknownSpeakerActions(charactersQuery.data)

  const columns: TableColumnsType<ScriptProgressItem> = [
    {
      title: '章节',
      render: (_, item) => (
        <Space size={4} wrap>
          <span>{item.chapter_title ?? item.title ?? item.chapter_id ?? '-'}</span>
          {item.memory_stale ? <Tag color="warning">上下文过期</Tag> : null}
          {item.memory_available ? <Tag>有记忆</Tag> : null}
        </Space>
      ),
    },
    { title: '状态', dataIndex: 'status', width: 120, render: (value) => <Tag color={statusColor(value)}>{summarizeStatus(value)}</Tag> },
    { title: '条目', dataIndex: 'entry_count', width: 100, render: formatCount },
    { title: '覆盖率', dataIndex: 'coverage_ratio', width: 100, render: formatPercent },
    {
      title: '问题',
      dataIndex: 'issue_count',
      width: 100,
      render: (value, record) => value ? (
        <Button type="link" size="small" onClick={() => dispatch({ type: 'setIssueChapterId', value: record.chapter_id ?? '' })}>
          {value}
        </Button>
      ) : '-',
    },
    {
      title: '操作',
      width: 96,
      render: (_, record) => (
        <Button
          size="small"
          disabled={!record.chapter_id || status?.running}
          onClick={() => record.chapter_id && rerunChapter(record.chapter_id)}
        >
          重跑
        </Button>
      ),
    },
  ]

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={8}>
        <ScriptControlPanel
          mode={mode}
          reuseCharacterBook={reuseCharacterBook}
          selectedCount={selectedCount}
          estimate={estimate}
          status={status}
          reviewStatus={reviewStatus.data}
          scriptStream={scriptStream}
          reviewStream={reviewStream}
          generatePending={generateMutation.isPending}
          cancelPending={cancelMutation.isPending}
          reviewPending={reviewMutation.isPending}
          rebuildBiblePending={rebuildBibleMutation.isPending}
          onModeChange={(value) => dispatch({ type: 'setMode', value })}
          onReuseCharacterBookChange={(value) => dispatch({ type: 'setReuseCharacterBook', value })}
          onEstimate={estimateCurrentTask}
          onStart={startCurrentTask}
          onCancel={() => cancelMutation.mutate()}
          onReview={() => reviewMutation.mutate()}
          onRebuildBible={() => rebuildBibleMutation.mutate()}
        />
        <ScriptResourcesPanel
          storyBibleAvailable={Boolean(storyBibleQuery.data?.available)}
          actionItems={actionItems}
          memoryAvailableChapters={memorySummary.available_chapters}
          snapshotAvailable={Boolean(snapshotQuery.data?.available)}
          outputFiles={outputFiles}
          outputFilesLoading={outputsQuery.isLoading}
          annotatedScriptLoading={annotatedScriptLoading}
          chapterMemoryLoading={chapterMemoryQuery.isFetching}
          snapshotLoading={snapshotQuery.isFetching}
          onShowAnnotatedScript={showAnnotatedScript}
          onShowChapterMemory={() => dispatch({ type: 'setResourcePreview', value: { title: '章节记忆', data: chapterMemoryQuery.data ?? {} } })}
          onShowSnapshot={() => dispatch({ type: 'setResourcePreview', value: { title: '生成快照', data: snapshotQuery.data ?? {} } })}
        />
      </Col>
      <Col xs={24} xl={16}>
        <ScriptProgressPanel
          issueSummary={issueSummary}
          selectedCount={selectedCount}
          running={status?.running}
          generatePending={generateMutation.isPending}
          progressLoading={progressQuery.isLoading}
          progressItems={progressItems}
          columns={columns}
          selectedChapterIds={selectedChapterIds}
          onRefresh={() => progressQuery.refetch()}
          onSelectRows={selectProgressRows}
          onClearSelection={() => dispatchSelectedChapterIds([])}
          onSelectedChapterIdsChange={dispatchSelectedChapterIds}
          onGenerateSelected={() => generateSelected(false)}
          onGenerateSelectedReuse={() => generateSelected(true)}
          onGenerateMissingSelected={generateMissingSelected}
          onGenerateAllMissing={generateAllMissing}
          onGenerateFailed={generateFailed}
          onGenerateFromFirstSelected={generateFromFirstSelected}
          onAnalyzeSelectedCharacters={analyzeSelectedCharacters}
          onAnalyzeMissingCharacters={analyzeMissingCharacters}
        />
        <UnknownSpeakersPanel
          unknownSpeakers={unknownSpeakers}
          characters={characters}
          loading={issuesQuery.isLoading || charactersQuery.isLoading}
          addPending={addUnknownSpeakersMutation.isPending}
          onRefresh={() => issuesQuery.refetch()}
          onAddSpeakers={(speakers) => addUnknownSpeakersMutation.mutate(speakers)}
          onMergeSpeaker={(speaker, targetName) => mergeUnknownSpeakerMutation.mutate({ speaker, targetName })}
        />
        <Card title="脚本包" className="mt-16">
          <SavedScriptsList scripts={scriptsQuery.data ?? []} loading={scriptsQuery.isLoading} />
        </Card>
        <Card title="Tagged 文本导入" className="mt-16">
          <TaggedScriptEditor />
        </Card>
      </Col>
      <Drawer open={!!issueChapterId} title="脚本问题详情" width={680} onClose={() => dispatch({ type: 'setIssueChapterId', value: null })}>
        <Spin spinning={issueDetailQuery.isLoading}>
          <pre className="json-preview">{JSON.stringify(issueDetailQuery.data ?? {}, null, 2)}</pre>
        </Spin>
      </Drawer>
      <Drawer
        open={!!resourcePreview}
        title={resourcePreview?.title}
        width={820}
        onClose={() => dispatch({ type: 'setResourcePreview', value: null })}
      >
        <pre className="json-preview">{JSON.stringify(resourcePreview?.data ?? {}, null, 2)}</pre>
      </Drawer>
    </Row>
  )
}

type ScriptControlPanelProps = {
  mode: ScriptMode
  reuseCharacterBook: boolean
  selectedCount: number
  estimate: Record<string, unknown> | null
  status?: TaskStatus
  reviewStatus?: TaskStatus
  scriptStream: ScriptStreamState
  reviewStream: ScriptStreamState
  generatePending: boolean
  cancelPending: boolean
  reviewPending: boolean
  rebuildBiblePending: boolean
  onModeChange: (mode: ScriptMode) => void
  onReuseCharacterBookChange: (value: boolean) => void
  onEstimate: () => void
  onStart: () => void
  onCancel: () => void
  onReview: () => void
  onRebuildBible: () => void
}

function ScriptControlPanel({
  mode,
  reuseCharacterBook,
  selectedCount,
  estimate,
  status,
  reviewStatus,
  scriptStream,
  reviewStream,
  generatePending,
  cancelPending,
  reviewPending,
  rebuildBiblePending,
  onModeChange,
  onReuseCharacterBookChange,
  onEstimate,
  onStart,
  onCancel,
  onReview,
  onRebuildBible,
}: ScriptControlPanelProps) {
  return (
    <Card title="脚本生成">
      <Space direction="vertical" size={16} className="full-width">
        <Select
          value={mode}
          onChange={onModeChange}
          options={[
            { value: 'script', label: '生成标注脚本' },
            { value: 'characters', label: '只分析人物池' },
          ]}
        />
        <Flex justify="space-between" align="center">
          <Text>复用现有人物池</Text>
          <Switch checked={reuseCharacterBook} disabled={mode !== 'script'} onChange={onReuseCharacterBookChange} />
        </Flex>
        <Space wrap>
          <Button icon={<HighlightOutlined />} loading={generatePending} disabled={status?.running} onClick={onEstimate}>
            预估
          </Button>
          <Button type="primary" icon={<PlayCircleOutlined />} loading={generatePending} disabled={status?.running} onClick={onStart}>
            启动
          </Button>
          <Button danger icon={<StopOutlined />} disabled={!status?.running} loading={cancelPending} onClick={onCancel}>
            取消
          </Button>
          <Button loading={reviewPending} onClick={onReview}>
            审校脚本
          </Button>
          <Button loading={rebuildBiblePending} onClick={onRebuildBible}>
            重建 Story Bible
          </Button>
        </Space>
        {selectedCount ? <Text type="secondary">当前选中 {formatCount(selectedCount)} 章</Text> : null}
        {estimate ? (
          <Card size="small" title="任务预估">
            <pre className="json-preview">{JSON.stringify(estimate, null, 2)}</pre>
          </Card>
        ) : null}
        <ScriptDiagnosticsPanel stream={scriptStream} status={status} reviewStatus={reviewStatus} reviewStream={reviewStream} />
        <ScriptStreamPanel stream={scriptStream} status={status} />
        <TaskStatusCard status={reviewStatus} />
      </Space>
    </Card>
  )
}

type ScriptResourcesPanelProps = {
  storyBibleAvailable: boolean
  actionItems: Record<string, unknown>[]
  memoryAvailableChapters?: number
  snapshotAvailable: boolean
  outputFiles: [string, unknown][]
  outputFilesLoading: boolean
  annotatedScriptLoading: boolean
  chapterMemoryLoading: boolean
  snapshotLoading: boolean
  onShowAnnotatedScript: () => void
  onShowChapterMemory: () => void
  onShowSnapshot: () => void
}

function ScriptResourcesPanel({
  storyBibleAvailable,
  actionItems,
  memoryAvailableChapters,
  snapshotAvailable,
  outputFiles,
  outputFilesLoading,
  annotatedScriptLoading,
  chapterMemoryLoading,
  snapshotLoading,
  onShowAnnotatedScript,
  onShowChapterMemory,
  onShowSnapshot,
}: ScriptResourcesPanelProps) {
  return (
    <Card title="脚本资源" className="mt-16">
      <Space direction="vertical" size={12} className="full-width">
        <Descriptions size="small" column={1}>
          <Descriptions.Item label="Story Bible">{storyBibleAvailable ? '可用' : '未建立'}</Descriptions.Item>
          <Descriptions.Item label="操作建议">{formatCount(actionItems.length)}</Descriptions.Item>
          <Descriptions.Item label="章节记忆">{formatCount(memoryAvailableChapters)} 章</Descriptions.Item>
          <Descriptions.Item label="快照">{snapshotAvailable ? '可用' : '未建立'}</Descriptions.Item>
        </Descriptions>
        <Space wrap>
          <Button size="small" loading={annotatedScriptLoading} onClick={onShowAnnotatedScript}>
            查看标注脚本
          </Button>
          <Button size="small" loading={chapterMemoryLoading} onClick={onShowChapterMemory}>
            查看章节记忆
          </Button>
          <Button size="small" loading={snapshotLoading} onClick={onShowSnapshot}>
            查看生成快照
          </Button>
        </Space>
        {actionItems.length ? (
          <List
            size="small"
            dataSource={actionItems.slice(0, 5)}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={String(item.title ?? item.type ?? '建议')}
                  description={String(item.message ?? item.description ?? '')}
                />
              </List.Item>
            )}
          />
        ) : null}
        <ResourceList<[string, unknown]>
          loading={outputFilesLoading}
          data={outputFiles}
          empty="暂无脚本输出文件"
          getTitle={([name]) => name}
          getDescription={([, value]) => typeof value === 'string' ? value : JSON.stringify(value)}
        />
      </Space>
    </Card>
  )
}

type ScriptProgressPanelProps = {
  issueSummary: Record<string, number>
  selectedCount: number
  running?: boolean
  generatePending: boolean
  progressLoading: boolean
  progressItems: ScriptProgressItem[]
  columns: TableColumnsType<ScriptProgressItem>
  selectedChapterIds: Key[]
  onRefresh: () => void
  onSelectRows: (filter: (item: ScriptProgressItem) => boolean) => void
  onClearSelection: () => void
  onSelectedChapterIdsChange: (keys: Key[]) => void
  onGenerateSelected: () => void
  onGenerateSelectedReuse: () => void
  onGenerateMissingSelected: () => void
  onGenerateAllMissing: () => void
  onGenerateFailed: () => void
  onGenerateFromFirstSelected: () => void
  onAnalyzeSelectedCharacters: () => void
  onAnalyzeMissingCharacters: () => void
}

function ScriptProgressPanel({
  issueSummary,
  selectedCount,
  running,
  generatePending,
  progressLoading,
  progressItems,
  columns,
  selectedChapterIds,
  onRefresh,
  onSelectRows,
  onClearSelection,
  onSelectedChapterIdsChange,
  onGenerateSelected,
  onGenerateSelectedReuse,
  onGenerateMissingSelected,
  onGenerateAllMissing,
  onGenerateFailed,
  onGenerateFromFirstSelected,
  onAnalyzeSelectedCharacters,
  onAnalyzeMissingCharacters,
}: ScriptProgressPanelProps) {
  return (
    <Card title="章节进度" extra={<Button icon={<ReloadOutlined />} onClick={onRefresh}>刷新</Button>}>
      <Row gutter={12} className="metric-row">
        <Col span={8}><Statistic title="问题章节" value={issueSummary.issue_chapters ?? 0} /></Col>
        <Col span={8}><Statistic title="总问题" value={issueSummary.total_issues ?? 0} /></Col>
        <Col span={8}><Statistic title="未知角色章节" value={issueSummary.unknown_speaker_chapters ?? 0} /></Col>
      </Row>
      <Space wrap className="mb-16">
        <Tag color={selectedCount ? 'blue' : 'default'}>已选 {formatCount(selectedCount)}</Tag>
        <Button size="small" onClick={() => onSelectRows(() => true)}>全选</Button>
        <Button size="small" onClick={onClearSelection}>清空</Button>
        <Button size="small" onClick={() => onSelectRows((item) => !item.generated && item.status !== 'done')}>缺失脚本</Button>
        <Button size="small" danger onClick={() => onSelectRows((item) => Boolean(item.failed || item.cancelled || item.status === 'failed' || item.status === 'error'))}>失败/取消</Button>
        <Button size="small" onClick={() => onSelectRows((item) => !item.character_analyzed || Boolean(item.character_analysis_failed))}>人物池未分析</Button>
        <Button size="small" onClick={() => onSelectRows((item) => Boolean(item.issue_count || item.unknown_speaker_count))}>有问题</Button>
        <Button size="small" onClick={() => onSelectRows((item) => Boolean(item.memory_stale))}>上下文过期</Button>
        <Button size="small" onClick={() => onSelectRows((item) => Boolean(item.generated || item.status === 'done'))}>已生成</Button>
      </Space>
      <Space wrap className="mb-16">
        <Button size="small" type="primary" disabled={!selectedCount || running} loading={generatePending} onClick={onGenerateSelected}>
          重跑选中
        </Button>
        <Button size="small" disabled={!selectedCount || running} loading={generatePending} onClick={onGenerateSelectedReuse}>
          复用人物池重跑选中
        </Button>
        <Button size="small" disabled={!selectedCount || running} loading={generatePending} onClick={onGenerateMissingSelected}>
          补选中缺失
        </Button>
        <Button size="small" disabled={running} loading={generatePending} onClick={onGenerateAllMissing}>
          补全所有缺失
        </Button>
        <Button size="small" danger disabled={running} loading={generatePending} onClick={onGenerateFailed}>
          重跑失败/中断
        </Button>
        <Button size="small" disabled={!selectedCount || running} loading={generatePending} onClick={onGenerateFromFirstSelected}>
          从选中章起重跑
        </Button>
        <Button size="small" disabled={!selectedCount || running} loading={generatePending} onClick={onAnalyzeSelectedCharacters}>
          分析选中人物池
        </Button>
        <Button size="small" disabled={running} loading={generatePending} onClick={onAnalyzeMissingCharacters}>
          补分析缺失人物池
        </Button>
      </Space>
      <Table<ScriptProgressItem>
        size="small"
        rowKey={(record, index) => record.chapter_id ?? String(index)}
        loading={progressLoading}
        dataSource={progressItems}
        columns={columns}
        pagination={{ pageSize: 8 }}
        rowSelection={{
          selectedRowKeys: selectedChapterIds,
          onChange: onSelectedChapterIdsChange,
          preserveSelectedRowKeys: true,
        }}
      />
    </Card>
  )
}

type UnknownSpeakersPanelProps = {
  unknownSpeakers: UnknownSpeakerRow[]
  characters: CharacterItem[]
  loading: boolean
  addPending: boolean
  onRefresh: () => void
  onAddSpeakers: (speakers: string[]) => void
  onMergeSpeaker: (speaker: string, targetName: string) => void
}

function UnknownSpeakersPanel({
  unknownSpeakers,
  characters,
  loading,
  addPending,
  onRefresh,
  onAddSpeakers,
  onMergeSpeaker,
}: UnknownSpeakersPanelProps) {
  return (
    <Card
      title="未知说话人"
      className="mt-16"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={onRefresh} />
          <Button
            disabled={!unknownSpeakers.length}
            loading={addPending}
            onClick={() => onAddSpeakers(unknownSpeakers.map((item) => item.speaker))}
          >
            全部加入人物池
          </Button>
        </Space>
      }
    >
      <Table<UnknownSpeakerRow>
        size="small"
        rowKey="speaker"
        loading={loading}
        dataSource={unknownSpeakers}
        pagination={{ pageSize: 6 }}
        locale={{ emptyText: '暂无未知说话人' }}
        columns={[
          {
            title: '说话人',
            dataIndex: 'speaker',
            render: (value) => <Tag color="warning">{value}</Tag>,
          },
          { title: '涉及章节', dataIndex: 'chapterCount', width: 96, render: formatCount },
          {
            title: '章节',
            dataIndex: 'chapters',
            render: (value: string[]) => value.slice(0, 3).join('、') || '-',
          },
          {
            title: '处理',
            width: 320,
            render: (_, record) => (
              <Space.Compact className="full-width">
                <Select
                  showSearch
                  allowClear
                  placeholder="合并到已有角色"
                  optionFilterProp="label"
                  className="flex-input"
                  options={characters.map((character) => ({
                    value: character.name,
                    label: character.name,
                  }))}
                  onChange={(targetName) => {
                    if (!targetName) return
                    onMergeSpeaker(record.speaker, targetName)
                  }}
                />
                <Button loading={addPending} onClick={() => onAddSpeakers([record.speaker])}>
                  新角色
                </Button>
              </Space.Compact>
            ),
          },
        ]}
      />
    </Card>
  )
}

function SavedScriptsList({ scripts, loading }: { scripts: SavedScript[]; loading: boolean }) {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [name, setName] = useState('')

  const saveMutation = useMutation({
    mutationFn: api.saveScript,
    onSuccess: async () => {
      message.success('当前脚本包已保存')
      setName('')
      await queryClient.invalidateQueries({ queryKey: ['saved-scripts'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const loadMutation = useMutation({
    mutationFn: api.loadScript,
    onSuccess: async () => {
      message.success('脚本包已加载')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['saved-scripts'] }),
        queryClient.invalidateQueries({ queryKey: ['chunks'] }),
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['chapters'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['script-issues'] }),
        queryClient.invalidateQueries({ queryKey: ['books'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteScript,
    onSuccess: async () => {
      message.success('脚本包已删除')
      await queryClient.invalidateQueries({ queryKey: ['saved-scripts'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  return (
    <Space direction="vertical" size={12} className="full-width">
      <Flex gap={8} wrap>
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="脚本包名称"
          className="flex-input"
          onPressEnter={() => name.trim() && saveMutation.mutate(name.trim())}
        />
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saveMutation.isPending}
          disabled={!name.trim()}
          onClick={() => saveMutation.mutate(name.trim())}
        >
          保存当前脚本
        </Button>
      </Flex>
      <List
        loading={loading}
        dataSource={scripts}
        locale={{ emptyText: '暂无保存的脚本包' }}
        renderItem={(item) => (
          <List.Item
            actions={[
              <Button
                key="load"
                size="small"
                icon={<FolderOpenOutlined />}
                loading={loadMutation.isPending}
                onClick={() => {
                  modal.confirm({
                    title: `加载脚本包「${item.name}」？`,
                    content: '这会替换当前脚本、片段、人物池和声音配置中的可用部分。',
                    okText: '加载',
                    cancelText: '取消',
                    onOk: () => loadMutation.mutate(item.name),
                  })
                }}
              >
                加载
              </Button>,
              <Button
                key="delete"
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={deleteMutation.isPending}
                onClick={() => {
                  modal.confirm({
                    title: `删除脚本包「${item.name}」？`,
                    okText: '删除',
                    okButtonProps: { danger: true },
                    cancelText: '取消',
                    onOk: () => deleteMutation.mutate(item.name),
                  })
                }}
              />,
            ]}
          >
            <List.Item.Meta
              title={item.name}
              description={`${formatCount(item.entry_count)} 条目 · ${formatCount(item.chunk_count)} 片段 · ${formatCount(item.chapter_count)} 章节 · ${item.source_book_title ?? item.book_title ?? '未知书籍'}`}
            />
            <Space wrap>
              {item.has_voice_config ? <Tag color="blue">声音</Tag> : null}
              {item.has_character_book ? <Tag color="green">人物池</Tag> : null}
              {item.has_chunks ? <Tag>片段</Tag> : null}
            </Space>
          </List.Item>
        )}
      />
    </Space>
  )
}

function TaggedScriptEditor() {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const chaptersQuery = useQuery({ queryKey: ['chapters'], queryFn: api.chapters })
  const [editor, dispatchEditor] = useReducer(taggedEditorReducer, INITIAL_TAGGED_EDITOR_STATE)
  const { scope, chapterId, defaultInstruct, content, preview, loadingTaggedScript } = editor

  const activeChapterId = scope === 'chapter' ? chapterId : undefined
  const chapters = chaptersQuery.data?.chapters ?? []

  const loadTaggedScript = async () => {
    dispatchEditor({ type: 'setLoadingTaggedScript', value: true })
    try {
      const result = await api.taggedScript(activeChapterId)
      dispatchEditor({ type: 'loadedTaggedScript', content: result.content ?? '', entryCount: result.entry_count ?? 0 })
      message.success('已读取当前 tagged 文本')
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      dispatchEditor({ type: 'setLoadingTaggedScript', value: false })
    }
  }

  const importMutation = useMutation({
    mutationFn: (dryRun: boolean) => api.importTaggedScript({
      content,
      default_instruct: defaultInstruct,
      chapter_id: activeChapterId,
      replace_scope: scope,
      dry_run: dryRun,
    }),
    onSuccess: async (result, dryRun) => {
      dispatchEditor({ type: 'setPreview', value: result })
      if (dryRun) {
        message.success('导入预览已生成')
        return
      }
      message.success(`已导入 ${formatCount(result.imported_entries)} 条 tagged 文本`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['chunks'] }),
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['script-issues'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  return (
    <Space direction="vertical" size={12} className="full-width">
      <Flex gap={12} wrap align="center">
        <Radio.Group
          value={scope}
          onChange={(event) => dispatchEditor({ type: 'setScope', value: event.target.value })}
          optionType="button"
          buttonStyle="solid"
          options={[
            { value: 'all', label: '全书' },
            { value: 'chapter', label: '单章' },
          ]}
        />
        <Select
          value={chapterId || undefined}
          disabled={scope !== 'chapter'}
          placeholder="选择章节"
          className="chapter-select"
          showSearch
          optionFilterProp="label"
          onChange={(value) => dispatchEditor({ type: 'setChapterId', value })}
          options={chapters.flatMap((chapter) => {
            const value = chapter.chapter_id ?? ''
            return value ? [{
              value,
              label: `${chapter.index ?? ''} ${chapter.title ?? chapter.chapter_title ?? chapter.chapter_id}`,
            }] : []
          })}
        />
        <Input
          value={defaultInstruct}
          onChange={(event) => dispatchEditor({ type: 'setDefaultInstruct', value: event.target.value })}
          placeholder="默认情绪/指令"
          className="compact-input"
        />
        <Button
          icon={<ReloadOutlined />}
          loading={loadingTaggedScript}
          disabled={scope === 'chapter' && !chapterId}
          onClick={loadTaggedScript}
        >
          读取现有
        </Button>
      </Flex>
      <Input.TextArea
        value={content}
        onChange={(event) => dispatchEditor({ type: 'setContent', value: event.target.value })}
        rows={10}
        className="mono textarea-editor"
        placeholder="<旁白:>这里是旁白文本&#10;<角色名:>这里是台词 {instruct=压低声音}"
      />
      <Flex justify="space-between" gap={12} wrap>
        <Space wrap>
          <Button
            loading={importMutation.isPending}
            disabled={!content.trim() || (scope === 'chapter' && !chapterId)}
            onClick={() => importMutation.mutate(true)}
          >
            预览
          </Button>
          <Button
            type="primary"
            icon={<UploadOutlined />}
            loading={importMutation.isPending}
            disabled={!content.trim() || (scope === 'chapter' && !chapterId)}
            onClick={() => importMutation.mutate(false)}
          >
            导入并重建片段
          </Button>
        </Space>
        {preview ? (
          <Text type="secondary">
            条目 {formatCount((preview.imported_entries as number | undefined) ?? (preview.entry_count as number | undefined))}
            {' · '}
            片段 {formatCount(preview.total_chunks as number | undefined)}
            {' · '}
            说话人修正 {formatCount(preview.speaker_updates as number | undefined)}
          </Text>
        ) : null}
      </Flex>
    </Space>
  )
}
