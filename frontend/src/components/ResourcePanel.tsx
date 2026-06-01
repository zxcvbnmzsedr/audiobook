import {
  App as AntApp,
  Badge,
  Button,
  Card,
  Col,
  Divider,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
} from 'antd'
import type { FormInstance, UploadProps } from 'antd'
import { DeleteOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, UploadOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useReducer, useState } from 'react'
import { api } from '../api'
import { firstDoneIndex, parseSeed } from '../domain'
import type { CloneVoice, DatasetSample, DesignedVoice, LoraDataset, LoraModel, TaskStatus } from '../types'
import { formatCount, statusColor, summarizeStatus } from '../utils'
import { ResourceList, TaskStatusCard } from './common'

const { Text } = Typography
const LORA_PREVIEW_CAPTION = 'The ancient library stood at the crossroads of two forgotten paths, its weathered stone walls covered in ivy that had been growing for centuries.'

type AudioPreviewProps = {
  src: string
  label: string
  captionText: string
  captionLanguage?: string
}

type AudioState = {
  url: string
  captionText: string
}

type LoraTrainFormValues = {
  name: string
  dataset_id: string
  epochs: number
  lr: number
  batch_size: number
  lora_r: number
  lora_alpha: number
  gradient_accumulation_steps: number
  language: string
}

type LoraGenerateDatasetFormValues = {
  name: string
  description: string
  language: string
  samples: Array<{ emotion: string; text: string }>
}

type DatasetEditorState = {
  selectedName: string
  createName: string
  description: string
  globalSeed: string
  rows: DatasetSample[]
}

type DatasetEditorAction =
  | { type: 'setCreateName'; value: string }
  | { type: 'selectProject'; value: string }
  | { type: 'projectCreated'; value: string }
  | { type: 'projectDeleted' }
  | { type: 'setDescription'; value: string }
  | { type: 'setGlobalSeed'; value: string }
  | { type: 'loadStatus'; description: string; globalSeed: string; rows: DatasetSample[] }
  | { type: 'addRow' }
  | { type: 'updateRow'; index: number; patch: Partial<DatasetSample> }
  | { type: 'deleteRow'; index: number }

const INITIAL_DATASET_EDITOR_STATE: DatasetEditorState = {
  selectedName: '',
  createName: '',
  description: '',
  globalSeed: '',
  rows: [],
}

function datasetEditorReducer(state: DatasetEditorState, action: DatasetEditorAction): DatasetEditorState {
  switch (action.type) {
    case 'setCreateName':
      return { ...state, createName: action.value }
    case 'selectProject':
      return { ...state, selectedName: action.value, description: '', globalSeed: '', rows: [] }
    case 'projectCreated':
      return { ...state, createName: '', selectedName: action.value }
    case 'projectDeleted':
      return { ...state, selectedName: '' }
    case 'setDescription':
      return { ...state, description: action.value }
    case 'setGlobalSeed':
      return { ...state, globalSeed: action.value }
    case 'loadStatus':
      return {
        ...state,
        description: action.description,
        globalSeed: action.globalSeed,
        rows: action.rows,
      }
    case 'addRow':
      return { ...state, rows: [...state.rows, { emotion: '', text: '', seed: '', status: 'pending' }] }
    case 'updateRow':
      return {
        ...state,
        rows: state.rows.map((row, rowIndex) => rowIndex === action.index ? { ...row, ...action.patch } : row),
      }
    case 'deleteRow':
      return { ...state, rows: state.rows.filter((_, rowIndex) => rowIndex !== action.index) }
    default:
      return state
  }
}

function toVttText(text: string) {
  const normalized = text.trim() || '音频文本未提供。'
  return normalized
    .replace(/\r\n?/g, '\n')
    .replace(/\u2028|\u2029/g, '\n')
    .replace(/-->/g, '->')
}

function captionDataUrl(text: string) {
  const vtt = `WEBVTT\n\n00:00:00.000 --> 99:59:59.000\n${toVttText(text)}\n`
  return `data:text/vtt;charset=utf-8,${encodeURIComponent(vtt)}`
}

function AudioPreview({ src, label, captionText, captionLanguage = 'zh' }: AudioPreviewProps) {
  return (
    <audio className="audio-player" controls src={src} aria-label={label}>
      <track kind="captions" src={captionDataUrl(captionText)} srcLang={captionLanguage} label="文本" default />
    </audio>
  )
}

export function ResourcePanel() {
  return (
    <Space direction="vertical" size={16} className="full-width">
      <VoiceDesignerPanel />
      <CloneVoicePanel />
      <LoraPanel />
      <DatasetBuilder />
    </Space>
  )
}

function VoiceDesignerPanel() {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const designedVoicesQuery = useQuery({ queryKey: ['designed-voices'], queryFn: api.designedVoices })
  const [form] = Form.useForm<{ name: string; description: string; sample_text: string; language?: string }>()
  const [previewAudio, setPreviewAudio] = useState({ url: '', captionText: '' })

  const previewMutation = useMutation({
    mutationFn: (values: { description: string; sample_text: string; language?: string }) => api.voiceDesignPreview(values),
    onSuccess: async (result, values) => {
      setPreviewAudio({ url: result.audio_url, captionText: values.sample_text })
      message.success('声音设计试听已生成')
      await queryClient.invalidateQueries({ queryKey: ['designed-voices'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const saveMutation = useMutation({
    mutationFn: (values: { name: string; description: string; sample_text: string }) => {
      const previewFile = previewAudio.url.split('/').pop()?.split('?')[0] ?? ''
      return api.saveDesignedVoice({
        name: values.name,
        description: values.description,
        sample_text: values.sample_text,
        preview_file: previewFile,
      })
    },
    onSuccess: async () => {
      message.success('设计声音已保存')
      setPreviewAudio({ url: '', captionText: '' })
      form.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['designed-voices'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteDesignedVoice,
    onSuccess: async () => {
      message.success('设计声音已删除')
      await queryClient.invalidateQueries({ queryKey: ['designed-voices'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  return (
    <Card title="声音设计">
      <Space direction="vertical" size={12} className="full-width">
        <Form form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="声音描述" rules={[{ required: true, message: '请输入声音描述' }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="sample_text" label="试听文本" rules={[{ required: true, message: '请输入试听文本' }]}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Space wrap>
            <Button loading={previewMutation.isPending} icon={<PlayCircleOutlined />} onClick={() => previewMutation.mutate(form.getFieldsValue())}>
              生成试听
            </Button>
            <Button type="primary" loading={saveMutation.isPending} disabled={!previewAudio.url} icon={<SaveOutlined />} onClick={() => form.submit()}>
              保存声音
            </Button>
          </Space>
        </Form>
        {previewAudio.url ? <AudioPreview src={previewAudio.url} label="声音设计试听" captionText={previewAudio.captionText} /> : null}
        <ResourceList<DesignedVoice>
          loading={designedVoicesQuery.isLoading}
          data={designedVoicesQuery.data ?? []}
          empty="暂无已保存声音"
          getTitle={(item) => item.name}
          getDescription={(item) => item.description ?? item.filename ?? ''}
          getActions={(item) => [
            <Button
              key="delete"
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deleteMutation.isPending}
              onClick={() => {
                modal.confirm({
                  title: `删除设计声音「${item.name}」？`,
                  okText: '删除',
                  okButtonProps: { danger: true },
                  cancelText: '取消',
                  onOk: () => deleteMutation.mutate(item.id),
                })
              }}
            />,
          ]}
        />
      </Space>
    </Card>
  )
}

function CloneVoicePanel() {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const cloneVoicesQuery = useQuery({ queryKey: ['clone-voices'], queryFn: api.cloneVoices })

  const uploadMutation = useMutation({
    mutationFn: api.uploadCloneVoice,
    onSuccess: async () => {
      message.success('克隆参考音频已上传')
      await queryClient.invalidateQueries({ queryKey: ['clone-voices'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteCloneVoice,
    onSuccess: async () => {
      message.success('克隆音频已删除')
      await queryClient.invalidateQueries({ queryKey: ['clone-voices'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const uploadProps: UploadProps = {
    accept: '.wav,.mp3,.flac,.ogg',
    showUploadList: false,
    beforeUpload: (file) => {
      uploadMutation.mutate(file)
      return false
    },
  }

  return (
    <Card title="克隆音频">
      <Space direction="vertical" size={12} className="full-width">
        <Upload {...uploadProps}>
          <Button icon={<UploadOutlined />} loading={uploadMutation.isPending}>
            上传参考音频
          </Button>
        </Upload>
        <ResourceList<CloneVoice>
          loading={cloneVoicesQuery.isLoading}
          data={cloneVoicesQuery.data ?? []}
          empty="暂无克隆音频"
          getTitle={(item) => item.name}
          getDescription={(item) => item.filename ?? ''}
          getActions={(item) => [
            <Button
              key="delete"
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deleteMutation.isPending}
              onClick={() => {
                modal.confirm({
                  title: `删除克隆音频「${item.name}」？`,
                  okText: '删除',
                  okButtonProps: { danger: true },
                  cancelText: '取消',
                  onOk: () => deleteMutation.mutate(item.id),
                })
              }}
            />,
          ]}
        />
      </Space>
    </Card>
  )
}

function LoraPanel() {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const datasetsQuery = useQuery({ queryKey: ['lora-datasets'], queryFn: api.loraDatasets })
  const modelsQuery = useQuery({ queryKey: ['lora-models'], queryFn: api.loraModels })
  const trainingStatusQuery = useQuery({
    queryKey: ['task-status', 'lora_training'],
    queryFn: () => api.taskStatus('lora_training'),
    refetchInterval: (query) => query.state.data?.running ? 2000 : false,
  })
  const datasetGenStatusQuery = useQuery({
    queryKey: ['task-status', 'dataset_gen'],
    queryFn: () => api.taskStatus('dataset_gen'),
    refetchInterval: (query) => query.state.data?.running ? 2000 : false,
  })
  const [trainForm] = Form.useForm<LoraTrainFormValues>()
  const [generateDatasetForm] = Form.useForm<LoraGenerateDatasetFormValues>()
  const [testAdapterId, setTestAdapterId] = useState('')
  const [testText, setTestText] = useState('那座古老图书馆立在两条被遗忘小径的交叉口。')
  const [testInstruct, setTestInstruct] = useState('')
  const [testAudio, setTestAudio] = useState({ url: '', captionText: '' })

  const uploadDatasetMutation = useMutation({
    mutationFn: api.uploadLoraDataset,
    onSuccess: async () => {
      message.success('LoRA 数据集已上传')
      await queryClient.invalidateQueries({ queryKey: ['lora-datasets'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteDatasetMutation = useMutation({
    mutationFn: api.deleteLoraDataset,
    onSuccess: async () => {
      message.success('LoRA 数据集已删除')
      await queryClient.invalidateQueries({ queryKey: ['lora-datasets'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const trainMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => api.trainLora(values),
    onSuccess: async () => {
      message.success('LoRA 训练已启动')
      await queryClient.invalidateQueries({ queryKey: ['task-status', 'lora_training'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const generateDatasetMutation = useMutation({
    mutationFn: (values: ReturnType<typeof generateDatasetForm.getFieldsValue>) => api.generateLoraDataset({
      name: values.name,
      description: values.description,
      language: values.language,
      samples: (values.samples ?? []).flatMap((item) => {
        const text = item?.text?.trim()
        return text ? [{
          emotion: item.emotion ?? '',
          text,
        }] : []
      }),
    }),
    onSuccess: async () => {
      message.success('LoRA 数据集生成已启动')
      await queryClient.invalidateQueries({ queryKey: ['task-status', 'dataset_gen'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const downloadMutation = useMutation({
    mutationFn: api.downloadLoraModel,
    onSuccess: async () => {
      message.success('内置 LoRA 已下载')
      await queryClient.invalidateQueries({ queryKey: ['lora-models'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const previewMutation = useMutation({
    mutationFn: api.previewLoraModel,
    onSuccess: async (result) => {
      setTestAudio({ url: result.audio_url, captionText: LORA_PREVIEW_CAPTION })
      message.success('LoRA 试听已生成')
      await queryClient.invalidateQueries({ queryKey: ['lora-models'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const testMutation = useMutation({
    mutationFn: (values: { adapterId: string; text: string; instruct: string }) => api.testLoraModel({
      adapter_id: values.adapterId,
      text: values.text,
      instruct: values.instruct,
    }),
    onSuccess: async (result, values) => {
      setTestAudio({ url: result.audio_url, captionText: values.text })
      message.success('LoRA 测试音频已生成')
      await queryClient.invalidateQueries({ queryKey: ['lora-models'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteModelMutation = useMutation({
    mutationFn: api.deleteLoraModel,
    onSuccess: async () => {
      message.success('LoRA 模型已删除')
      await queryClient.invalidateQueries({ queryKey: ['lora-models'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const datasetUploadProps: UploadProps = {
    accept: '.zip',
    showUploadList: false,
    beforeUpload: (file) => {
      uploadDatasetMutation.mutate(file)
      return false
    },
  }

  return (
    <Card title="LoRA 训练">
      <Tabs
        items={[
          {
            key: 'datasets',
            label: '数据集',
            children: <LoraDatasetsTab
              datasets={datasetsQuery.data ?? []}
              loading={datasetsQuery.isLoading}
              uploadProps={datasetUploadProps}
              uploadPending={uploadDatasetMutation.isPending}
              deletePending={deleteDatasetMutation.isPending}
              onDelete={(datasetId) => {
                modal.confirm({
                  title: `删除数据集「${datasetId}」？`,
                  okText: '删除',
                  okButtonProps: { danger: true },
                  cancelText: '取消',
                  onOk: () => deleteDatasetMutation.mutate(datasetId),
                })
              }}
              form={generateDatasetForm}
              generatePending={generateDatasetMutation.isPending}
              generationStatus={datasetGenStatusQuery.data}
              onGenerate={(values) => generateDatasetMutation.mutate(values)}
            />,
          },
          {
            key: 'train',
            label: '训练',
            children: <LoraTrainingTab
              form={trainForm}
              datasets={datasetsQuery.data ?? []}
              trainingStatus={trainingStatusQuery.data}
              trainPending={trainMutation.isPending}
              onTrain={(values) => trainMutation.mutate(values)}
            />,
          },
          {
            key: 'models',
            label: '模型',
            children: <LoraModelsTab
              models={modelsQuery.data ?? []}
              loading={modelsQuery.isLoading}
              downloadPending={downloadMutation.isPending}
              previewPending={previewMutation.isPending}
              deletePending={deleteModelMutation.isPending}
              onDownload={(modelId) => downloadMutation.mutate(modelId)}
              onPreview={(modelId) => previewMutation.mutate(modelId)}
              onDelete={(model) => {
                modal.confirm({
                  title: `删除 LoRA「${model.name ?? model.id}」？`,
                  okText: '删除',
                  okButtonProps: { danger: true },
                  cancelText: '取消',
                  onOk: () => deleteModelMutation.mutate(model.id),
                })
              }}
              testAdapterId={testAdapterId}
              testText={testText}
              testInstruct={testInstruct}
              testAudio={testAudio}
              testPending={testMutation.isPending}
              onSelectTestAdapter={setTestAdapterId}
              onChangeTestText={setTestText}
              onChangeTestInstruct={setTestInstruct}
              onTest={() => testMutation.mutate({ adapterId: testAdapterId, text: testText.trim(), instruct: testInstruct })}
            />,
          },
        ]}
      />
    </Card>
  )
}

type LoraDatasetsTabProps = {
  datasets: LoraDataset[]
  loading: boolean
  uploadProps: UploadProps
  uploadPending: boolean
  deletePending: boolean
  onDelete: (datasetId: string) => void
  form: FormInstance<LoraGenerateDatasetFormValues>
  generatePending: boolean
  generationStatus?: TaskStatus
  onGenerate: (values: LoraGenerateDatasetFormValues) => void
}

function LoraDatasetsTab({
  datasets,
  loading,
  uploadProps,
  uploadPending,
  deletePending,
  onDelete,
  form,
  generatePending,
  generationStatus,
  onGenerate,
}: LoraDatasetsTabProps) {
  return (
    <Space direction="vertical" size={12} className="full-width">
      <Upload {...uploadProps}>
        <Button icon={<UploadOutlined />} loading={uploadPending}>
          上传 ZIP
        </Button>
      </Upload>
      <ResourceList<LoraDataset>
        loading={loading}
        data={datasets}
        empty="暂无 LoRA 数据集"
        getTitle={(item) => item.dataset_id}
        getDescription={(item) => `${formatCount(item.sample_count)} 条样本`}
        getActions={(item) => [
          <Button
            key="delete"
            size="small"
            danger
            icon={<DeleteOutlined />}
            loading={deletePending}
            onClick={() => onDelete(item.dataset_id)}
          />,
        ]}
      />
      <Divider />
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          language: 'zh',
          samples: [
            { emotion: '平静', text: '那座古老图书馆立在两条被遗忘小径的交叉口。' },
            { emotion: '紧张', text: '她屏住呼吸，听见门外传来极轻的脚步声。' },
            { emotion: '温柔', text: '没关系，慢慢来，我会一直在这里等你。' },
          ],
        }}
        onFinish={onGenerate}
      >
        <Row gutter={12}>
          <Col span={12}>
            <Form.Item name="name" label="新数据集名称" rules={[{ required: true, message: '请输入数据集名称' }]}>
              <Input />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item name="language" label="语言">
              <Select options={[{ value: 'zh', label: '中文' }, { value: 'en', label: '英文' }]} />
            </Form.Item>
          </Col>
          <Col span={24}>
            <Form.Item name="description" label="根声音描述" rules={[{ required: true, message: '请输入声音描述' }]}>
              <Input.TextArea rows={3} />
            </Form.Item>
          </Col>
        </Row>
        <Form.List name="samples">
          {(fields, { add, remove }) => (
            <Space direction="vertical" size={8} className="full-width">
              {fields.map((field) => (
                <Row key={field.key} gutter={8} align="top">
                  <Col span={6}>
                    <Form.Item {...field} name={[field.name, 'emotion']} label="情绪">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col span={16}>
                    <Form.Item {...field} name={[field.name, 'text']} label="样本文本" rules={[{ required: true, message: '请输入样本文本' }]}>
                      <Input.TextArea autoSize={{ minRows: 1, maxRows: 3 }} />
                    </Form.Item>
                  </Col>
                  <Col span={2}>
                    <Button danger icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
                  </Col>
                </Row>
              ))}
              <Button icon={<PlusOutlined />} onClick={() => add({ emotion: '', text: '' })}>
                添加样本
              </Button>
            </Space>
          )}
        </Form.List>
        <Button
          type="primary"
          loading={generatePending}
          disabled={generationStatus?.running}
          onClick={() => form.submit()}
        >
          直接生成训练数据集
        </Button>
      </Form>
      <TaskStatusCard status={generationStatus} />
    </Space>
  )
}

type LoraTrainingTabProps = {
  form: FormInstance<LoraTrainFormValues>
  datasets: LoraDataset[]
  trainingStatus?: TaskStatus
  trainPending: boolean
  onTrain: (values: LoraTrainFormValues) => void
}

function LoraTrainingTab({ form, datasets, trainingStatus, trainPending, onTrain }: LoraTrainingTabProps) {
  return (
    <Space direction="vertical" size={12} className="full-width">
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          epochs: 5,
          lr: 0.000005,
          batch_size: 1,
          lora_r: 32,
          lora_alpha: 128,
          gradient_accumulation_steps: 8,
          language: 'chinese',
        }}
        onFinish={onTrain}
      >
        <Form.Item name="name" label="Adapter 名称" rules={[{ required: true, message: '请输入名称' }]}>
          <Input />
        </Form.Item>
        <Form.Item name="dataset_id" label="训练数据集" rules={[{ required: true, message: '请选择数据集' }]}>
          <Select options={datasets.map((item) => ({ value: item.dataset_id, label: item.dataset_id }))} />
        </Form.Item>
        <Row gutter={12}>
          <Col span={12}><Form.Item name="epochs" label="Epochs"><InputNumber min={1} className="full-width" /></Form.Item></Col>
          <Col span={12}><Form.Item name="lr" label="LR"><InputNumber min={0} step={0.000001} className="full-width" /></Form.Item></Col>
          <Col span={12}><Form.Item name="batch_size" label="Batch Size"><InputNumber min={1} className="full-width" /></Form.Item></Col>
          <Col span={12}><Form.Item name="language" label="语言"><Select options={[{ value: 'chinese', label: 'Chinese' }, { value: 'english', label: 'English' }]} /></Form.Item></Col>
        </Row>
        <Button type="primary" loading={trainPending} disabled={trainingStatus?.running} onClick={() => form.submit()}>
          开始训练
        </Button>
      </Form>
      <TaskStatusCard status={trainingStatus} />
    </Space>
  )
}

type LoraModelsTabProps = {
  models: LoraModel[]
  loading: boolean
  downloadPending: boolean
  previewPending: boolean
  deletePending: boolean
  onDownload: (modelId: string) => void
  onPreview: (modelId: string) => void
  onDelete: (model: LoraModel) => void
  testAdapterId: string
  testText: string
  testInstruct: string
  testAudio: AudioState
  testPending: boolean
  onSelectTestAdapter: (modelId: string) => void
  onChangeTestText: (text: string) => void
  onChangeTestInstruct: (instruct: string) => void
  onTest: () => void
}

function LoraModelsTab({
  models,
  loading,
  downloadPending,
  previewPending,
  deletePending,
  onDownload,
  onPreview,
  onDelete,
  testAdapterId,
  testText,
  testInstruct,
  testAudio,
  testPending,
  onSelectTestAdapter,
  onChangeTestText,
  onChangeTestInstruct,
  onTest,
}: LoraModelsTabProps) {
  return (
    <Space direction="vertical" size={12} className="full-width">
      <ResourceList<LoraModel>
        loading={loading}
        data={models}
        empty="暂无 LoRA 模型"
        getTitle={(item) => item.name ?? item.id}
        getDescription={(item) => `${item.builtin ? '内置' : '训练'} · ${item.downloaded === false ? '未下载' : '可用'} · ${item.description ?? item.dataset_id ?? ''}`}
        getActions={(item) => [
          item.downloaded === false ? (
            <Button key="download" size="small" loading={downloadPending} onClick={() => onDownload(item.id)}>
              下载
            </Button>
          ) : (
            <Button key="preview" size="small" icon={<PlayCircleOutlined />} loading={previewPending} onClick={() => onPreview(item.id)} />
          ),
          <Button key="test" size="small" onClick={() => onSelectTestAdapter(item.id)}>
            测试
          </Button>,
          item.builtin ? null : (
            <Button
              key="delete"
              size="small"
              danger
              icon={<DeleteOutlined />}
              loading={deletePending}
              onClick={() => onDelete(item)}
            />
          ),
        ].flatMap((action) => action ? [action] : [])}
      />
      <Space direction="vertical" size={8} className="full-width">
        <Select
          value={testAdapterId || undefined}
          placeholder="选择测试模型"
          onChange={onSelectTestAdapter}
          options={models.map((item) => ({ value: item.id, label: item.name ?? item.id }))}
        />
        <Input.TextArea value={testText} rows={3} onChange={(event) => onChangeTestText(event.target.value)} />
        <Input value={testInstruct} onChange={(event) => onChangeTestInstruct(event.target.value)} placeholder="测试指令" />
        <Button
          type="primary"
          icon={<PlayCircleOutlined />}
          loading={testPending}
          disabled={!testAdapterId || !testText.trim()}
          onClick={onTest}
        >
          生成测试
        </Button>
        {testAudio.url ? <AudioPreview src={testAudio.url} label="LoRA 测试音频" captionText={testAudio.captionText} /> : null}
      </Space>
    </Space>
  )
}

function DatasetBuilder() {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const projectsQuery = useQuery({ queryKey: ['dataset-projects'], queryFn: api.datasetProjects })
  const [editor, dispatchEditor] = useReducer(datasetEditorReducer, INITIAL_DATASET_EDITOR_STATE)
  const { selectedName, createName, description, globalSeed, rows } = editor
  const activeName = selectedName || projectsQuery.data?.[0]?.name || ''

  const statusQuery = useQuery({
    queryKey: ['dataset-status', activeName],
    queryFn: () => api.datasetStatus(activeName),
    enabled: !!activeName,
    refetchInterval: (query) => query.state.data?.running ? 1500 : false,
  })

  const createMutation = useMutation({
    mutationFn: api.createDatasetProject,
    onSuccess: async (project) => {
      message.success('数据集项目已创建')
      dispatchEditor({ type: 'projectCreated', value: project.name })
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteDatasetProject,
    onSuccess: async () => {
      message.success('数据集项目已删除')
      dispatchEditor({ type: 'projectDeleted' })
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const saveMetaMutation = useMutation({
    mutationFn: () => api.updateDatasetMeta({ name: activeName, description, global_seed: globalSeed }),
    onSuccess: async () => {
      message.success('项目描述已保存')
      await queryClient.invalidateQueries({ queryKey: ['dataset-status', activeName] })
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const saveRowsMutation = useMutation({
    mutationFn: () => api.updateDatasetRows({ name: activeName, rows }),
    onSuccess: async () => {
      message.success('样本行已保存')
      await queryClient.invalidateQueries({ queryKey: ['dataset-status', activeName] })
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const generateOneMutation = useMutation({
    mutationFn: (index: number) => {
      const row = rows[index]
      return api.generateDatasetSample({
        dataset_name: activeName,
        sample_index: index,
        description: row?.emotion ? `${description}, ${row.emotion}` : description,
        text: row?.text ?? '',
        seed: parseSeed(row?.seed, -1),
      })
    },
    onSuccess: async () => {
      message.success('样本已生成')
      await statusQuery.refetch()
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const batchMutation = useMutation({
    mutationFn: (missingOnly: boolean) => api.generateDatasetBatch({
      name: activeName,
      description,
      samples: rows.map((row) => ({ emotion: row.emotion ?? '', text: row.text ?? '' })),
      indices: missingOnly
        ? rows.flatMap((row, index) => row.status === 'done' ? [] : [index])
        : undefined,
      global_seed: parseSeed(globalSeed, -1),
      seeds: rows.map((row) => parseSeed(row.seed, -1)),
    }),
    onSuccess: async () => {
      message.success('批量生成已启动')
      await statusQuery.refetch()
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const cancelMutation = useMutation({
    mutationFn: api.cancelDataset,
    onSuccess: async () => {
      message.success('已请求取消数据集生成')
      await statusQuery.refetch()
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const saveDatasetMutation = useMutation({
    mutationFn: () => api.saveDataset({ name: activeName, ref_index: firstDoneIndex(rows) }),
    onSuccess: async () => {
      message.success('训练数据集已保存')
      await queryClient.invalidateQueries({ queryKey: ['dataset-projects'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const updateRow = (index: number, patch: DatasetSample) => {
    dispatchEditor({ type: 'updateRow', index, patch })
  }

  const confirmDeleteProject = () => {
    modal.confirm({
      title: `删除数据集项目「${activeName}」？`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: () => deleteMutation.mutate(activeName),
    })
  }

  const loadStatusIntoEditor = () => {
    if (!statusQuery.data) return
    dispatchEditor({
      type: 'loadStatus',
      description: statusQuery.data.description ?? '',
      globalSeed: String(statusQuery.data.global_seed ?? ''),
      rows: statusQuery.data.samples ?? [],
    })
  }

  return (
    <Card title="数据集构建">
      <Space direction="vertical" size={12} className="full-width">
        <Flex gap={8} wrap>
          <Input
            value={createName}
            onChange={(event) => dispatchEditor({ type: 'setCreateName', value: event.target.value })}
            placeholder="新项目名称"
            className="flex-input"
            onPressEnter={() => createName.trim() && createMutation.mutate(createName.trim())}
          />
          <Button
            icon={<PlusOutlined />}
            loading={createMutation.isPending}
            disabled={!createName.trim()}
            onClick={() => createMutation.mutate(createName.trim())}
          >
            创建
          </Button>
        </Flex>
        <Select
          value={activeName || undefined}
          placeholder="选择数据集项目"
          loading={projectsQuery.isLoading}
          onChange={(value) => dispatchEditor({ type: 'selectProject', value })}
          options={(projectsQuery.data ?? []).map((project) => ({
            value: project.name,
            label: `${project.name} · ${formatCount(project.done_count)}/${formatCount(project.sample_count)}`,
          }))}
        />
        {activeName ? (
          <Space direction="vertical" size={12} className="full-width">
            <DatasetProjectEditor
              description={description}
              globalSeed={globalSeed}
              rows={rows}
              running={statusQuery.data?.running}
              logs={statusQuery.data?.logs ?? []}
              loadingStatus={statusQuery.isFetching}
              loadingRows={statusQuery.isLoading}
              savingMeta={saveMetaMutation.isPending}
              savingRows={saveRowsMutation.isPending}
              generatingBatch={batchMutation.isPending}
              cancelling={cancelMutation.isPending}
              savingDataset={saveDatasetMutation.isPending}
              deletingProject={deleteMutation.isPending}
              generatingOne={generateOneMutation.isPending}
              onLoad={loadStatusIntoEditor}
              onDescriptionChange={(value) => dispatchEditor({ type: 'setDescription', value })}
              onGlobalSeedChange={(value) => dispatchEditor({ type: 'setGlobalSeed', value })}
              onSaveMeta={() => saveMetaMutation.mutate()}
              onAddRow={() => dispatchEditor({ type: 'addRow' })}
              onSaveRows={() => saveRowsMutation.mutate()}
              onGenerateMissing={() => batchMutation.mutate(true)}
              onCancel={() => cancelMutation.mutate()}
              onUpdateRow={updateRow}
              onGenerateOne={(index) => generateOneMutation.mutate(index)}
              onDeleteRow={(index) => dispatchEditor({ type: 'deleteRow', index })}
              onSaveDataset={() => saveDatasetMutation.mutate()}
              onDeleteProject={confirmDeleteProject}
            />
          </Space>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据集项目" />
        )}
      </Space>
    </Card>
  )
}

type DatasetProjectEditorProps = {
  description: string
  globalSeed: string
  rows: DatasetSample[]
  running?: boolean
  logs: string[]
  loadingStatus: boolean
  loadingRows: boolean
  savingMeta: boolean
  savingRows: boolean
  generatingBatch: boolean
  cancelling: boolean
  savingDataset: boolean
  deletingProject: boolean
  generatingOne: boolean
  onLoad: () => void
  onDescriptionChange: (value: string) => void
  onGlobalSeedChange: (value: string) => void
  onSaveMeta: () => void
  onAddRow: () => void
  onSaveRows: () => void
  onGenerateMissing: () => void
  onCancel: () => void
  onUpdateRow: (index: number, patch: DatasetSample) => void
  onGenerateOne: (index: number) => void
  onDeleteRow: (index: number) => void
  onSaveDataset: () => void
  onDeleteProject: () => void
}

function DatasetProjectEditor({
  description,
  globalSeed,
  rows,
  running,
  logs,
  loadingStatus,
  loadingRows,
  savingMeta,
  savingRows,
  generatingBatch,
  cancelling,
  savingDataset,
  deletingProject,
  generatingOne,
  onLoad,
  onDescriptionChange,
  onGlobalSeedChange,
  onSaveMeta,
  onAddRow,
  onSaveRows,
  onGenerateMissing,
  onCancel,
  onUpdateRow,
  onGenerateOne,
  onDeleteRow,
  onSaveDataset,
  onDeleteProject,
}: DatasetProjectEditorProps) {
  return (
    <Space direction="vertical" size={12} className="full-width">
      <Button loading={loadingStatus} icon={<ReloadOutlined />} onClick={onLoad}>
        载入项目内容
      </Button>
      <Input.TextArea
        value={description}
        onChange={(event) => onDescriptionChange(event.target.value)}
        rows={3}
        placeholder="根声音描述"
      />
      <Flex gap={8} wrap>
        <Input
          value={globalSeed}
          onChange={(event) => onGlobalSeedChange(event.target.value)}
          placeholder="全局 Seed，空或 -1 为随机"
          className="flex-input"
        />
        <Button loading={savingMeta} onClick={onSaveMeta}>
          保存描述
        </Button>
      </Flex>
      <DatasetSamplesToolbar
        rows={rows}
        running={running}
        savingRows={savingRows}
        generatingBatch={generatingBatch}
        cancelling={cancelling}
        onAddRow={onAddRow}
        onSaveRows={onSaveRows}
        onGenerateMissing={onGenerateMissing}
        onCancel={onCancel}
      />
      <DatasetSamplesTable
        rows={rows}
        loading={loadingRows}
        generatingOne={generatingOne}
        onUpdateRow={onUpdateRow}
        onGenerateOne={onGenerateOne}
        onDeleteRow={onDeleteRow}
      />
      <Flex justify="space-between" gap={12} wrap>
        <Space wrap>
          <Button
            loading={savingDataset}
            disabled={!rows.some((row) => row.status === 'done')}
            onClick={onSaveDataset}
          >
            保存为训练数据集
          </Button>
          <Button danger loading={deletingProject} onClick={onDeleteProject}>
            删除项目
          </Button>
        </Space>
        <Badge status={running ? 'processing' : 'default'} text={running ? '生成中' : '空闲'} />
      </Flex>
      <div className="log-window compact-log">
        {logs.length
          ? logs.slice(-8).map((line, index) => <div key={`${index}-${line}`}>{line}</div>)
          : <Text type="secondary">暂无生成日志</Text>}
      </div>
    </Space>
  )
}

type DatasetSamplesToolbarProps = {
  rows: DatasetSample[]
  running?: boolean
  savingRows: boolean
  generatingBatch: boolean
  cancelling: boolean
  onAddRow: () => void
  onSaveRows: () => void
  onGenerateMissing: () => void
  onCancel: () => void
}

function DatasetSamplesToolbar({
  rows,
  running,
  savingRows,
  generatingBatch,
  cancelling,
  onAddRow,
  onSaveRows,
  onGenerateMissing,
  onCancel,
}: DatasetSamplesToolbarProps) {
  return (
    <Flex gap={8} wrap>
      <Button icon={<PlusOutlined />} onClick={onAddRow}>
        添加样本
      </Button>
      <Button loading={savingRows} onClick={onSaveRows}>
        保存行
      </Button>
      <Button
        type="primary"
        icon={<PlayCircleOutlined />}
        loading={generatingBatch}
        disabled={!rows.length || running}
        onClick={onGenerateMissing}
      >
        生成缺失
      </Button>
      <Button danger disabled={!running} loading={cancelling} onClick={onCancel}>
        取消
      </Button>
    </Flex>
  )
}

type DatasetSamplesTableProps = {
  rows: DatasetSample[]
  loading: boolean
  generatingOne: boolean
  onUpdateRow: (index: number, patch: DatasetSample) => void
  onGenerateOne: (index: number) => void
  onDeleteRow: (index: number) => void
}

function DatasetSamplesTable({
  rows,
  loading,
  generatingOne,
  onUpdateRow,
  onGenerateOne,
  onDeleteRow,
}: DatasetSamplesTableProps) {
  return (
    <Table<DatasetSample>
      size="small"
      rowKey={(_, index) => String(index)}
      loading={loading}
      dataSource={rows}
      pagination={{ pageSize: 4 }}
      scroll={{ x: 760 }}
      columns={[
        {
          title: '情绪',
          width: 140,
          render: (_, row, index) => (
            <Input value={row.emotion} onChange={(event) => onUpdateRow(index, { emotion: event.target.value })} />
          ),
        },
        {
          title: '文本',
          width: 280,
          render: (_, row, index) => (
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} value={row.text} onChange={(event) => onUpdateRow(index, { text: event.target.value })} />
          ),
        },
        {
          title: 'Seed',
          width: 100,
          render: (_, row, index) => (
            <Input value={String(row.seed ?? '')} onChange={(event) => onUpdateRow(index, { seed: event.target.value })} />
          ),
        },
        { title: '状态', width: 90, render: (_, row) => <Tag color={statusColor(row.status)}>{summarizeStatus(row.status)}</Tag> },
        {
          title: '试听',
          width: 180,
          render: (_, row) => row.audio_url
            ? <AudioPreview src={row.audio_url} label="数据集样本试听" captionText={row.text ?? ''} />
            : '-',
        },
        {
          title: '操作',
          width: 150,
          fixed: 'right',
          render: (_, row, index) => (
            <Space>
              <Button
                size="small"
                icon={<PlayCircleOutlined />}
                loading={generatingOne}
                disabled={!row.text?.trim()}
                onClick={() => onGenerateOne(index)}
              />
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => onDeleteRow(index)}
              />
            </Space>
          ),
        },
      ]}
    />
  )
}
