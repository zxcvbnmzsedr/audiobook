import {
  App as AntApp,
  Button,
  Card,
  Col,
  Dropdown,
  Flex,
  Form,
  Input,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { AudioOutlined, MoreOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../api'
import { ChunkEditorTable } from '../components/ChunkEditorTable'
import { TaskStatusCard } from '../components/common'
import type { ChapterTtsProgressItem, ChapterTtsProgressResponse } from '../types'
import { formatCount } from '../utils'

const { Text } = Typography

function chapterProgressText(chapter: ChapterTtsProgressItem) {
  return [
    `音频 ${formatCount(chapter.audio_chunks)}/${formatCount(chapter.total_chunks)}`,
    `待处理 ${formatCount(chapter.pending_chunks)}`,
    `生成中 ${formatCount(chapter.generating_chunks)}`,
    `错误 ${formatCount(chapter.error_chunks)}`,
  ].join('，')
}

function buildIncompleteChapterMessage(progress?: ChapterTtsProgressResponse) {
  const summary = progress?.summary ?? {}
  const incomplete = (progress?.chapters ?? []).filter((chapter) => !chapter.complete)
  if (!incomplete.length) return ''

  const preview = incomplete
    .slice(0, 6)
    .map((chapter) => `${chapter.chapter_title ?? chapter.chapter_id ?? '未归属章节'}（${chapterProgressText(chapter)}）`)
    .join('\n')
  const more = incomplete.length > 6 ? `\n另有 ${formatCount(incomplete.length - 6)} 章未完成。` : ''
  return `还有 ${formatCount(summary.incomplete_chapters ?? incomplete.length)} 章未完成，最终合并只会包含已生成的音频片段。\n\n${preview}${more}\n\n是否仍然合并全部已有音频？`
}

export function ResultStep() {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const chaptersQuery = useQuery({ queryKey: ['chapters'], queryFn: api.chapters })
  const [chapterId, setChapterId] = useState<string | undefined>()
  const [renderPlan, setRenderPlan] = useState<Record<string, unknown> | null>(null)
  const [m4bOpen, setM4bOpen] = useState(false)
  const [chapterAudioUrl, setChapterAudioUrl] = useState('')
  const [m4bForm] = Form.useForm<{
    title: string
    author: string
    narrator: string
    year: string
    description: string
    per_chunk_chapters: boolean
  }>()
  const chunksQuery = useQuery({
    queryKey: ['chunks'],
    queryFn: api.chunks,
    refetchInterval: (query) => query.state.data?.some((chunk) => chunk.status === 'generating') ? 2000 : false,
  })
  const audioStatusQuery = useQuery({
    queryKey: ['task-status', 'audio'],
    queryFn: () => api.taskStatus('audio'),
    refetchInterval: (query) => query.state.data?.running ? 1500 : false,
  })
  const audacityStatusQuery = useQuery({
    queryKey: ['task-status', 'audacity_export'],
    queryFn: () => api.taskStatus('audacity_export'),
    refetchInterval: (query) => query.state.data?.running ? 1500 : false,
  })
  const m4bStatusQuery = useQuery({
    queryKey: ['task-status', 'm4b_export'],
    queryFn: () => api.taskStatus('m4b_export'),
    refetchInterval: (query) => query.state.data?.running ? 1500 : false,
  })
  const chapterProgressQuery = useQuery({
    queryKey: ['chapter-tts-progress'],
    queryFn: api.chapterProgress,
    refetchInterval: audioStatusQuery.data?.running ? 2000 : false,
  })
  const batchBody = (regenerate = false, targetChapterId: string | null | undefined = chapterId) => ({
    ...(targetChapterId ? { chapter_id: targetChapterId } : {}),
    regenerate_all: regenerate,
  })
  const renderPlanMutation = useMutation({
    mutationFn: () => api.renderPlan(batchBody()),
    onSuccess: (result) => {
      setRenderPlan(result)
      message.success('渲染计划已更新')
    },
    onError: (error: Error) => message.error(error.message),
  })
  const generateMutation = useMutation({
    mutationFn: ({ fast, regenerate, targetChapterId }: { fast: boolean; regenerate?: boolean; targetChapterId?: string }) =>
      fast ? api.generateBatchFast(batchBody(regenerate, targetChapterId)) : api.generateBatch(batchBody(regenerate, targetChapterId)),
    onSuccess: async () => {
      message.success('批量生成已启动')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['task-status', 'audio'] }),
        queryClient.invalidateQueries({ queryKey: ['chapter-tts-progress'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })
  const mergeMutation = useMutation({
    mutationFn: api.mergeAudio,
    onSuccess: () => message.success('合并任务已启动'),
    onError: (error: Error) => message.error(error.message),
  })
  const cancelMutation = useMutation({
    mutationFn: api.cancelAudio,
    onSuccess: () => message.success('已请求取消音频任务'),
    onError: (error: Error) => message.error(error.message),
  })
  const audacityMutation = useMutation({
    mutationFn: api.exportAudacity,
    onSuccess: async () => {
      message.success('Audacity 导出已启动')
      await queryClient.invalidateQueries({ queryKey: ['task-status', 'audacity_export'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const m4bMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.exportM4b(values),
    onSuccess: async () => {
      message.success('M4B 导出已启动')
      setM4bOpen(false)
      await queryClient.invalidateQueries({ queryKey: ['task-status', 'm4b_export'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const mergeChapterMutation = useMutation({
    mutationFn: (targetChapterId?: string) => api.mergeChapterAudio(targetChapterId ?? chapterId ?? ''),
    onSuccess: async (result) => {
      message.success('当前章节音频已合并')
      setChapterAudioUrl(result.audio_url ?? '')
      await queryClient.invalidateQueries({ queryKey: ['chapter-tts-progress'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const coverMutation = useMutation({
    mutationFn: api.uploadM4bCover,
    onSuccess: () => message.success('M4B 封面已上传'),
    onError: (error: Error) => message.error(error.message),
  })
  const deleteCoverMutation = useMutation({
    mutationFn: api.deleteM4bCover,
    onSuccess: () => message.success('M4B 封面已删除'),
    onError: (error: Error) => message.error(error.message),
  })

  const chunks = chunksQuery.data ?? []
  const visibleChunks = chapterId ? chunks.filter((item) => item.chapter_id === chapterId) : chunks
  const chapterProgressItems = chapterProgressQuery.data?.chapters ?? []
  const chapterProgressSummary = chapterProgressQuery.data?.summary ?? {}
  const selectedChapterProgress = chapterId
    ? chapterProgressItems.find((item) => item.chapter_id === chapterId)
    : undefined
  const done = visibleChunks.filter((item) => item.status === 'done').length
  const failed = visibleChunks.filter((item) => item.status === 'error').length
  const progress = visibleChunks.length ? Math.round((done / visibleChunks.length) * 100) : 0
  const chapterProgressPercent = (item: ChapterTtsProgressItem) => {
    const total = Number(item.total_chunks ?? 0)
    if (!total) return 0
    return Math.round((Number(item.audio_chunks ?? 0) / total) * 100)
  }
  const confirmMergeAudio = async () => {
    const progress = chapterProgressQuery.data ?? (await chapterProgressQuery.refetch()).data
    const mergePrompt = buildIncompleteChapterMessage(progress) || '要将所有有效音频片段合并为最终有声书吗？'
    modal.confirm({
      title: '合并 MP3',
      content: <Typography.Paragraph className="pre-line-text">{mergePrompt}</Typography.Paragraph>,
      okText: '确认合并',
      cancelText: '取消',
      onOk: () => mergeMutation.mutate(),
    })
  }
  const missingButtonLabel = chapterId ? '生成本章缺失' : '生成全书缺失'
  const regenerateButtonLabel = chapterId ? '重新生成本章' : '重新生成全书'
  const confirmRegenerate = ({ targetChapterId = chapterId }: {
    targetChapterId?: string
  } = {}) => {
    const targetChapterProgress = targetChapterId
      ? chapterProgressItems.find((item) => item.chapter_id === targetChapterId)
      : undefined
    const label = targetChapterId ? targetChapterProgress?.chapter_title ?? targetChapterId : '全书'
    modal.confirm({
      title: `重新生成${label}的已完成片段？`,
      content: '这会覆盖已有音频，适合在修改文本、说话人或音色后重新出声。',
      okText: '重新生成',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => generateMutation.mutate({
        fast: false,
        regenerate: true,
        targetChapterId,
      }),
    })
  }
  const generateMoreMenu = {
    items: [
      {
        key: 'regenerate',
        label: regenerateButtonLabel,
        danger: true,
        disabled: Boolean(audioStatusQuery.data?.running || generateMutation.isPending),
      },
    ],
    onClick: ({ key }: { key: string }) => {
      if (key === 'regenerate') confirmRegenerate()
    },
  }

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={8}>
        <Card title="音频生成">
          <Space direction="vertical" size={16} className="full-width">
            <Progress percent={progress} status={failed ? 'exception' : audioStatusQuery.data?.running ? 'active' : 'normal'} />
            <Row gutter={12}>
              <Col span={8}><Statistic title="片段" value={visibleChunks.length} /></Col>
              <Col span={8}><Statistic title="完成" value={done} /></Col>
              <Col span={8}><Statistic title="失败" value={failed} /></Col>
            </Row>
            <Space.Compact className="full-width">
              <Button type={chapterId ? 'default' : 'primary'} onClick={() => setChapterId(undefined)}>
                查看全书
              </Button>
              <Select
                allowClear
                className="flex-input"
                value={chapterId}
                placeholder="选择章节查看"
                showSearch
                optionFilterProp="label"
                onChange={setChapterId}
                options={(chaptersQuery.data?.chapters ?? []).map((chapter) => ({
                  value: chapter.chapter_id ?? '',
                  label: `${chapter.index ?? ''} ${chapter.title ?? chapter.chapter_title ?? chapter.chapter_id}`,
                })).filter((option) => option.value)}
              />
            </Space.Compact>
            {selectedChapterProgress ? (
              <Card size="small" title="当前章节完成度">
                <Space direction="vertical" size={8} className="full-width">
                  <Flex justify="space-between" align="center">
                    <Text>{selectedChapterProgress.chapter_title ?? selectedChapterProgress.chapter_id}</Text>
                    <Tag color={selectedChapterProgress.complete ? 'success' : selectedChapterProgress.error_chunks ? 'error' : 'default'}>
                      {selectedChapterProgress.complete ? '完整' : selectedChapterProgress.error_chunks ? '有错误' : '未完成'}
                    </Tag>
                  </Flex>
                  <Text type="secondary">
                    音频 {formatCount(selectedChapterProgress.audio_chunks)}/{formatCount(selectedChapterProgress.total_chunks)}
                    ，待处理 {formatCount(selectedChapterProgress.pending_chunks)}
                    ，生成中 {formatCount(selectedChapterProgress.generating_chunks)}
                    ，错误 {formatCount(selectedChapterProgress.error_chunks)}
                  </Text>
                </Space>
              </Card>
            ) : null}
            <Space direction="vertical" size={12} className="audio-action-groups">
              <div className="audio-action-group">
                <Text strong>生成</Text>
                <Space wrap>
                  <Button loading={renderPlanMutation.isPending} onClick={() => renderPlanMutation.mutate()}>
                    预览计划
                  </Button>
                  <Button type="primary" icon={<PlayCircleOutlined />} disabled={audioStatusQuery.data?.running} loading={generateMutation.isPending} onClick={() => generateMutation.mutate({ fast: false })}>
                    {missingButtonLabel}
                  </Button>
                  <Button icon={<PlayCircleOutlined />} disabled={audioStatusQuery.data?.running} loading={generateMutation.isPending} onClick={() => generateMutation.mutate({ fast: true })}>
                    快速生成
                  </Button>
                  <Dropdown menu={generateMoreMenu} trigger={['click']} disabled={audioStatusQuery.data?.running || generateMutation.isPending}>
                    <Button icon={<MoreOutlined />} loading={generateMutation.isPending}>
                      更多操作
                    </Button>
                  </Dropdown>
                  <Button danger icon={<StopOutlined />} disabled={!audioStatusQuery.data?.running} loading={cancelMutation.isPending} onClick={() => cancelMutation.mutate()}>
                    取消任务
                  </Button>
                </Space>
              </div>
              <div className="audio-action-group">
                <Text strong>当前章节输出</Text>
                <Space wrap>
                  <Button disabled={!chapterId} loading={mergeChapterMutation.isPending} onClick={() => mergeChapterMutation.mutate(chapterId)}>
                    合并当前章
                  </Button>
                  <Button
                    disabled={!chapterId}
                    onClick={() => chapterId && window.open(api.chapterAudiobookUrl(chapterId), '_blank')}
                  >
                    下载当前章
                  </Button>
                </Space>
              </div>
              <div className="audio-action-group">
                <Text strong>合并与下载</Text>
                <Space wrap>
                  <Button icon={<AudioOutlined />} loading={mergeMutation.isPending} onClick={confirmMergeAudio}>
                    合并全书 MP3
                  </Button>
                  <Button onClick={() => window.open('/api/audiobook', '_blank')}>
                    下载 MP3
                  </Button>
                  <Button onClick={() => window.open('/api/audiobook_m4b', '_blank')}>
                    下载 M4B
                  </Button>
                </Space>
              </div>
            </Space>
            {renderPlan ? (
              <Card size="small" title="渲染计划">
                <Row gutter={12}>
                  <Col span={8}><Statistic title="将生成" value={Number(renderPlan.total_selected ?? 0)} /></Col>
                  <Col span={8}><Statistic title="对象内片段" value={Number(renderPlan.total_scoped ?? 0)} /></Col>
                  <Col span={8}><Statistic title="缺失音频" value={Number(renderPlan.missing_audio_count ?? 0)} /></Col>
                </Row>
                <Text type="secondary">{String(renderPlan.scope_label ?? '全书')}</Text>
              </Card>
            ) : null}
            <TaskStatusCard status={audioStatusQuery.data} />
            {chapterAudioUrl ? <audio className="audio-player" controls src={chapterAudioUrl} /> : null}
          </Space>
        </Card>
        <Card title="导出" className="mt-16">
          <Space direction="vertical" size={12} className="full-width">
            <Space direction="vertical" size={12} className="audio-action-groups">
              <div className="audio-action-group">
                <Text strong>Audacity</Text>
                <Space wrap>
                  <Button loading={audacityMutation.isPending} onClick={() => audacityMutation.mutate()}>
                    生成 ZIP 包
                  </Button>
                  <Button onClick={() => window.open('/api/export_audacity', '_blank')}>
                    下载 ZIP
                  </Button>
                </Space>
              </div>
              <div className="audio-action-group">
                <Text strong>M4B</Text>
                <Space wrap>
                  <Button onClick={() => setM4bOpen(true)}>
                    生成 M4B
                  </Button>
                  <Upload
                    accept="image/*"
                    showUploadList={false}
                    beforeUpload={(file) => {
                      coverMutation.mutate(file)
                      return false
                    }}
                  >
                    <Button loading={coverMutation.isPending}>上传封面</Button>
                  </Upload>
                  <Button danger loading={deleteCoverMutation.isPending} onClick={() => deleteCoverMutation.mutate()}>
                    删除封面
                  </Button>
                  <Button onClick={() => window.open('/api/audiobook_m4b', '_blank')}>
                    下载 M4B
                  </Button>
                </Space>
              </div>
            </Space>
            <TaskStatusCard status={audacityStatusQuery.data} />
            <TaskStatusCard status={m4bStatusQuery.data} />
          </Space>
        </Card>
      </Col>
      <Col xs={24} xl={16}>
        <Card
          title="章节完成度"
          extra={<Button icon={<ReloadOutlined />} onClick={() => chapterProgressQuery.refetch()}>刷新</Button>}
        >
          <Row gutter={12} className="metric-row">
            <Col span={6}><Statistic title="章节" value={chapterProgressSummary.total_chapters ?? 0} /></Col>
            <Col span={6}><Statistic title="完整章节" value={chapterProgressSummary.complete_chapters ?? 0} /></Col>
            <Col span={6}><Statistic title="音频片段" value={chapterProgressSummary.audio_chunks ?? 0} /></Col>
            <Col span={6}><Statistic title="缺音频" value={chapterProgressSummary.missing_audio_chunks ?? 0} /></Col>
          </Row>
          <Table<ChapterTtsProgressItem>
            size="small"
            rowKey={(record, index) => record.chapter_id || String(index)}
            loading={chapterProgressQuery.isLoading}
            dataSource={chapterProgressItems}
            pagination={{ pageSize: 5 }}
            columns={[
              {
                title: '章节',
                render: (_, record) => `${record.chapter_index ? `#${record.chapter_index} ` : ''}${record.chapter_title ?? record.chapter_id ?? '未归属章节'}`,
              },
              {
                title: '完成度',
                width: 180,
                render: (_, record) => <Progress percent={chapterProgressPercent(record)} size="small" />,
              },
              {
                title: '状态',
                width: 100,
                render: (_, record) => (
                  <Tag color={record.complete ? 'success' : record.error_chunks ? 'error' : record.generating_chunks ? 'processing' : 'default'}>
                    {record.complete ? '完整' : record.error_chunks ? '有错误' : record.generating_chunks ? '生成中' : '未完成'}
                  </Tag>
                ),
              },
              {
                title: '片段',
                width: 160,
                render: (_, record) => `${formatCount(record.audio_chunks)}/${formatCount(record.total_chunks)} 音频`,
              },
              {
                title: '操作',
                width: 340,
                render: (_, record) => (
                  <Space>
                    <Button size="small" disabled={!record.chapter_id} onClick={() => setChapterId(record.chapter_id)}>
                      查看
                    </Button>
                    <Button
                      size="small"
                      disabled={!record.chapter_id || !record.total_chunks || record.complete || audioStatusQuery.data?.running}
                      loading={generateMutation.isPending}
                      onClick={() => {
                        setChapterId(record.chapter_id)
                        generateMutation.mutate({ fast: false, regenerate: false, targetChapterId: record.chapter_id })
                      }}
                    >
                      生成缺失
                    </Button>
                    <Button
                      size="small"
                      disabled={!record.chapter_id || !record.total_chunks || audioStatusQuery.data?.running}
                      loading={generateMutation.isPending}
                      onClick={() => {
                        setChapterId(record.chapter_id)
                        confirmRegenerate({ targetChapterId: record.chapter_id })
                      }}
                    >
                      重新生成
                    </Button>
                    <Button
                      size="small"
                      disabled={!record.chapter_id || !record.audio_chunks}
                      onClick={() => record.chapter_id && window.open(api.chapterAudiobookUrl(record.chapter_id), '_blank')}
                    >
                      下载
                    </Button>
                  </Space>
                ),
              },
            ]}
          />
        </Card>
        <ChunkEditorTable
          chapterId={chapterId}
          className="mt-16"
          title={chapterId ? '当前章节片段编辑' : '全书片段编辑'}
        />
      </Col>
      <Modal
        open={m4bOpen}
        title="M4B 导出"
        okText="开始导出"
        cancelText="取消"
        confirmLoading={m4bMutation.isPending}
        onCancel={() => setM4bOpen(false)}
        onOk={() => m4bForm.submit()}
      >
        <Form form={m4bForm} layout="vertical" onFinish={(values) => m4bMutation.mutate(values)}>
          <Form.Item name="title" label="标题"><Input /></Form.Item>
          <Form.Item name="author" label="作者"><Input /></Form.Item>
          <Form.Item name="narrator" label="旁白"><Input /></Form.Item>
          <Form.Item name="year" label="年份"><Input /></Form.Item>
          <Form.Item name="description" label="简介"><Input.TextArea rows={4} /></Form.Item>
          <Form.Item name="per_chunk_chapters" label="按片段生成章节" valuePropName="checked" initialValue={false}>
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Row>
  )
}
