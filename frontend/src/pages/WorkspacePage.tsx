import { Alert, App as AntApp, Button, Card, Flex, Space, Spin, Steps, Tag, Typography } from 'antd'
import {
  AudioOutlined,
  FileTextOutlined,
  HomeOutlined,
  SoundOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { lazy, Suspense, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useBooks } from '../hooks'
import { formatCount, normalizeScriptProgress } from '../utils'
import { api } from '../api'

const { Title } = Typography

const ResultStep = lazy(() => import('../steps/ResultStep').then((module) => ({ default: module.ResultStep })))
const ScriptStep = lazy(() => import('../steps/ScriptStep').then((module) => ({ default: module.ScriptStep })))
const UploadStep = lazy(() => import('../steps/UploadStep').then((module) => ({ default: module.UploadStep })))
const VoicesStep = lazy(() => import('../steps/VoicesStep').then((module) => ({ default: module.VoicesStep })))

const workspaceSteps = [
  { key: 'upload', title: '上传', path: 'upload', icon: <UploadOutlined /> },
  { key: 'script', title: '脚本', path: 'script', icon: <FileTextOutlined /> },
  { key: 'voices', title: '声音', path: 'voices', icon: <SoundOutlined /> },
  { key: 'result', title: '生成', path: 'result', icon: <AudioOutlined /> },
]

function StepLoading() {
  return (
    <Card>
      <Spin /> 正在加载步骤...
    </Card>
  )
}

export function WorkspacePage() {
  const { message } = AntApp.useApp()
  const { bookId = '', step = 'upload' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const decodedBookId = decodeURIComponent(bookId)
  const booksQuery = useBooks()
  const currentBook = booksQuery.data?.books.find((book) => book.id === decodedBookId)
  const currentBookId = booksQuery.data?.current_book_id
  const activeStep = workspaceSteps.findIndex((item) => item.key === step)
  const switchingBook = !!booksQuery.data && currentBookId !== decodedBookId
  const prerequisitesEnabled = !!currentBook && !switchingBook
  const scriptProgressQuery = useQuery({
    queryKey: ['script-progress'],
    queryFn: api.scriptProgress,
    enabled: prerequisitesEnabled,
  })
  const chunksQuery = useQuery({
    queryKey: ['chunks'],
    queryFn: api.chunks,
    enabled: prerequisitesEnabled,
  })
  const selectBookMutation = useMutation({
    mutationFn: api.selectBook,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['books'] })
    },
  })

  useEffect(() => {
    if (!decodedBookId || !booksQuery.data || currentBookId === decodedBookId || selectBookMutation.isPending) return
    selectBookMutation.mutate(decodedBookId)
  }, [booksQuery.data, currentBookId, decodedBookId, selectBookMutation])

  if (step === 'editor') {
    return <Navigate to={`/books/${encodeURIComponent(decodedBookId)}/result`} replace />
  }

  if (!workspaceSteps.some((item) => item.key === step)) {
    return <Navigate to={`/books/${encodeURIComponent(decodedBookId)}/upload`} replace />
  }

  const hasChapters = !!currentBook?.source_filename && Number(currentBook?.chapter_count ?? 0) > 0
  const progressItems = normalizeScriptProgress(scriptProgressQuery.data)
  const hasScript = progressItems.some((item) => (
    Boolean(item.generated)
    || ['done', 'complete', 'completed'].includes(String(item.status ?? '').toLowerCase())
    || Number(item.entry_count ?? 0) > 0
  ))
  const hasChunks = (chunksQuery.data ?? []).length > 0
  const stepAvailability: Record<string, { available: boolean; reason?: string; target: string; action: string }> = {
    upload: { available: true, target: 'upload', action: '上传源稿' },
    script: {
      available: hasChapters,
      reason: '请先上传源稿并生成章节清单，再进入脚本生成。',
      target: 'upload',
      action: '去上传源稿',
    },
    voices: {
      available: hasChapters && hasScript,
      reason: hasChapters ? '请先生成标注脚本，再配置角色声音。' : '请先上传源稿并生成章节清单。',
      target: hasChapters ? 'script' : 'upload',
      action: hasChapters ? '去生成脚本' : '去上传源稿',
    },
    result: {
      available: hasChapters && hasScript && hasChunks,
      reason: !hasChapters
        ? '请先上传源稿并生成章节清单。'
        : !hasScript
          ? '请先生成标注脚本，再生成音频。'
          : '请先让脚本产出音频片段，再进入生成与导出。',
      target: !hasChapters ? 'upload' : !hasScript ? 'script' : 'voices',
      action: !hasChapters ? '去上传源稿' : !hasScript ? '去生成脚本' : '去配置声音',
    },
  }
  const currentAvailability = stepAvailability[step]
  const prerequisitesLoading = prerequisitesEnabled && (
    ((step === 'voices' || step === 'result') && scriptProgressQuery.isLoading)
    || (step === 'result' && chunksQuery.isLoading)
  )
  const navigateToStep = (nextStep: string) => {
    navigate(`/books/${encodeURIComponent(decodedBookId)}/${nextStep}`)
  }

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <Card>
        <Flex justify="space-between" align="center" gap={16} wrap>
          <Space direction="vertical" size={4}>
            <Button type="link" icon={<HomeOutlined />} className="inline-link" onClick={() => navigate('/library')}>
              返回书库
            </Button>
            <Title level={3} className="section-title">{currentBook?.title ?? decodedBookId}</Title>
          </Space>
          <Space wrap>
            <Tag color="blue">{currentBook?.source_filename ?? '未上传源稿'}</Tag>
            <Tag>{formatCount(currentBook?.chapter_count)} 章</Tag>
            <Tag>{formatCount(currentBook?.char_count)} 字</Tag>
          </Space>
        </Flex>
      </Card>

      <Card className="step-card">
        <Steps
          current={Math.max(activeStep, 0)}
          items={workspaceSteps.map((item) => ({
            title: item.title,
            icon: item.icon,
            disabled: !stepAvailability[item.key].available,
            description: stepAvailability[item.key].available ? undefined : '未就绪',
          }))}
          onChange={(index) => {
            const next = workspaceSteps[index]
            const availability = stepAvailability[next.key]
            if (!availability.available) {
              message.warning(availability.reason)
              return
            }
            navigateToStep(next.path)
          }}
        />
      </Card>

      {switchingBook || prerequisitesLoading ? (
        <Card>
          <Spin /> {switchingBook ? '正在切换书籍...' : '正在检查当前步骤状态...'}
        </Card>
      ) : currentAvailability && !currentAvailability.available ? (
        <Card>
          <Alert
            type="warning"
            showIcon
            message="当前步骤还未就绪"
            description={currentAvailability.reason}
            action={(
              <Button type="primary" onClick={() => navigateToStep(currentAvailability.target)}>
                {currentAvailability.action}
              </Button>
            )}
          />
        </Card>
      ) : (
        <Suspense fallback={<StepLoading />}>
          {step === 'upload' && <UploadStep />}
          {step === 'script' && <ScriptStep />}
          {step === 'voices' && <VoicesStep />}
          {step === 'result' && <ResultStep />}
        </Suspense>
      )}
    </Space>
  )
}
