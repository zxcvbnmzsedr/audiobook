import {
  App as AntApp,
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Switch,
  Upload,
} from 'antd'
import { HighlightOutlined, PlayCircleOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { dashscopeModelOptions, voiceTypeOptions, volcengineResourceOptions } from '../voiceOptions'
import type { CharacterItem, EdgeVoice, VoiceConfigItem, VoiceItem, VolcengineVoiceOption } from '../types'
import { formatCount } from '../utils'

export function VoiceDrawer({ voice, edgeVoices, volcengineVoices, onRefreshVolcengineVoices, onClose, onSaveAndGenerate }: {
  voice: VoiceItem | CharacterItem | null
  edgeVoices: EdgeVoice[]
  volcengineVoices: Record<string, VolcengineVoiceOption[]>
  onRefreshVolcengineVoices: () => void
  onClose: () => void
  onSaveAndGenerate?: (values: VoiceConfigItem) => Promise<void> | void
}) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<VoiceConfigItem & { preview_text?: string }>()
  const [preview, setPreview] = useState<{ voiceName: string; url: string } | null>(null)
  const cloneVoicesQuery = useQuery({ queryKey: ['clone-voices'], queryFn: api.cloneVoices, enabled: !!voice })
  const designedVoicesQuery = useQuery({ queryKey: ['designed-voices'], queryFn: api.designedVoices, enabled: !!voice })
  const loraModelsQuery = useQuery({ queryKey: ['lora-models'], queryFn: api.loraModels, enabled: !!voice })
  const uploadCloneVoiceMutation = useMutation({
    mutationFn: api.uploadCloneVoice,
    onSuccess: async (result) => {
      message.success('参考声音已上传')
      form.setFieldsValue({ ref_audio: `/clone_voices/${result.filename}` })
      await queryClient.invalidateQueries({ queryKey: ['clone-voices'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const config = useMemo(() => (voice?.config ?? {}) as VoiceConfigItem, [voice])
  const metadata = useMemo(() => (voice?.metadata ?? {}) as Record<string, unknown>, [voice])
  const type = Form.useWatch('type', form) ?? config.type ?? 'edge'
  const currentCharacterStyle = Form.useWatch('character_style', form) ?? ''
  const selectedVolcengineResource = Form.useWatch('volcengine_resource_id', form) ?? config.volcengine_resource_id ?? 'seed-tts-2.0'
  const voiceProfileValue = voice?.voice_profile ?? metadata.voice_profile
  const voiceProfile = typeof voiceProfileValue === 'string' ? voiceProfileValue.trim() : ''
  const voiceProfileSourceValue = voice?.voice_profile_source ?? metadata.voice_profile_source
  const voiceProfileSource = voiceProfile
    ? voiceProfileSourceValue === 'narrator_style' ? '人物池旁白风格' : '人物池音色描述'
    : '未匹配到人物池声线'
  const currentCharacterStyleText = String(currentCharacterStyle || '').trim()
  const isVoiceProfileApplied = !!voiceProfile && currentCharacterStyleText === voiceProfile
  const syncAlertType = voiceProfile ? 'info' : 'warning'
  const syncAlertMessage = voiceProfile
    ? isVoiceProfileApplied ? '已写入当前表单' : '可同步人物池声线'
    : '没有可同步的角色声线'
  const syncAlertDescription = voiceProfile
    ? isVoiceProfileApplied
      ? '当前角色风格已等于人物池声线；点击保存后才会落盘到 voice_config.json。保存时如果 character_style 发生变化，后端会把该角色相关已生成音频标记为待重新生成。'
      : '点击恢复人物池声线会先写入上方角色风格字段；点击保存后才会落盘到 voice_config.json。保存时如果 character_style 发生变化，后端会把该角色相关已生成音频标记为待重新生成。'
    : '这个说话人没有匹配到 character_book.json 里的 voice_profile 或 narrator_style。先在人物池里把它合并到已有角色，或新增同名角色并填写音色描述；保存人物池后再回到这里同步。'
  const cloneReferenceOptions = useMemo(() => [
    ...(cloneVoicesQuery.data ?? []).map((item) => ({
      value: `clone:${item.id}`,
      label: `上传：${item.name}`,
      refAudio: item.filename ? `/clone_voices/${item.filename}` : '',
      refText: '',
    })),
    ...(designedVoicesQuery.data ?? []).map((item) => ({
      value: `design:${item.id}`,
      label: `设计：${item.name}`,
      refAudio: item.filename ? `/designed_voices/${item.filename}` : '',
      refText: item.sample_text ?? '',
    })),
  ], [cloneVoicesQuery.data, designedVoicesQuery.data])
  const loraOptions = useMemo(() => (loraModelsQuery.data ?? [])
    .filter((item) => !item.builtin)
    .map((item) => ({
      value: item.id,
      label: `${item.name ?? item.id}${item.description ? ` · ${item.description}` : ''}`,
      adapterPath: item.adapter_path ?? `lora_models/${item.id}`,
    })), [loraModelsQuery.data])
  const builtinLoraOptions = useMemo(() => (loraModelsQuery.data ?? [])
    .filter((item) => item.builtin)
    .map((item) => ({
      value: item.id,
      label: `${item.gender === 'male' ? '男声' : item.gender === 'female' ? '女声' : '内置'} · ${item.name ?? item.id}${item.downloaded === false ? '（未下载）' : ''}${item.description ? ` · ${item.description}` : ''}`,
      disabled: item.downloaded === false,
      adapterPath: item.adapter_path ?? `builtin_lora/${item.id}`,
    })), [loraModelsQuery.data])
  const volcengineSpeakerOptions = useMemo(() => {
    const currentSpeaker = form.getFieldValue('volcengine_speaker') as string | undefined
    const options = (volcengineVoices[selectedVolcengineResource] ?? volcengineVoices['seed-tts-2.0'] ?? [])
      .map((item) => ({
        value: item.value,
        label: `${item.label ?? item.name ?? item.value} · ${item.value}`,
      }))
    if (currentSpeaker && !options.some((item) => item.value === currentSpeaker)) {
      return [{ value: currentSpeaker, label: `${currentSpeaker}（自定义）` }, ...options]
    }
    return options
  }, [form, selectedVolcengineResource, volcengineVoices])

  useEffect(() => {
    if (!voice) {
      form.resetFields()
      return
    }
    form.setFieldsValue({
      type: config.type ?? 'edge',
      voice: config.voice ?? '',
      character_style: config.character_style || config.default_style || voiceProfile,
      seed: config.seed ?? '-1',
      confirmed: config.confirmed ?? voice.configured ?? false,
      ref_audio: config.ref_audio ?? '',
      ref_text: config.ref_text ?? '',
      adapter_id: config.adapter_id ?? '',
      adapter_path: config.adapter_path ?? '',
      description: config.description || voiceProfile,
      edge_voice: config.edge_voice ?? config.voice ?? 'zh-CN-XiaoxiaoNeural',
      edge_rate: config.edge_rate ?? '+0%',
      edge_pitch: config.edge_pitch ?? '+0Hz',
      dashscope_model: config.dashscope_model ?? 'qwen3-tts-instruct-flash',
      dashscope_voice: config.dashscope_voice ?? '',
      volcengine_resource_id: config.volcengine_resource_id ?? 'seed-tts-2.0',
      volcengine_speaker: config.volcengine_speaker ?? '',
      volcengine_sample_rate: config.volcengine_sample_rate ?? 24000,
      volcengine_speech_rate: config.volcengine_speech_rate ?? 0,
      volcengine_loudness_rate: config.volcengine_loudness_rate ?? 0,
      volcengine_emotion: config.volcengine_emotion ?? '',
      volcengine_emotion_scale: config.volcengine_emotion_scale ?? 4,
      preview_text: '那座古老图书馆立在两条被遗忘小径的交叉口。',
    })
  }, [config, form, voice, voiceProfile])

  const applyVoiceProfileToForm = () => {
    if (!voiceProfile) {
      message.warning('当前角色没有可同步的音色描述')
      return
    }
    form.setFieldsValue({ character_style: voiceProfile })
    message.success('已写入当前表单，保存后生效')
  }

  const normalizeVoiceConfig = (values: VoiceConfigItem & { preview_text?: string }): VoiceConfigItem => {
    const item: VoiceConfigItem = {
      type: values.type ?? 'edge',
      confirmed: values.confirmed ?? false,
      seed: String(values.seed ?? '-1'),
    }
    if (values.character_style) item.character_style = values.character_style
    if (item.type === 'edge') {
      item.edge_voice = values.edge_voice
      item.voice = values.edge_voice
      item.edge_rate = values.edge_rate || '+0%'
      item.edge_pitch = values.edge_pitch || '+0Hz'
    } else if (item.type === 'dashscope') {
      item.dashscope_model = values.dashscope_model
      item.dashscope_voice = values.dashscope_voice
    } else if (item.type === 'volcengine') {
      item.volcengine_resource_id = values.volcengine_resource_id || 'seed-tts-2.0'
      item.volcengine_speaker = values.volcengine_speaker
      item.volcengine_sample_rate = Number(values.volcengine_sample_rate || 24000)
      item.volcengine_speech_rate = Number(values.volcengine_speech_rate || 0)
      item.volcengine_loudness_rate = Number(values.volcengine_loudness_rate || 0)
      item.volcengine_emotion = values.volcengine_emotion
      item.volcengine_emotion_scale = Number(values.volcengine_emotion_scale || 4)
    } else if (item.type === 'clone') {
      item.ref_audio = values.ref_audio
      item.ref_text = values.ref_text
    } else if (item.type === 'design') {
      item.description = values.description
    } else if (item.type === 'lora' || item.type === 'builtin_lora') {
      item.adapter_id = values.adapter_id
      const selectedAdapter = (loraModelsQuery.data ?? []).find((model) => model.id === values.adapter_id)
      item.adapter_path = values.adapter_path || selectedAdapter?.adapter_path || (values.adapter_id ? `${item.type === 'builtin_lora' ? 'builtin_lora' : 'lora_models'}/${values.adapter_id}` : '')
    } else {
      item.voice = values.voice
    }
    return item
  }

  const saveMutation = useMutation({
    mutationFn: (values: VoiceConfigItem & { preview_text?: string }) => {
      if (!voice) throw new Error('未选择声音')
      return api.saveVoiceConfig({ [voice.name]: normalizeVoiceConfig(values) })
    },
    onSuccess: async () => {
      message.success('声音配置已保存')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['chunks'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const saveAndGenerateMutation = useMutation({
    mutationFn: async (values: VoiceConfigItem & { preview_text?: string }) => {
      if (!voice) throw new Error('未选择声音')
      const nextConfig = normalizeVoiceConfig(values)
      await api.saveVoiceConfig({ [voice.name]: nextConfig })
      await onSaveAndGenerate?.(nextConfig)
    },
    onSuccess: async () => {
      message.success('声音配置已保存，当前片段生成已启动')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['chunks'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const previewMutation = useMutation({
    mutationFn: (values: VoiceConfigItem & { preview_text?: string }) => {
      if (!voice) throw new Error('未选择声音')
      return api.voicePreview({
        voice_name: voice.name,
        text: values.preview_text,
        voice_config: normalizeVoiceConfig(values),
      })
    },
    onSuccess: (result) => {
      setPreview(voice ? { voiceName: voice.name, url: result.audio_url } : null)
      message.success('试听已生成')
    },
    onError: (error: Error) => message.error(error.message),
  })

  const previewUrl = voice && preview?.voiceName === voice.name ? preview.url : ''

  return (
    <Drawer
      open={!!voice}
      title={voice?.name ?? '声音配置'}
      width={680}
      onClose={onClose}
      extra={
        <Space>
          <Button loading={previewMutation.isPending} icon={<PlayCircleOutlined />} onClick={() => previewMutation.mutate(form.getFieldsValue())}>
            试听
          </Button>
          {onSaveAndGenerate ? (
            <Button loading={saveAndGenerateMutation.isPending} icon={<PlayCircleOutlined />} onClick={() => saveAndGenerateMutation.mutate(form.getFieldsValue())}>
              保存并生成本行
            </Button>
          ) : null}
          <Button type="primary" loading={saveMutation.isPending} icon={<SaveOutlined />} onClick={() => form.submit()}>
            保存
          </Button>
        </Space>
      }
    >
      {voice ? (
        <Space direction="vertical" size={16} className="full-width">
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="名称">{voice.name}</Descriptions.Item>
            <Descriptions.Item label="来源">{voice.source ?? '-'}</Descriptions.Item>
            <Descriptions.Item label="台词">{formatCount(voice.line_count)}</Descriptions.Item>
            <Descriptions.Item label="别名">{(voice.aliases ?? []).join('、') || '-'}</Descriptions.Item>
          </Descriptions>
          <Card
            size="small"
            title="角色声线同步"
            extra={
              <Button
                size="small"
                icon={<HighlightOutlined />}
                disabled={!voiceProfile || isVoiceProfileApplied}
                onClick={applyVoiceProfileToForm}
              >
                {isVoiceProfileApplied ? '已写入表单' : '恢复人物池声线'}
              </Button>
            }
          >
            <Space direction="vertical" size={8} className="full-width">
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="来源">{voiceProfileSource}</Descriptions.Item>
                <Descriptions.Item label="写入字段">voice_config.json / {voice.name} / character_style</Descriptions.Item>
                <Descriptions.Item label="当前值">{currentCharacterStyleText || '未填写'}</Descriptions.Item>
                <Descriptions.Item label="同步值">{voiceProfile || '当前角色没有音色描述'}</Descriptions.Item>
              </Descriptions>
              <Alert
                type={syncAlertType}
                showIcon
                message={syncAlertMessage}
                description={syncAlertDescription}
              />
            </Space>
          </Card>
          <Form form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
            <Row gutter={12}>
              <Col span={12}>
                <Form.Item name="type" label="声音类型" rules={[{ required: true, message: '请选择声音类型' }]}>
                  <Select options={voiceTypeOptions} />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="confirmed" label="确认使用" valuePropName="checked">
                  <Switch />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Form.Item name="character_style" label="角色风格 / 语音指令">
                  <Input.TextArea rows={3} placeholder="例如：温和、沉稳、语速略慢，适合旁白" />
                </Form.Item>
              </Col>
              {type === 'edge' && (
                <>
                  <Col span={24}>
                    <Form.Item name="edge_voice" label="Edge 声音" rules={[{ required: true, message: '请选择 Edge 声音' }]}>
                      <Select
                        showSearch
                        optionFilterProp="label"
                        options={edgeVoices.map((item) => ({
                          value: item.id,
                          label: `${item.id} · ${item.locale ?? ''} · ${item.gender ?? ''}`,
                        }))}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={12}><Form.Item name="edge_rate" label="语速"><Input placeholder="+0%" /></Form.Item></Col>
                  <Col span={12}><Form.Item name="edge_pitch" label="音调"><Input placeholder="+0Hz" /></Form.Item></Col>
                </>
              )}
              {type === 'dashscope' && (
                <>
                  <Col span={12}><Form.Item name="dashscope_model" label="模型"><Select options={dashscopeModelOptions} /></Form.Item></Col>
                  <Col span={12}><Form.Item name="dashscope_voice" label="音色 ID"><Input placeholder="例如 Cherry" /></Form.Item></Col>
                </>
              )}
              {type === 'volcengine' && (
                <>
                  <Col span={12}><Form.Item name="volcengine_resource_id" label="资源"><Select options={volcengineResourceOptions} /></Form.Item></Col>
                  <Col span={12}>
                    <Form.Item name="volcengine_speaker" label="音色 ID">
                      <Select
                        showSearch
                        allowClear
                        options={volcengineSpeakerOptions}
                        optionFilterProp="label"
                        dropdownRender={(menu) => (
                          <>
                            {menu}
                            <Divider className="compact-divider" />
                            <Button type="link" size="small" onClick={onRefreshVolcengineVoices}>
                              刷新火山音色列表
                            </Button>
                          </>
                        )}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={8}><Form.Item name="volcengine_sample_rate" label="采样率"><Select options={[16000, 24000, 32000, 44100, 48000].map((value) => ({ value, label: `${value} Hz` }))} /></Form.Item></Col>
                  <Col span={8}><Form.Item name="volcengine_speech_rate" label="语速"><InputNumber min={-50} max={100} className="full-width" /></Form.Item></Col>
                  <Col span={8}><Form.Item name="volcengine_loudness_rate" label="音量"><InputNumber min={-50} max={100} className="full-width" /></Form.Item></Col>
                  <Col span={12}><Form.Item name="volcengine_emotion" label="情感"><Input /></Form.Item></Col>
                  <Col span={12}><Form.Item name="volcengine_emotion_scale" label="情感强度"><InputNumber min={1} max={10} className="full-width" /></Form.Item></Col>
                </>
              )}
              {type === 'clone' && (
                <>
                  <Col span={24}>
                    <Form.Item label="选择参考声音">
                      <Space.Compact className="full-width">
                        <Select
                          allowClear
                          showSearch
                          loading={cloneVoicesQuery.isLoading || designedVoicesQuery.isLoading}
                          optionFilterProp="label"
                          options={cloneReferenceOptions}
                          onChange={(value) => {
                            const selected = cloneReferenceOptions.find((item) => item.value === value)
                            if (!selected) return
                            form.setFieldsValue({
                              ref_audio: selected.refAudio,
                              ref_text: selected.refText || form.getFieldValue('ref_text'),
                            })
                          }}
                          placeholder="选择已上传或已设计的声音"
                        />
                        <Upload
                          accept=".wav,.mp3,.flac,.ogg"
                          showUploadList={false}
                          beforeUpload={(file) => {
                            uploadCloneVoiceMutation.mutate(file)
                            return false
                          }}
                        >
                          <Button loading={uploadCloneVoiceMutation.isPending}>上传</Button>
                        </Upload>
                      </Space.Compact>
                    </Form.Item>
                  </Col>
                  <Col span={24}><Form.Item name="ref_audio" label="参考音频路径"><Input /></Form.Item></Col>
                  <Col span={24}><Form.Item name="ref_text" label="参考文本"><Input.TextArea rows={3} /></Form.Item></Col>
                </>
              )}
              {type === 'design' && (
                <Col span={24}><Form.Item name="description" label="声音描述"><Input.TextArea rows={4} /></Form.Item></Col>
              )}
              {(type === 'lora' || type === 'builtin_lora') && (
                <>
                  <Col span={24}>
                    <Form.Item name="adapter_id" label={type === 'builtin_lora' ? '内置声音' : '训练适配器'}>
                      <Select
                        allowClear
                        showSearch
                        loading={loraModelsQuery.isLoading}
                        optionFilterProp="label"
                        options={type === 'builtin_lora' ? builtinLoraOptions : loraOptions}
                        onChange={(value) => {
                          const selected = [...builtinLoraOptions, ...loraOptions].find((item) => item.value === value)
                          form.setFieldsValue({ adapter_path: selected?.adapterPath ?? '' })
                        }}
                      />
                    </Form.Item>
                  </Col>
                  <Col span={24}><Form.Item name="adapter_path" label="Adapter Path"><Input /></Form.Item></Col>
                </>
              )}
              {type === 'custom' && (
                <Col span={24}><Form.Item name="voice" label="本地声音标识"><Input /></Form.Item></Col>
              )}
              <Col span={12}><Form.Item name="seed" label="Seed"><Input /></Form.Item></Col>
              <Col span={24}><Form.Item name="preview_text" label="试听文本"><Input.TextArea rows={3} /></Form.Item></Col>
            </Row>
          </Form>
          {previewUrl ? <audio className="audio-player" controls src={previewUrl} /> : null}
          <Card size="small" title="当前原始配置">
            <pre className="json-preview">{JSON.stringify(config, null, 2)}</pre>
          </Card>
        </Space>
      ) : null}
    </Drawer>
  )
}
