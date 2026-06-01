import {
  Alert,
  App as AntApp,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  Progress,
  Row,
  Segmented,
  Space,
  Statistic,
  Spin,
  Tag,
  Typography,
} from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  CloudDownloadOutlined,
  CopyOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FolderOpenOutlined,
  LinkOutlined,
  ReloadOutlined,
  SettingOutlined,
  StopOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type {
  CapabilityModule,
  DesktopDiagnostics,
  ModuleRequirement,
  ModulesResponse,
  VocStudioDesktopBridge,
} from '../types'

const { Text, Title, Paragraph } = Typography

const stateMeta: Record<string, { color: string; text: string; badge: 'success' | 'processing' | 'warning' | 'error' | 'default' }> = {
  ready: { color: 'success', text: '可用', badge: 'success' },
  needs_config: { color: 'warning', text: '需配置', badge: 'warning' },
  not_installed: { color: 'default', text: '未安装', badge: 'default' },
  unavailable: { color: 'error', text: '缺依赖', badge: 'error' },
  installing: { color: 'processing', text: '安装中', badge: 'processing' },
}

const summaryItems = [
  { key: 'ready', label: '可用', color: '#16a34a' },
  { key: 'needs_config', label: '需配置', color: '#d97706' },
  { key: 'not_installed', label: '未安装', color: '#64748b' },
  { key: 'unavailable', label: '缺依赖', color: '#dc2626' },
  { key: 'installing', label: '安装中', color: '#2563eb' },
]

const stateFilterOptions = [
  { label: '全部', value: 'all' },
  ...summaryItems.map((item) => ({ label: item.label, value: item.key })),
]

function moduleIcon(category: string) {
  if (category === 'LLM') return <ApiOutlined />
  if (category === 'TTS') return <CloudDownloadOutlined />
  if (category === '本地模型') return <DatabaseOutlined />
  if (category === '训练') return <ExperimentOutlined />
  return <ToolOutlined />
}

function requirementText(requirement: ModuleRequirement) {
  if (requirement.package) return requirement.package
  if (requirement.section && requirement.key) return `${requirement.section}.${requirement.key}`
  return requirement.import_name ?? '-'
}

function stateLabel(module: CapabilityModule) {
  const meta = stateMeta[module.state] ?? stateMeta.not_installed
  return <Tag color={meta.color}>{meta.text}</Tag>
}

function groupModules(data?: ModulesResponse) {
  const groups = new Map<string, CapabilityModule[]>()
  ;(data?.modules ?? []).forEach((module) => {
    groups.set(module.category, [...(groups.get(module.category) ?? []), module])
  })
  return [...groups.entries()]
}

function filterModules(data: ModulesResponse | undefined, query: string, stateFilter: string) {
  const normalizedQuery = query.trim().toLowerCase()
  if (!data) return undefined

  return {
    ...data,
    modules: data.modules.filter((module) => {
      const matchesState = stateFilter === 'all' || module.state === stateFilter
      const searchable = [
        module.name,
        module.category,
        module.summary,
        module.model_id,
        module.manual_hint,
      ].filter(Boolean).join(' ').toLowerCase()
      return matchesState && (!normalizedQuery || searchable.includes(normalizedQuery))
    }),
  }
}

function moduleSummary(data?: ModulesResponse) {
  const counts = Object.fromEntries(summaryItems.map((item) => [item.key, 0])) as Record<string, number>
  ;(data?.modules ?? []).forEach((module) => {
    counts[module.state] = (counts[module.state] ?? 0) + 1
  })
  return {
    total: data?.modules.length ?? 0,
    counts,
  }
}

function formatDateTime(value?: string) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

function installProgress(logs: string[], running?: boolean) {
  if (!running) return { percent: 0, label: '空闲' }
  const normalizedLogs = logs.map((log) => log.toLowerCase()).join('\n')
  if (/ready|complete|completed|success|finished|安装完成|完成/.test(normalizedLogs)) {
    return { percent: 95, label: '收尾验证' }
  }
  if (/download|snapshot|resolve|fetch|拉取|下载/.test(normalizedLogs)) {
    return { percent: 60, label: '下载模型' }
  }
  if (/cache|exists|found|缓存/.test(normalizedLogs)) {
    return { percent: 45, label: '检查缓存' }
  }
  if (/start|install|安装|开始/.test(normalizedLogs)) {
    return { percent: 25, label: '准备安装' }
  }
  return { percent: 10, label: '排队启动' }
}

function moduleInstallDetails(module: CapabilityModule) {
  return [
    `能力：${module.name}`,
    module.model_id ? `模型：${module.model_id}` : '',
    module.disk_estimate_gb ? `预计空间：${module.disk_estimate_gb} GB` : '预计空间：无需大模型',
    module.manual_hint ? `提示：${module.manual_hint}` : '',
  ].filter(Boolean).join('\n')
}

function desktopBridge(): VocStudioDesktopBridge | undefined {
  if (typeof window === 'undefined') return undefined
  return (window as typeof window & { vocStudio?: VocStudioDesktopBridge }).vocStudio
}

function ModuleCard({
  module,
  activeInstallId,
  installRunning,
}: {
  module: CapabilityModule
  activeInstallId?: string | null
  installRunning?: boolean
}) {
  const { message, modal } = AntApp.useApp()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isActiveInstall = activeInstallId === module.id
  const installBlocked = !!installRunning && !isActiveInstall
  const installMutation = useMutation({
    mutationFn: () => api.installModule(module.id),
    onSuccess: async () => {
      message.success('安装任务已启动')
      await queryClient.invalidateQueries({ queryKey: ['modules'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const repairMutation = useMutation({
    mutationFn: () => api.repairModule(module.id),
    onSuccess: async () => {
      message.success('修复任务已启动')
      await queryClient.invalidateQueries({ queryKey: ['modules'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const missingPackages = module.missing_packages ?? []
  const missingConfig = module.missing_config ?? []
  const missingExecutables = module.missing_executables ?? []
  const meta = stateMeta[module.state] ?? stateMeta.not_installed
  const shouldRepair = module.model_cached || module.state === 'ready'
  const primaryMutation = shouldRepair ? repairMutation : installMutation
  const runInstallAction = () => {
    modal.confirm({
      title: shouldRepair ? `修复 ${module.name}` : `安装 ${module.name}`,
      content: (
        <Space direction="vertical" size={8}>
          <Text className="pre-line-text">{moduleInstallDetails(module)}</Text>
          <Text type="secondary">任务启动后会进入安装队列，同一时间只运行一个安装任务。</Text>
        </Space>
      ),
      okText: shouldRepair ? '开始修复' : '开始安装',
      cancelText: '取消',
      onOk: () => shouldRepair ? repairMutation.mutate() : installMutation.mutate(),
    })
  }
  const actions = [
    <Button
      key="install-or-repair"
      type="link"
      icon={<CloudDownloadOutlined />}
      disabled={!module.installable || installBlocked || isActiveInstall || primaryMutation.isPending}
      loading={primaryMutation.isPending || isActiveInstall}
      title={installBlocked ? '已有安装任务正在运行' : undefined}
      onClick={runInstallAction}
    >
      {isActiveInstall ? '安装中' : shouldRepair ? '修复' : '安装'}
    </Button>,
  ]

  if (module.state === 'needs_config') {
    actions.push(
      <Button
        key="settings"
        type="link"
        icon={<SettingOutlined />}
        onClick={() => navigate('/settings')}
      >
        配置
      </Button>,
    )
  }

  return (
    <Card
      className="module-card"
      title={
        <Space>
          {moduleIcon(module.category)}
          <span>{module.name}</span>
        </Space>
      }
      extra={<Badge status={meta.badge} text={stateLabel(module)} />}
      actions={actions}
    >
      <Space direction="vertical" size={12} className="full-width">
        <Paragraph className="module-summary">{module.summary}</Paragraph>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="类型">{module.category}</Descriptions.Item>
          <Descriptions.Item label="预计空间">
            {module.disk_estimate_gb ? `${module.disk_estimate_gb} GB` : '无需大模型'}
          </Descriptions.Item>
          <Descriptions.Item label="模型">
            {module.model_id ? <Text code>{module.model_id}</Text> : '-'}
          </Descriptions.Item>
        </Descriptions>

        {module.state === 'ready' && (
          <Alert type="success" showIcon icon={<CheckCircleOutlined />} message="此能力已可用" />
        )}
        {module.state === 'needs_config' && (
          <Alert
            type="warning"
            showIcon
            message="需要补充配置"
            description={missingConfig.map(requirementText).join('，')}
          />
        )}
        {module.state === 'unavailable' && (
          <Alert
            type="error"
            showIcon
            message="缺少运行依赖"
            description={[
              missingPackages.length ? `Python 包：${missingPackages.map(requirementText).join('，')}` : '',
              missingExecutables.length ? `系统工具：${missingExecutables.join('，')}` : '',
              module.manual_hint ?? '',
            ].filter(Boolean).join('；')}
          />
        )}
        {module.state === 'not_installed' && module.installable && (
          <Alert type="info" showIcon message="可按需安装" description="安装会下载模型快照，耗时取决于网络和模型缓存。" />
        )}
        {module.state === 'not_installed' && !module.installable && module.manual_hint && (
          <Alert type="info" showIcon message="需要手动准备" description={module.manual_hint} />
        )}
        {module.model_path && (
          <Text type="secondary" className="module-path">{module.model_path}</Text>
        )}
      </Space>
    </Card>
  )
}

function DesktopPanel({ diagnostics }: { diagnostics?: DesktopDiagnostics }) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const bridge = desktopBridge()
  const backend = diagnostics?.backend
  const runtimeCommands = diagnostics?.runtime?.commands ?? []
  const backendMode = backend?.mode === 'external'
    ? '挂接已有服务'
    : backend?.managed === false
      ? '外部服务'
      : '客户端托管'
  const backendStatusText = backend?.ready
    ? backend?.mode === 'external' ? '已挂接' : '运行中'
    : '未就绪'

  if (!bridge) return null

  const runDesktopAction = async (action: () => Promise<unknown>, success: string) => {
    try {
      await action()
      message.success(success)
      await queryClient.invalidateQueries({ queryKey: ['desktop-diagnostics'] })
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <Card
      title={
        <Space>
          <ToolOutlined />
          <span>桌面运行时</span>
        </Space>
      }
      extra={<Badge status={backend?.ready ? 'success' : 'warning'} text={backendStatusText} />}
    >
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="应用">{diagnostics?.appName ?? 'Voc Studio'} {diagnostics?.appVersion ?? ''}</Descriptions.Item>
            <Descriptions.Item label="Electron">{diagnostics?.electronVersion ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="平台">{[diagnostics?.platform, diagnostics?.arch].filter(Boolean).join(' / ') || '-'}</Descriptions.Item>
            <Descriptions.Item label="打包模式">{diagnostics?.packaged ? '已打包' : '开发模式'}</Descriptions.Item>
            <Descriptions.Item label="后端模式">{backendMode}</Descriptions.Item>
            <Descriptions.Item label="后端 PID">{backend?.pid ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="后端端口">{backend?.port ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="后端地址">{backend?.url ? <Text code>{backend.url}</Text> : '-'}</Descriptions.Item>
            <Descriptions.Item label="工作目录">{backend?.cwd ? <Text className="module-path">{backend.cwd}</Text> : '-'}</Descriptions.Item>
            <Descriptions.Item label="数据目录">{backend?.dataDir ? <Text className="module-path">{backend.dataDir}</Text> : '-'}</Descriptions.Item>
            <Descriptions.Item label="缓存目录">{backend?.cacheDir ? <Text className="module-path">{backend.cacheDir}</Text> : '-'}</Descriptions.Item>
            <Descriptions.Item label="日志目录">{backend?.logDir ? <Text className="module-path">{backend.logDir}</Text> : '-'}</Descriptions.Item>
          </Descriptions>
          {runtimeCommands.length > 0 && (
            <Space direction="vertical" size={8} className="runtime-checks">
              <Text type="secondary">运行环境</Text>
              {runtimeCommands.map((command) => (
                <Space key={command.name} wrap>
                  <Tag color={command.available ? 'success' : command.required ? 'error' : 'warning'}>
                    {command.name}
                  </Tag>
                  <Text type="secondary">{command.version || command.error || '未检测到'}</Text>
                </Space>
              ))}
            </Space>
          )}
        </Col>
        <Col xs={24} xl={10}>
          <Space direction="vertical" size={10} className="full-width">
            <Button icon={<FolderOpenOutlined />} block onClick={() => runDesktopAction(bridge.openDataDirectory, '已打开数据目录')}>
              打开数据目录
            </Button>
            <Button icon={<FolderOpenOutlined />} block onClick={() => runDesktopAction(bridge.openLogDirectory, '已打开日志目录')}>
              打开日志目录
            </Button>
            <Button icon={<FolderOpenOutlined />} block onClick={() => runDesktopAction(bridge.openCacheDirectory, '已打开缓存目录')}>
              打开缓存目录
            </Button>
            <Button icon={<LinkOutlined />} block disabled={!backend?.url} onClick={() => runDesktopAction(bridge.openBackendUrl, '已打开本地服务')}>
              打开本地服务
            </Button>
            <Button icon={<CopyOutlined />} block onClick={() => runDesktopAction(bridge.copyBackendLaunchCommand, '后端启动命令已复制')}>
              {backend?.mode === 'external' ? '复制挂接命令' : '复制启动命令'}
            </Button>
            <Button icon={<CopyOutlined />} block onClick={() => runDesktopAction(bridge.copyDiagnostics, '诊断报告已复制')}>
              复制诊断报告
            </Button>
            {backend?.command && <Text code className="desktop-command">{backend.command}</Text>}
            {backend?.error && <Alert type="warning" showIcon message="后端状态" description={backend.error} />}
          </Space>
        </Col>
      </Row>
    </Card>
  )
}

export function ModulesPage() {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [moduleQuery, setModuleQuery] = useState('')
  const [stateFilter, setStateFilter] = useState('all')
  const modulesQuery = useQuery({
    queryKey: ['modules'],
    queryFn: api.modules,
    refetchInterval: (query) => query.state.data?.install_task?.running ? 1500 : false,
  })
  const desktopDiagnosticsQuery = useQuery({
    queryKey: ['desktop-diagnostics'],
    queryFn: () => desktopBridge()?.diagnostics() ?? Promise.resolve(undefined),
    refetchInterval: (query) => query.state.data?.backend?.ready ? 5000 : false,
  })
  const cancelInstallMutation = useMutation({
    mutationFn: api.cancelModuleInstall,
    onSuccess: async (result) => {
      message.info(result.status === 'idle' ? '当前没有安装任务' : '已请求取消安装')
      await queryClient.invalidateQueries({ queryKey: ['modules'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const summary = moduleSummary(modulesQuery.data)
  const filteredModules = useMemo(
    () => filterModules(modulesQuery.data, moduleQuery, stateFilter),
    [modulesQuery.data, moduleQuery, stateFilter],
  )
  const groups = groupModules(filteredModules)
  const installTask = modulesQuery.data?.install_task
  const logs = installTask?.logs ?? []
  const installRunning = !!installTask?.running
  const currentInstallProgress = installProgress(logs, installRunning)

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <Card
        title={
          <Space direction="vertical" size={0}>
            <Title level={4} className="section-title">能力中心</Title>
            <Text type="secondary">Voc Studio 按需检测和安装模型、驱动与音频工具。</Text>
          </Space>
        }
        extra={<Button icon={<ReloadOutlined />} onClick={() => modulesQuery.refetch()}>刷新</Button>}
      >
        <Spin spinning={modulesQuery.isLoading}>
          <Row gutter={[12, 12]} className="module-summary-grid">
            <Col xs={12} md={4}>
              <Card size="small" className="module-summary-tile">
                <Statistic title="能力总数" value={summary.total} />
              </Card>
            </Col>
            {summaryItems.map((item) => (
              <Col xs={12} md={4} key={item.key}>
                <Card size="small" className="module-summary-tile">
                  <Statistic title={item.label} value={summary.counts[item.key] ?? 0} valueStyle={{ color: item.color }} />
                </Card>
              </Col>
            ))}
          </Row>

          <Space className="module-filter-bar" wrap>
            <Input.Search
              allowClear
              className="module-search"
              placeholder="搜索能力、模型或说明"
              value={moduleQuery}
              onChange={(event) => setModuleQuery(event.target.value)}
            />
            <Segmented
              options={stateFilterOptions}
              value={stateFilter}
              onChange={(value) => setStateFilter(String(value))}
            />
          </Space>

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={8}>
              <Descriptions column={1} bordered size="small">
                <Descriptions.Item label="应用">{modulesQuery.data?.app_name ?? 'Voc Studio'}</Descriptions.Item>
                <Descriptions.Item label="模式">{modulesQuery.data?.desktop?.desktop ? '桌面客户端' : 'Web / 开发模式'}</Descriptions.Item>
                <Descriptions.Item label="数据目录">{modulesQuery.data?.desktop?.data_dir ?? '-'}</Descriptions.Item>
                <Descriptions.Item label="缓存目录">{modulesQuery.data?.desktop?.cache_dir ?? '-'}</Descriptions.Item>
                <Descriptions.Item label="Python">{modulesQuery.data?.desktop?.python ?? '-'}</Descriptions.Item>
              </Descriptions>
            </Col>
            <Col xs={24} lg={16}>
              {installRunning ? (
                <Alert
                  type="info"
                  showIcon
                  message={`正在安装：${installTask.module_id}`}
                  description={
                    <Space direction="vertical" size={10} className="full-width">
                      <Progress percent={currentInstallProgress.percent} status="active" />
                      <Text type="secondary">阶段：{currentInstallProgress.label}</Text>
                      <Text type="secondary">
                        开始：{formatDateTime(installTask.started_at)}
                      </Text>
                      <Button
                        danger
                        size="small"
                        icon={<StopOutlined />}
                        loading={cancelInstallMutation.isPending}
                        onClick={() => cancelInstallMutation.mutate()}
                      >
                        取消安装
                      </Button>
                    </Space>
                  }
                />
              ) : installTask?.error ? (
                <Alert
                  type="error"
                  showIcon
                  message="最近一次安装失败"
                  description={
                    <Space direction="vertical" size={4}>
                      <Text>{installTask.error}</Text>
                      <Text type="secondary">结束：{formatDateTime(installTask.finished_at)}</Text>
                    </Space>
                  }
                />
              ) : (
                <Alert
                  type="success"
                  showIcon
                  message="能力检测完成"
                  description="本地模型不会自动下载，只有点击安装或首次使用对应功能时才会拉取。"
                />
              )}
              {logs.length > 0 && <pre className="log-window compact-log mt-16">{logs.slice(-30).join('\n')}</pre>}
            </Col>
          </Row>
        </Spin>
      </Card>

      <DesktopPanel diagnostics={desktopDiagnosticsQuery.data} />

      {groups.length ? groups.map(([category, modules]) => (
        <Card key={category} title={category}>
          <Row gutter={[16, 16]}>
            {modules.map((module) => (
              <Col xs={24} md={12} xl={8} key={module.id}>
                <ModuleCard module={module} activeInstallId={installTask?.module_id} installRunning={installRunning} />
              </Col>
            ))}
          </Row>
        </Card>
      )) : (
        <Card><Empty description={modulesQuery.data?.modules.length ? '没有匹配的能力模块' : '暂无能力模块'} /></Card>
      )}
    </Space>
  )
}
