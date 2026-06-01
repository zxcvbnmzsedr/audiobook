import { Badge, Card, Empty, Flex, List, Progress, Space, Statistic, Tag, Timeline, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { formatCount } from '../utils'
import type { ScriptStreamState } from '../scriptStream'
import type { TaskStatus } from '../types'

const { Text } = Typography

type ResourceListProps<T> = {
  loading: boolean
  data: T[]
  empty: string
  getTitle: (item: T) => string
  getDescription: (item: T) => string
  getActions?: (item: T) => React.ReactNode[]
}

function ResourceListItem<T>({ item, getTitle, getDescription, getActions }: {
  item: T
  getTitle: (item: T) => string
  getDescription: (item: T) => string
  getActions?: (item: T) => React.ReactNode[]
}) {
  return (
    <List.Item actions={getActions?.(item)}>
      <List.Item.Meta title={getTitle(item)} description={getDescription(item)} />
    </List.Item>
  )
}

export function ResourceList<T>({ loading, data, empty, getTitle, getDescription, getActions }: ResourceListProps<T>) {
  return (
    <List
      loading={loading}
      dataSource={data}
      locale={{ emptyText: empty }}
      renderItem={(item) => (
        <ResourceListItem
          item={item}
          getTitle={getTitle}
          getDescription={getDescription}
          getActions={getActions}
        />
      )}
    />
  )
}

export function TaskStatusCard({ status }: { status?: TaskStatus }) {
  const logs = status?.logs ?? []
  return (
    <Card size="small" className="status-card">
      <Space direction="vertical" size={8} className="full-width">
        <Badge status={status?.running ? 'processing' : 'default'} text={status?.running ? '运行中' : '空闲'} />
        <div className="log-window">
          {logs.length ? logs.slice(-12).map((line, index) => <div key={`${index}-${line}`}>{line}</div>) : <Text type="secondary">暂无日志</Text>}
        </div>
      </Space>
    </Card>
  )
}

export function ScriptStreamPanel({ stream, status }: { stream: ScriptStreamState; status?: TaskStatus }) {
  const fallbackLogs = status?.logs ?? []
  const logs = stream.logs.length ? stream.logs : fallbackLogs.slice(-24)
  const progress = stream.total ? Math.min(100, Math.round((stream.current / stream.total) * 100)) : 0
  const badgeStatus = stream.status === 'success'
    ? 'success'
    : stream.status === 'error'
      ? 'error'
      : stream.status === 'warning'
        ? 'warning'
        : status?.running || stream.connected
          ? 'processing'
          : 'default'

  return (
    <Card size="small" className="status-card">
      <Space direction="vertical" size={10} className="full-width">
        <Flex justify="space-between" align="center" gap={12}>
          <Badge status={badgeStatus} text={status?.running || stream.connected ? '运行中' : stream.status === 'success' ? '完成' : '空闲'} />
          <Tag color={stream.connected ? 'processing' : 'default'}>{stream.connected ? 'SSE 已连接' : '轮询'}</Tag>
        </Flex>
        {stream.stage ? <Text>{stream.stage}</Text> : null}
        {stream.total ? <Progress percent={progress} size="small" status={stream.status === 'error' ? 'exception' : stream.status === 'success' ? 'success' : 'active'} /> : null}
        {stream.entries.length ? (
          <Card size="small" title={`实时样例 ${formatCount(stream.entries.length)}`}>
            <div className="script-entry-stream">
              {stream.entries.slice(-8).map((entry, index) => (
                <div key={`${index}-${entry.speaker}-${entry.text}`} className="script-entry-item">
                  <Tag>{entry.speaker || '?'}</Tag>
                  <span>{entry.text || ''}</span>
                  {entry.instruct ? <Text type="secondary">{entry.instruct}</Text> : null}
                </div>
              ))}
            </div>
          </Card>
        ) : null}
        <div className="log-window">
          {logs.length ? logs.slice(-18).map((line, index) => <div key={`${index}-${line}`}>{line}</div>) : <Text type="secondary">暂无日志</Text>}
        </div>
      </Space>
    </Card>
  )
}

function formatElapsed(ms?: number) {
  if (!ms || ms < 0) return '-'
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`
}

function formatMetricElapsed(ms?: number) {
  if (!ms || ms <= 0) return '0s'
  if (ms < 1000) return `${ms}ms`
  return formatElapsed(ms)
}

function stageStatus(stage: ScriptStreamState['activeStage'] | undefined, target: ScriptStreamState['activeStage'], running?: boolean) {
  if (stage === 'done') return 'finish'
  if (stage === target && running) return 'process'
  if (!stage || stage === 'split') return target === 'split' ? 'process' : 'wait'
  const order = ['split', 'characters', 'script', 'memory', 'review', 'done']
  return order.indexOf(stage) > order.indexOf(target) ? 'finish' : 'wait'
}

function stageColor(status: string) {
  if (status === 'process') return 'blue'
  if (status === 'finish') return 'green'
  return 'gray'
}

function stageName(stage?: ScriptStreamState['activeStage']) {
  if (stage === 'characters') return '人物池'
  if (stage === 'script') return '脚本'
  if (stage === 'memory') return '章节记忆'
  if (stage === 'review') return '审校'
  if (stage === 'split') return '章节拆分'
  return 'LLM'
}

function useLiveNow() {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const intervalId = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(intervalId)
  }, [])

  return now
}

export function ScriptDiagnosticsPanel({
  stream,
  status,
  reviewStatus,
  reviewStream,
}: {
  stream: ScriptStreamState
  status?: TaskStatus
  reviewStatus?: TaskStatus
  reviewStream?: ScriptStreamState
}) {
  const reviewRunning = Boolean(reviewStatus?.running || reviewStream?.connected || reviewStream?.status === 'running')
  const reviewIsNewest = Boolean(reviewStream?.lastEventAt && (!stream.lastEventAt || reviewStream.lastEventAt >= stream.lastEventAt))
  const showReviewStream = Boolean(reviewRunning || (!status?.running && !stream.connected && reviewIsNewest))
  const displayStream = showReviewStream ? (reviewStream ?? stream) : stream
  const running = Boolean(status?.running || stream.connected || reviewRunning)
  const activeStage = showReviewStream && displayStream.activeStage !== 'done' ? 'review' : displayStream.activeStage
  const logs = showReviewStream
    ? reviewStream?.logs.length ? reviewStream.logs : reviewStatus?.logs ?? []
    : displayStream.logs.length ? displayStream.logs : (status?.logs ?? []).slice(-24)
  const lastLog = logs.at(-1)
  const now = useLiveNow()
  const stageElapsed = displayStream.stageStartedAt ? formatElapsed(now - displayStream.stageStartedAt) : '-'
  const llmIdle = displayStream.lastLlmAt ? formatElapsed(now - displayStream.lastLlmAt) : '-'
  const currentLlmWaitMs = displayStream.currentLlmStartedAt ? now - displayStream.currentLlmStartedAt : 0
  const currentLlmIdleMs = displayStream.currentLlmStartedAt && displayStream.lastLlmAt
    ? now - Math.max(displayStream.currentLlmStartedAt, displayStream.lastLlmAt)
    : currentLlmWaitMs
  const currentLlmHasOutput = Boolean(
    displayStream.currentLlmStartedAt
    && displayStream.lastLlmAt
    && displayStream.lastLlmAt > displayStream.currentLlmStartedAt,
  )
  const showSlowLlmNotice = Boolean(currentLlmWaitMs >= 30_000 && running)
  const slowLlmLevel = currentLlmIdleMs >= 90_000 ? 'stalled' : currentLlmHasOutput ? 'streaming' : 'waiting'
  const chapterLabel = displayStream.currentChapterTitle || displayStream.currentChapterId || '-'
  const metricEntries = Object.entries(displayStream.stageMetrics)
  const slowestStage = metricEntries.reduce<{ key: string; elapsedMs: number } | null>((slowest, [key, metrics]) => {
    const elapsedMs = metrics?.elapsedMs ?? 0
    if (!elapsedMs) return slowest
    if (!slowest || elapsedMs > slowest.elapsedMs) return { key, elapsedMs }
    return slowest
  }, null)
  const stages: Array<{ key: ScriptStreamState['activeStage']; label: string; description: string }> = [
    { key: 'split', label: '章节拆分', description: '规则拆分，0 token' },
    { key: 'characters', label: '人物池', description: 'LLM 逐章分析' },
    { key: 'script', label: '脚本', description: 'LLM tagged 输出' },
    { key: 'memory', label: '章节记忆', description: displayStream.enableChapterMemory ? 'LLM 连续性记录' : '未启用时跳过' },
    { key: 'review', label: '审校', description: '手动触发，分批 LLM' },
  ]

  return (
    <Card size="small" title="LLM 生成诊断" className="status-card">
      <Space direction="vertical" size={12} className="full-width">
        <div className="diagnostic-flow">
          {stages.map((item) => {
            const statusValue = stageStatus(activeStage, item.key, running)
            const metrics = item.key && displayStream.stageMetrics[item.key as keyof typeof displayStream.stageMetrics]
            const isSlowest = Boolean(metrics?.elapsedMs && slowestStage?.key === item.key)
            return (
              <div key={item.key} className={`diagnostic-node ${statusValue} ${isSlowest ? 'slowest' : ''}`}>
                <Flex justify="space-between" align="center" gap={6}>
                  <Tag color={stageColor(statusValue)}>{item.label}</Tag>
                  {isSlowest ? <Tag color="volcano">最慢</Tag> : null}
                </Flex>
                <Text type="secondary">{item.description}</Text>
                {metrics ? (
                  <Text type="secondary" className="diagnostic-node-metric">
                    {metrics.calls} 次 / {formatMetricElapsed(metrics.elapsedMs)}
                  </Text>
                ) : null}
              </div>
            )
          })}
        </div>
        <Card size="small">
          <Space direction="vertical" size={8} className="full-width">
            <Flex justify="space-between" gap={12} wrap>
              <Badge status={running ? 'processing' : displayStream.status === 'error' ? 'error' : displayStream.status === 'warning' ? 'warning' : 'default'} text={running ? '运行中' : '空闲'} />
              <Tag>{showReviewStream ? reviewStream?.connected ? '审校 SSE' : '审校轮询' : stream.connected ? 'SSE' : '轮询'}</Tag>
            </Flex>
            <Text strong>{displayStream.stage || (showReviewStream ? '脚本审校：等待 LLM 分批修正' : '暂无正在运行的脚本任务')}</Text>
            {showSlowLlmNotice ? (
              <div className={`diagnostic-waiting ${slowLlmLevel}`}>
                <Flex justify="space-between" align="center" gap={8} wrap>
                  <Text strong>{slowLlmLevel === 'stalled' ? '疑似卡住' : slowLlmLevel === 'streaming' ? 'LLM 输出中' : '等待 LLM'}</Text>
                  <Tag color={slowLlmLevel === 'stalled' ? 'red' : slowLlmLevel === 'streaming' ? 'blue' : 'gold'}>{formatElapsed(currentLlmWaitMs)}</Tag>
                </Flex>
                <Text type="secondary">
                  {stageName(displayStream.currentLlmStage)} {slowLlmLevel === 'streaming' ? '正在流式输出' : '正在等待响应'}：{displayStream.currentLabel || '-'}
                </Text>
              </div>
            ) : null}
            <div className="diagnostic-grid">
              <Statistic title={showReviewStream ? '批次' : '章节'} value={displayStream.total ? `${displayStream.current}/${displayStream.total}` : '-'} />
              <Statistic title={showReviewStream ? '当前批次' : '当前章节'} value={chapterLabel} valueStyle={{ fontSize: 14 }} />
              <Statistic title="本阶段耗时" value={stageElapsed} />
              <Statistic title="距上次 LLM 输出" value={llmIdle} />
              <Statistic title="Attempt" value={displayStream.currentAttempt ? `${displayStream.currentAttempt}/3` : '-'} />
              <Statistic title={showReviewStream ? '失败批次' : '重试'} value={showReviewStream ? displayStream.failedBatches ?? 0 : displayStream.retryCount} />
            </div>
          </Space>
        </Card>
        <div className="diagnostic-grid compact">
          <Statistic title="Input tokens" value={displayStream.inputTokens} formatter={(value) => formatCount(Number(value))} />
          <Statistic title="Output tokens" value={displayStream.outputTokens} formatter={(value) => formatCount(Number(value))} />
          <Statistic title="Total tokens" value={displayStream.totalTokens} formatter={(value) => formatCount(Number(value))} />
          <Statistic title="Cache read" value={displayStream.cacheReadTokens} formatter={(value) => formatCount(Number(value))} />
          <Statistic title="LLM 总耗时" value={formatMetricElapsed(displayStream.llmElapsedMs)} />
          <Statistic title="LLM 调用" value={displayStream.llmCallCount} />
          <Statistic title="平均耗时" value={displayStream.llmCallCount ? formatMetricElapsed(Math.round(displayStream.llmElapsedMs / displayStream.llmCallCount)) : '-'} />
          <Statistic title="最慢阶段" value={slowestStage ? `${stages.find((item) => item.key === slowestStage.key)?.label ?? slowestStage.key} ${formatMetricElapsed(slowestStage.elapsedMs)}` : '-'} valueStyle={{ fontSize: 14 }} />
        </div>
        <Timeline
          className="diagnostic-timeline"
          items={logs.slice(-6).reverse().map((line) => ({
            color: line.includes('retry') || line.includes('failed') || line.includes('error') ? 'red' : 'blue',
            children: <Text className="diagnostic-log-line">{line}</Text>,
          }))}
        />
        {!lastLog ? <Text type="secondary">任务开始后会显示当前 LLM 卡点、token 和重试信息。</Text> : null}
      </Space>
    </Card>
  )
}

export function EmptyState({ description }: { description: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
}
