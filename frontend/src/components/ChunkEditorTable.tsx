import {
  Alert,
  App as AntApp,
  Button,
  Card,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from 'antd'
import {
  DeleteOutlined,
  CloseCircleOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SoundOutlined,
  StopOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { VoiceDrawer } from './VoiceDrawer'
import {
  chunkAudioSrc,
  fallbackVoiceForSpeaker,
  voiceConfigLabel,
} from '../domain'
import { useWorkspaceStore } from '../store'
import type { CharacterItem, Chunk, ChunkDeleteResponse, VoiceItem } from '../types'
import { statusColor, summarizeStatus } from '../utils'

const { Text } = Typography

type IndexedChunk = Chunk & { _index: number }

const EMPTY_SPEAKER_KEY = '__empty_speaker__'
const EMPTY_STATUS_KEY = '__empty_status__'
const ALL_CHUNKS_SCOPE_KEY = '__all_chunks__'

function chunkSpeakerKey(chunk: Chunk) {
  return String(chunk.speaker ?? '').trim() || EMPTY_SPEAKER_KEY
}

function chunkStatusKey(chunk: Chunk) {
  return String(chunk.status ?? '').trim() || EMPTY_STATUS_KEY
}

function speakerLabel(value: string) {
  return value === EMPTY_SPEAKER_KEY ? '未填写说话人' : value
}

function statusLabel(value: string) {
  return value === EMPTY_STATUS_KEY ? '未知' : summarizeStatus(value)
}

type ChunkEditorTableProps = {
  chapterId?: string
  className?: string
  title?: string
}

type ChunkFilterState = {
  scopeKey: string
  speakers: string[]
  statuses: string[]
}

type SaveField = 'speaker' | 'instruct' | 'pause_after' | 'text'

type SaveState = {
  status: 'saving' | 'saved' | 'error'
  body: Record<string, unknown>
  error?: string
}

function saveKey(index: number, field: SaveField) {
  return `${index}:${field}`
}

function formatSaveStatus(state?: SaveState) {
  if (!state) return ''
  if (state.status === 'saving') return '保存中...'
  if (state.status === 'saved') return '已保存'
  return '保存失败'
}

export function ChunkEditorTable({ chapterId, className, title = '音频片段编辑' }: ChunkEditorTableProps) {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const chunkScopeKey = chapterId ?? ALL_CHUNKS_SCOPE_KEY
  const [lastDeleted, setLastDeleted] = useState<{ chunk: Chunk; atIndex: number } | null>(null)
  const [editingVoice, setEditingVoice] = useState<(VoiceItem | CharacterItem) & { chunkIndex?: number } | null>(null)
  const [chunkPagination, setChunkPagination] = useState({ current: 1, pageSize: 8 })
  const [chunkFilters, setChunkFilters] = useState<ChunkFilterState>({
    scopeKey: chunkScopeKey,
    speakers: [],
    statuses: [],
  })
  const [sequencePlaying, setSequencePlaying] = useState(false)
  const [sequenceIndex, setSequenceIndex] = useState<number | null>(null)
  const [saveStates, setSaveStates] = useState<Record<string, SaveState>>({})
  const currentBookId = useWorkspaceStore((state) => state.currentBookId)
  const audioRefs = useRef(new Map<number, HTMLAudioElement>())

  const chunksQuery = useQuery({
    queryKey: ['chunks'],
    queryFn: api.chunks,
    refetchInterval: (query) => query.state.data?.some((chunk) => chunk.status === 'generating') ? 2000 : false,
  })
  const charactersQuery = useQuery({ queryKey: ['characters'], queryFn: () => api.characters() })
  const voicesQuery = useQuery({ queryKey: ['voices'], queryFn: () => api.voices() })
  const edgeVoicesQuery = useQuery({ queryKey: ['edge-voices'], queryFn: api.edgeVoices })
  const volcengineVoicesQuery = useQuery({ queryKey: ['volcengine-voices'], queryFn: () => api.volcengineVoices() })

  const invalidateChunkState = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['chunks'] }),
      queryClient.invalidateQueries({ queryKey: ['chapter-tts-progress'] }),
    ])
  }, [queryClient])

  const updateMutation = useMutation({
    mutationFn: ({ index, body }: { index: number; body: Record<string, unknown>; field?: SaveField }) => api.updateChunk(index, body),
    onSuccess: async (_, variables) => {
      if (variables.field) {
        const key = saveKey(variables.index, variables.field)
        setSaveStates((current) => ({
          ...current,
          [key]: { status: 'saved', body: variables.body },
        }))
      }
      const invalidations = [
        invalidateChunkState(),
      ]
      if ('speaker' in variables.body) {
        invalidations.push(
          queryClient.invalidateQueries({ queryKey: ['voices'] }),
          queryClient.invalidateQueries({ queryKey: ['characters'] }),
        )
      }
      await Promise.all(invalidations)
    },
    onError: (error: Error, variables) => {
      if (variables.field) {
        const key = saveKey(variables.index, variables.field)
        setSaveStates((current) => ({
          ...current,
          [key]: { status: 'error', body: variables.body, error: error.message },
        }))
      }
      message.error(error.message)
    },
  })

  const saveChunkField = (index: number, field: SaveField, body: Record<string, unknown>) => {
    const key = saveKey(index, field)
    setSaveStates((current) => ({
      ...current,
      [key]: { status: 'saving', body },
    }))
    updateMutation.mutate({ index, field, body })
  }

  const renderSaveHint = (index: number, field: SaveField) => {
    const state = saveStates[saveKey(index, field)]
    if (!state) return null
    return (
      <Space size={6} className="chunk-save-state">
        <Tag color={state.status === 'error' ? 'error' : state.status === 'saving' ? 'processing' : 'success'}>
          {formatSaveStatus(state)}
        </Tag>
        {state.status === 'error' ? (
          <Button size="small" type="link" onClick={() => saveChunkField(index, field, state.body)}>
            重试
          </Button>
        ) : null}
      </Space>
    )
  }

  const generateMutation = useMutation({
    mutationFn: api.generateChunk,
    onSuccess: async () => {
      message.success('片段生成已启动')
      await invalidateChunkState()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const insertMutation = useMutation({
    mutationFn: api.insertChunk,
    onSuccess: async () => {
      message.success('已插入空白片段')
      await invalidateChunkState()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteChunk,
    onSuccess: async (result: ChunkDeleteResponse, index) => {
      if (result.deleted) {
        setLastDeleted({ chunk: result.deleted, atIndex: index })
      }
      message.success('片段已删除')
      await invalidateChunkState()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const restoreMutation = useMutation({
    mutationFn: ({ chunk, atIndex }: { chunk: Chunk; atIndex: number }) => api.restoreChunk({ chunk, at_index: atIndex }),
    onSuccess: async () => {
      setLastDeleted(null)
      message.success('片段已恢复')
      await Promise.all([
        invalidateChunkState(),
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['script-issues'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const indexedChunks = useMemo<IndexedChunk[]>(
    () => (chunksQuery.data ?? []).map((chunk, index) => ({ ...chunk, _index: index })),
    [chunksQuery.data],
  )
  const visibleChunks = useMemo(
    () => chapterId ? indexedChunks.filter((chunk) => chunk.chapter_id === chapterId) : indexedChunks,
    [chapterId, indexedChunks],
  )
  const speakerOptions = useMemo(() => {
    const counts = new Map<string, number>()
    visibleChunks.forEach((chunk) => {
      const key = chunkSpeakerKey(chunk)
      counts.set(key, (counts.get(key) ?? 0) + 1)
    })
    return [...counts.entries()]
      .sort(([speakerA], [speakerB]) => speakerLabel(speakerA).localeCompare(speakerLabel(speakerB), 'zh-CN'))
      .map(([value, count]) => ({
        value,
        label: `${speakerLabel(value)} (${count})`,
      }))
  }, [visibleChunks])
  const statusOptions = useMemo(() => {
    const counts = new Map<string, number>()
    visibleChunks.forEach((chunk) => {
      const key = chunkStatusKey(chunk)
      counts.set(key, (counts.get(key) ?? 0) + 1)
    })
    return [...counts.entries()]
      .sort(([statusA], [statusB]) => statusLabel(statusA).localeCompare(statusLabel(statusB), 'zh-CN'))
      .map(([value, count]) => ({
        value,
        label: `${statusLabel(value)} (${count})`,
      }))
  }, [visibleChunks])
  const speakerOptionValues = useMemo(
    () => new Set(speakerOptions.map((option) => option.value)),
    [speakerOptions],
  )
  const statusOptionValues = useMemo(
    () => new Set(statusOptions.map((option) => option.value)),
    [statusOptions],
  )
  const currentSpeakerFilters = useMemo(() => {
    if (chunkFilters.scopeKey !== chunkScopeKey) return []
    return chunkFilters.speakers.filter((value) => speakerOptionValues.has(value))
  }, [chunkFilters, chunkScopeKey, speakerOptionValues])
  const currentStatusFilters = useMemo(() => {
    if (chunkFilters.scopeKey !== chunkScopeKey) return []
    return chunkFilters.statuses.filter((value) => statusOptionValues.has(value))
  }, [chunkFilters, chunkScopeKey, statusOptionValues])
  const filteredChunks = useMemo(() => {
    const selectedSpeakers = new Set(currentSpeakerFilters)
    const selectedStatuses = new Set(currentStatusFilters)
    return visibleChunks.filter((chunk) => (
      (!selectedSpeakers.size || selectedSpeakers.has(chunkSpeakerKey(chunk)))
      && (!selectedStatuses.size || selectedStatuses.has(chunkStatusKey(chunk)))
    ))
  }, [currentSpeakerFilters, currentStatusFilters, visibleChunks])
  const filtersActive = currentSpeakerFilters.length > 0 || currentStatusFilters.length > 0
  const maxPage = Math.max(1, Math.ceil(filteredChunks.length / chunkPagination.pageSize))
  const tablePagination = chapterId ? false : {
    current: Math.min(chunkPagination.current, maxPage),
    pageSize: chunkPagination.pageSize,
    showSizeChanger: true,
    pageSizeOptions: [8, 20, 50, 100],
    showTotal: (total: number, range: [number, number]) => `${range[0]}-${range[1]} / ${total}`,
    onChange: (current: number, pageSize: number) => {
      const nextMaxPage = Math.max(1, Math.ceil(filteredChunks.length / pageSize))
      setChunkPagination({ current: Math.min(current, nextMaxPage), pageSize })
    },
  }
  const characters = charactersQuery.data?.characters ?? []
  const voices = voicesQuery.data ?? []

  const findVoiceForChunk = (chunk: Chunk) => {
    const speaker = String(chunk.speaker ?? '').trim()
    if (!speaker) return null
    const speakerKey = speaker.toLowerCase()
    return voices.find((voice) => String(voice.name ?? '').trim().toLowerCase() === speakerKey)
      ?? characters.find((character) => String(character.name ?? '').trim().toLowerCase() === speakerKey)
      ?? fallbackVoiceForSpeaker(speaker)
  }

  const openVoiceConfig = (chunk: IndexedChunk) => {
    const voice = findVoiceForChunk(chunk)
    if (!voice) {
      message.warning('请先填写说话人')
      return
    }
    setEditingVoice({ ...voice, chunkIndex: chunk._index })
  }

  const stopSequence = useCallback(() => {
    setSequencePlaying(false)
    setSequenceIndex(null)
    audioRefs.current.forEach((audio) => {
      audio.pause()
      audio.currentTime = 0
      audio.onended = null
      audio.onerror = null
    })
  }, [])

  const playSequenceFrom = (startIndex = 0) => {
    const playableIndexes = filteredChunks
      .filter((chunk) => chunkAudioSrc(chunk, currentBookId))
      .map((chunk) => chunk._index)
    const firstIndex = playableIndexes.find((index) => index >= startIndex) ?? playableIndexes[0]
    if (firstIndex === undefined) {
      message.warning('当前没有可播放的音频片段')
      return
    }
    setSequencePlaying(true)
    setSequenceIndex(firstIndex)
  }

  useEffect(() => {
    if (!sequencePlaying || sequenceIndex === null) return
    const audio = audioRefs.current.get(sequenceIndex)
    if (!audio) {
      const nextIndex = filteredChunks.find((chunk) => chunk._index > sequenceIndex && chunkAudioSrc(chunk, currentBookId))?._index
      window.setTimeout(() => {
        if (nextIndex === undefined) {
          stopSequence()
        } else {
          setSequenceIndex(nextIndex)
        }
      }, 0)
      return
    }

    audioRefs.current.forEach((item, index) => {
      if (index !== sequenceIndex) item.pause()
    })

    audio.currentTime = 0
    audio.scrollIntoView({ behavior: 'smooth', block: 'center' })
    audio.onended = () => {
      const nextIndex = filteredChunks.find((chunk) => chunk._index > sequenceIndex && chunkAudioSrc(chunk, currentBookId))?._index
      if (nextIndex === undefined) {
        stopSequence()
      } else {
        setSequenceIndex(nextIndex)
      }
    }
    audio.onerror = audio.onended
    void audio.play().catch(() => {
      audio.onended?.(new Event('ended'))
    })
  }, [currentBookId, filteredChunks, sequenceIndex, sequencePlaying, stopSequence])

  useEffect(() => {
    const timeout = window.setTimeout(stopSequence, 0)
    return () => window.clearTimeout(timeout)
  }, [chapterId, stopSequence])
  useEffect(() => () => stopSequence(), [stopSequence])

  return (
    <>
      <Card
        title={title}
        className={className}
        extra={
          <Space>
            <Button
              type={sequencePlaying ? 'default' : 'primary'}
              danger={sequencePlaying}
              icon={sequencePlaying ? <StopOutlined /> : <PlayCircleOutlined />}
              onClick={() => {
                if (sequencePlaying) {
                  stopSequence()
                } else {
                  playSequenceFrom(0)
                }
              }}
            >
              {sequencePlaying ? '停止' : '顺序播放'}
            </Button>
            {lastDeleted ? (
              <Button
                loading={restoreMutation.isPending}
                onClick={() => restoreMutation.mutate(lastDeleted)}
              >
                恢复最近删除
              </Button>
            ) : null}
            <Button icon={<ReloadOutlined />} onClick={() => chunksQuery.refetch()}>刷新</Button>
          </Space>
        }
      >
        {lastDeleted ? (
          <Alert
            type="info"
            showIcon
            className="mb-16"
            message={`最近删除：第 ${lastDeleted.atIndex + 1} 行 · ${lastDeleted.chunk.speaker ?? '未知说话人'} · ${String(lastDeleted.chunk.text ?? '').slice(0, 48)}`}
            action={(
              <Button size="small" loading={restoreMutation.isPending} onClick={() => restoreMutation.mutate(lastDeleted)}>
                恢复
              </Button>
            )}
          />
        ) : null}
        <Space className="chunk-filter-bar" size={12} wrap>
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            className="chunk-filter-select"
            placeholder="筛选说话人"
            options={speakerOptions}
            value={currentSpeakerFilters}
            onChange={(speakers) => setChunkFilters((current) => ({
              scopeKey: chunkScopeKey,
              speakers,
              statuses: current.scopeKey === chunkScopeKey ? current.statuses : [],
            }))}
          />
          <Select
            mode="multiple"
            allowClear
            maxTagCount="responsive"
            className="chunk-filter-select"
            placeholder="筛选状态"
            options={statusOptions}
            value={currentStatusFilters}
            onChange={(statuses) => setChunkFilters((current) => ({
              scopeKey: chunkScopeKey,
              speakers: current.scopeKey === chunkScopeKey ? current.speakers : [],
              statuses,
            }))}
          />
          {filtersActive ? (
            <Button
              icon={<CloseCircleOutlined />}
              onClick={() => setChunkFilters({ scopeKey: chunkScopeKey, speakers: [], statuses: [] })}
            >
              清除筛选
            </Button>
          ) : null}
          <Text type="secondary">
            显示 {filteredChunks.length} / {visibleChunks.length} 个片段
          </Text>
        </Space>
        <Table<IndexedChunk>
          rowKey={(record) => String(record._index)}
          loading={chunksQuery.isLoading || charactersQuery.isLoading || voicesQuery.isLoading}
          dataSource={filteredChunks}
          pagination={tablePagination}
          scroll={{ x: 1260 }}
          rowClassName={(record) => (record._index === sequenceIndex ? 'editor-sequence-row' : '')}
          columns={[
            { title: '#', width: 64, render: (_, record) => record._index + 1 },
            {
              title: '说话人',
              dataIndex: 'speaker',
              width: 180,
              render: (value, record) => {
                const voice = findVoiceForChunk(record)
                const label = voiceConfigLabel(voice?.config)
                return (
                  <Space direction="vertical" size={6} className="full-width">
                    <Input
                      defaultValue={String(value ?? '')}
                      onBlur={(event) => saveChunkField(record._index, 'speaker', { speaker: event.target.value })}
                    />
                    {renderSaveHint(record._index, 'speaker')}
                    <Tooltip title={voice ? `当前说话人声音配置：${label}` : '先填写说话人'}>
                      <Button
                        size="small"
                        icon={<SoundOutlined />}
                        className="full-width"
                        disabled={!String(record.speaker ?? '').trim()}
                        onClick={() => openVoiceConfig(record)}
                      >
                        {label}
                      </Button>
                    </Tooltip>
                  </Space>
                )
              },
            },
            {
              title: '情绪/指令',
              dataIndex: 'instruct',
              width: 260,
              render: (value, record) => (
                <Space direction="vertical" size={6} className="full-width">
                  <Input.TextArea
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    defaultValue={String(value ?? '')}
                    onBlur={(event) => saveChunkField(record._index, 'instruct', { instruct: event.target.value })}
                  />
                  {renderSaveHint(record._index, 'instruct')}
                  <Space size={6} className="editor-pause-control">
                    <Text type="secondary">后置停顿</Text>
                    <InputNumber
                      min={0}
                      step={50}
                      addonAfter="ms"
                      className="editor-pause-input"
                      defaultValue={typeof record.pause_after === 'number' ? record.pause_after : undefined}
                      onBlur={(event) => {
                        const input = event.target as HTMLInputElement
                        saveChunkField(record._index, 'pause_after', { pause_after: input.value === '' ? null : Number(input.value) })
                      }}
                    />
                  </Space>
                  {renderSaveHint(record._index, 'pause_after')}
                </Space>
              ),
            },
            {
              title: '文本',
              dataIndex: 'text',
              render: (value, record) => (
                <Space direction="vertical" size={6} className="full-width">
                  <Input.TextArea
                    autoSize={{ minRows: 2, maxRows: 6 }}
                    defaultValue={String(value ?? '')}
                    onBlur={(event) => saveChunkField(record._index, 'text', { text: event.target.value })}
                  />
                  {renderSaveHint(record._index, 'text')}
                </Space>
              ),
            },
            { title: '状态', dataIndex: 'status', width: 100, render: (value) => <Tag color={statusColor(value)}>{summarizeStatus(value)}</Tag> },
            {
              title: '音频',
              width: 240,
              render: (_, record) => {
                const src = chunkAudioSrc(record, currentBookId)
                return src ? (
                  <audio
                    ref={(node) => {
                      if (node) {
                        audioRefs.current.set(record._index, node)
                      } else {
                        audioRefs.current.delete(record._index)
                      }
                    }}
                    className="audio-player"
                    controls
                    src={src}
                    onPlay={() => {
                      if (!sequencePlaying) {
                        audioRefs.current.forEach((audio, audioIndex) => {
                          if (audioIndex !== record._index) audio.pause()
                        })
                      }
                    }}
                  />
                ) : <Text type="secondary">未生成</Text>
              },
            },
            {
              title: '操作',
              width: 180,
              fixed: 'right',
              render: (_, record) => (
                <Space>
                  <Button size="small" icon={<PlayCircleOutlined />} loading={generateMutation.isPending} onClick={() => generateMutation.mutate(record._index)}>
                    生成
                  </Button>
                  <Tooltip title="在当前片段后插入">
                    <Button size="small" icon={<PlusOutlined />} loading={insertMutation.isPending} onClick={() => insertMutation.mutate(record._index)} />
                  </Tooltip>
                  <Tooltip title="删除片段">
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      loading={deleteMutation.isPending}
                      onClick={() => {
                        modal.confirm({
                          title: `删除第 ${record._index + 1} 个片段？`,
                          content: '删除后会同步更新当前脚本。',
                          okText: '删除',
                          okButtonProps: { danger: true },
                          cancelText: '取消',
                          onOk: () => deleteMutation.mutate(record._index),
                        })
                      }}
                    />
                  </Tooltip>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <VoiceDrawer
        voice={editingVoice}
        edgeVoices={edgeVoicesQuery.data ?? []}
        volcengineVoices={volcengineVoicesQuery.data?.voices ?? {}}
        onRefreshVolcengineVoices={() => volcengineVoicesQuery.refetch()}
        onSaveAndGenerate={async () => {
          if (typeof editingVoice?.chunkIndex !== 'number') return
          await api.generateChunk(editingVoice.chunkIndex)
          await invalidateChunkState()
        }}
        onClose={() => setEditingVoice(null)}
      />
    </>
  )
}
