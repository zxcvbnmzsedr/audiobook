import {
  App as AntApp,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Switch,
  Tabs,
} from 'antd'
import { ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '../api'
import { ResourcePanel } from '../components/ResourcePanel'
import type { AppConfig } from '../types'
import { volcengineResourceOptions } from '../voiceOptions'

const settingsTabs = [
  { key: 'llm', label: 'LLM' },
  { key: 'tts', label: 'TTS' },
  { key: 'generation', label: '生成' },
  { key: 'prompts', label: '提示词' },
]

export function SettingsPage() {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const configQuery = useQuery({ queryKey: ['config'], queryFn: api.config })
  const [activeTab, setActiveTab] = useState('llm')
  const [form] = Form.useForm<AppConfig>()

  const saveMutation = useMutation({
    mutationFn: (values: AppConfig) => api.saveConfig(values),
    onSuccess: async () => {
      message.success('配置已保存')
      await queryClient.invalidateQueries({ queryKey: ['config'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  useEffect(() => {
    if (configQuery.data) form.setFieldsValue(configQuery.data)
  }, [configQuery.data, form])

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={16}>
        <Card
          title="系统设置"
          extra={
            <Space>
              <Button icon={<ReloadOutlined />} onClick={() => configQuery.refetch()}>刷新</Button>
              <Button type="primary" icon={<SaveOutlined />} loading={saveMutation.isPending} onClick={() => form.submit()}>
                保存
              </Button>
            </Space>
          }
        >
          <Spin spinning={configQuery.isLoading}>
            <Form form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
              <Tabs activeKey={activeTab} onChange={setActiveTab} items={settingsTabs.map((tab) => ({ key: tab.key, label: tab.label }))} />
              {activeTab === 'llm' && <LlmSettings />}
              {activeTab === 'tts' && <TtsSettings />}
              {activeTab === 'generation' && <GenerationSettings />}
              {activeTab === 'prompts' && <PromptSettings />}
            </Form>
          </Spin>
        </Card>
      </Col>
      <Col xs={24} xl={8}>
        <ResourcePanel />
      </Col>
    </Row>
  )
}

function LlmSettings() {
  return (
    <Row gutter={12}>
      <Col span={12}>
        <Form.Item name={['llm', 'provider']} label="Provider">
          <Select options={[{ value: 'anthropic', label: 'Anthropic' }, { value: 'openai', label: 'OpenAI / 兼容服务' }]} />
        </Form.Item>
      </Col>
      <Col span={12}><Form.Item name={['llm', 'model_name']} label="模型"><Input /></Form.Item></Col>
      <Col span={24}><Form.Item name={['llm', 'base_url']} label="Base URL"><Input /></Form.Item></Col>
      <Col span={24}><Form.Item name={['llm', 'api_key']} label="API Key"><Input.Password /></Form.Item></Col>
      <Col span={12}>
        <Form.Item name={['llm', 'openai_api_type']} label="OpenAI API 类型">
          <Select options={[{ value: 'responses', label: 'Responses' }, { value: 'chat', label: 'Chat Completions' }]} />
        </Form.Item>
      </Col>
    </Row>
  )
}

function TtsSettings() {
  return (
    <Row gutter={12}>
      <Col span={12}><Form.Item name={['tts', 'mode']} label="TTS 模式"><Select options={[{ value: 'local', label: 'Local' }, { value: 'edge', label: 'Edge' }, { value: 'dashscope', label: 'DashScope' }, { value: 'volcengine', label: 'Volcengine' }]} /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'language']} label="语言"><Select options={[{ value: 'Chinese', label: 'Chinese' }, { value: 'English', label: 'English' }]} /></Form.Item></Col>
      <Col span={24}><Form.Item name={['tts', 'url']} label="本地 TTS URL"><Input /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'device']} label="设备"><Select options={[{ value: 'auto', label: 'auto' }, { value: 'cpu', label: 'cpu' }, { value: 'cuda:0', label: 'cuda:0' }]} /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'parallel_workers']} label="并行数"><InputNumber min={1} className="full-width" /></Form.Item></Col>
      <Col span={24}><Form.Item name={['tts', 'dashscope_api_key']} label="DashScope API Key"><Input.Password /></Form.Item></Col>
      <Col span={24}><Form.Item name={['tts', 'volcengine_api_key']} label="Volcengine API Key"><Input.Password /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'volcengine_resource_id']} label="火山 Resource ID"><Select options={volcengineResourceOptions} /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'volcengine_sample_rate']} label="火山采样率"><Select options={[16000, 24000, 32000, 44100, 48000].map((value) => ({ value, label: `${value} Hz` }))} /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'volcengine_uid']} label="火山 User ID"><Input /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'batch_seed']} label="批量 Seed"><InputNumber className="full-width" placeholder="-1 或留空为随机" /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'pause_between_speakers_ms']} label="不同说话人停顿 ms"><InputNumber min={0} className="full-width" /></Form.Item></Col>
      <Col span={12}><Form.Item name={['tts', 'pause_same_speaker_ms']} label="同说话人停顿 ms"><InputNumber min={0} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['tts', 'compile_codec']} label="Compile Codec" valuePropName="checked"><Switch /></Form.Item></Col>
      <Col span={8}><Form.Item name={['tts', 'batch_group_by_type']} label="按声音类型分组" valuePropName="checked"><Switch /></Form.Item></Col>
      <Col span={8}><Form.Item name={['tts', 'sub_batch_enabled']} label="启用子批次" valuePropName="checked"><Switch /></Form.Item></Col>
      <Col span={8}><Form.Item name={['tts', 'sub_batch_min_size']} label="最小子批次数"><InputNumber min={1} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['tts', 'sub_batch_ratio']} label="长度比例"><InputNumber min={1} step={0.5} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['tts', 'sub_batch_max_items']} label="每批最大条数"><InputNumber min={0} className="full-width" /></Form.Item></Col>
    </Row>
  )
}

function GenerationSettings() {
  return (
    <Row gutter={12}>
      <Col span={12}><Form.Item name={['generation', 'model_name']} label="生成模型"><Input /></Form.Item></Col>
      <Col span={12}><Form.Item name={['generation', 'max_tokens']} label="Max Tokens"><InputNumber min={1} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['generation', 'temperature']} label="Temperature"><InputNumber min={0} max={2} step={0.1} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['generation', 'top_p']} label="Top P"><InputNumber min={0} max={1} step={0.05} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['generation', 'top_k']} label="Top K"><InputNumber min={0} max={200} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['generation', 'min_p']} label="Min P"><InputNumber min={0} max={1} step={0.01} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['generation', 'presence_penalty']} label="存在惩罚"><InputNumber min={-2} max={2} step={0.1} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['generation', 'chunk_size']} label="分块字数"><InputNumber min={200} className="full-width" /></Form.Item></Col>
      <Col span={8}><Form.Item name={['generation', 'review_batch_size']} label="审校批量"><InputNumber min={1} className="full-width" /></Form.Item></Col>
      <Col span={24}>
        <Form.Item name={['generation', 'banned_tokens']} label="禁用 Token">
          <Select mode="tags" tokenSeparators={[',']} open={false} placeholder="输入后回车，例如 <think>" />
        </Form.Item>
      </Col>
      <Col span={12}><Form.Item name={['generation', 'enable_chapter_memory']} label="章节记忆" valuePropName="checked"><Switch /></Form.Item></Col>
      <Col span={12}><Form.Item name={['generation', 'merge_narrators']} label="合并旁白" valuePropName="checked"><Switch /></Form.Item></Col>
    </Row>
  )
}

function PromptSettings() {
  const { message } = AntApp.useApp()
  const form = Form.useFormInstance<AppConfig>()
  const [resettingPrompts, setResettingPrompts] = useState(false)

  const resetPrompts = async () => {
    setResettingPrompts(true)
    try {
      const prompts = await api.defaultPrompts()
      form.setFieldsValue({ prompts })
      message.success('默认提示词已恢复')
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      setResettingPrompts(false)
    }
  }

  return (
    <Space direction="vertical" size={12} className="full-width">
      <Button loading={resettingPrompts} onClick={resetPrompts}>
        恢复默认提示词
      </Button>
      <Row gutter={12}>
        <Col span={24}><Form.Item name={['prompts', 'system_prompt']} label="生成系统提示词"><Input.TextArea rows={10} className="mono" /></Form.Item></Col>
        <Col span={24}><Form.Item name={['prompts', 'user_prompt']} label="生成用户提示词"><Input.TextArea rows={5} className="mono" /></Form.Item></Col>
        <Col span={24}><Form.Item name={['prompts', 'review_system_prompt']} label="审校系统提示词"><Input.TextArea rows={8} className="mono" /></Form.Item></Col>
        <Col span={24}><Form.Item name={['prompts', 'review_user_prompt']} label="审校用户提示词"><Input.TextArea rows={5} className="mono" /></Form.Item></Col>
      </Row>
    </Space>
  )
}
