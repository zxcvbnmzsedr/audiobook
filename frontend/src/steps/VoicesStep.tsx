import {
  App as AntApp,
  Button,
  Card,
  Col,
  Divider,
  Drawer,
  Flex,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { SorterResult } from 'antd/es/table/interface'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import type { SpeakerSortOptions } from '../api'
import { api } from '../api'
import { VoiceDrawer } from '../components/VoiceDrawer'
import { hasConfirmedVoiceConfig, hasCustomizedVoiceConfig, splitList, voiceConfigLabel } from '../domain'
import type { CharacterItem, CharactersResponse, VoiceItem } from '../types'
import { formatCount } from '../utils'

const { Text } = Typography

export function VoicesStep() {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [voicesSort, setVoicesSort] = useState<SpeakerSortOptions>({})
  const [voicesPageSize, setVoicesPageSize] = useState(8)
  const charactersQuery = useQuery({ queryKey: ['characters'], queryFn: () => api.characters() })
  const voicesQuery = useQuery({ queryKey: ['voices', voicesSort], queryFn: () => api.voices(voicesSort) })
  const edgeVoicesQuery = useQuery({ queryKey: ['edge-voices'], queryFn: api.edgeVoices })
  const volcengineVoicesQuery = useQuery({ queryKey: ['volcengine-voices'], queryFn: () => api.volcengineVoices() })
  const [editingVoice, setEditingVoice] = useState<VoiceItem | CharacterItem | null>(null)
  const [characterEditorOpen, setCharacterEditorOpen] = useState(false)

  const compactMutation = useMutation({
    mutationFn: api.compactCharacters,
    onSuccess: async () => {
      message.success('人物池已压缩')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const voices = voicesQuery.data ?? []
  const confirmedVoiceCount = voices.filter(hasConfirmedVoiceConfig).length
  const customizedVoiceCount = voices.filter(hasCustomizedVoiceConfig).length
  const missingVoiceCount = Math.max(voices.length - confirmedVoiceCount - customizedVoiceCount, 0)

  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <Card
          title="角色与声音"
          extra={
            <Space wrap>
              <Button
                icon={<ReloadOutlined />}
                onClick={() => {
                  voicesQuery.refetch()
                  charactersQuery.refetch()
                }}
              />
              <Button icon={<EditOutlined />} onClick={() => setCharacterEditorOpen(true)}>
                编辑人物池
              </Button>
              <Button loading={compactMutation.isPending} onClick={() => compactMutation.mutate()}>
                压缩人物池
              </Button>
            </Space>
          }
        >
          <Row gutter={12} className="metric-row">
            <Col xs={12} md={6}><Statistic title="实际声音" value={voices.length} /></Col>
            <Col xs={12} md={6}><Statistic title="角色池" value={charactersQuery.data?.total ?? 0} /></Col>
            <Col xs={12} md={6}><Statistic title="已确认" value={confirmedVoiceCount} /></Col>
            <Col xs={12} md={6}><Statistic title="待配置" value={missingVoiceCount + customizedVoiceCount} /></Col>
          </Row>
          <Table<VoiceItem>
            size="small"
            rowKey="name"
            loading={voicesQuery.isLoading}
            dataSource={voices}
            pagination={{
              pageSize: voicesPageSize,
              showSizeChanger: true,
              onShowSizeChange: (_, size) => setVoicesPageSize(size),
            }}
            onChange={(_, __, sorter) => setVoicesSort(sorterToSpeakerSort(sorter))}
            columns={[
              { title: '角色/说话人', dataIndex: 'name', render: (value, record) => <SpeakerCell name={String(value)} record={record} /> },
              { title: '条数', dataIndex: 'line_count', width: 90, sorter: true, sortOrder: sortOrderFor(voicesSort, 'line_count'), render: formatCount },
              { title: '字数', dataIndex: 'char_count', width: 100, sorter: true, sortOrder: sortOrderFor(voicesSort, 'char_count'), render: formatCount },
              { title: '来源', dataIndex: 'source', width: 110, render: (_, record) => <SourceTag record={record} /> },
              { title: '别名/脚本说话人', width: 220, render: (_, record) => <SpeakerAliases record={record} /> },
              { title: '音色', width: 140, render: (_, record) => voiceConfigLabel(record.config) },
              { title: '状态', width: 110, render: (_, record) => <VoiceStatusTag record={record} confirmedLabel="已确认" pendingLabel="未配置" /> },
              { title: '操作', width: 110, render: (_, record) => <Button size="small" onClick={() => setEditingVoice(record)}>编辑音色</Button> },
            ]}
          />
          <Divider />
          <Space direction="vertical" size={8} className="full-width">
            <Text type="secondary">
              Edge 可选声音：{edgeVoicesQuery.data?.length ?? 0} 个；火山音色来源：{volcengineVoicesQuery.data?.source ?? '未知'}
              {volcengineVoicesQuery.data?.cache_hit ? '（缓存）' : ''}
            </Text>
          </Space>
        </Card>
      </Col>
      <VoiceDrawer
        voice={editingVoice}
        edgeVoices={edgeVoicesQuery.data ?? []}
        volcengineVoices={volcengineVoicesQuery.data?.voices ?? {}}
        onRefreshVolcengineVoices={() => volcengineVoicesQuery.refetch()}
        onClose={() => setEditingVoice(null)}
      />
      <CharacterBookDrawer
        open={characterEditorOpen}
        onClose={() => setCharacterEditorOpen(false)}
        charactersResponse={charactersQuery.data}
      />
    </Row>
  )
}

function SpeakerCell({ name, record }: { name: string; record: VoiceItem }) {
  return (
    <Space>
      <span>{name}</span>
      {record.is_narrator ? <Tag>旁白</Tag> : null}
    </Space>
  )
}

function SourceTag({ record }: { record: VoiceItem }) {
  const source = String(record.source ?? '')
  if (record.is_narrator || source === 'narrator') return <Tag color="purple">旁白</Tag>
  if (source === 'character_book') return <Tag color="blue">角色表</Tag>
  if (source === 'script') return <Tag>脚本</Tag>
  return <Tag>{source || '未知'}</Tag>
}

function SpeakerAliases({ record }: { record: VoiceItem }) {
  const names = [
    ...(Array.isArray(record.aliases) ? record.aliases : []),
    ...(Array.isArray(record.inherited_speakers) ? record.inherited_speakers as string[] : []),
    ...(Array.isArray(record.raw_speakers) ? record.raw_speakers : []),
  ]
    .map((name) => String(name ?? '').trim())
    .filter((name, index, list) => name && name !== record.name && list.indexOf(name) === index)
  if (!names.length) return <Text type="secondary">-</Text>
  return <Text type="secondary">{names.slice(0, 5).join('、')}{names.length > 5 ? ` 等 ${formatCount(names.length)} 个` : ''}</Text>
}

function sorterToSpeakerSort<T>(sorter: SorterResult<T> | SorterResult<T>[]): SpeakerSortOptions {
  const current = Array.isArray(sorter) ? sorter[0] : sorter
  if ((current?.field !== 'line_count' && current?.field !== 'char_count') || !current.order) return {}
  return {
    sortBy: current.field,
    sortOrder: current.order === 'ascend' ? 'asc' : 'desc',
  }
}

function sortOrderFor(sort: SpeakerSortOptions, field: SpeakerSortOptions['sortBy']) {
  if (sort.sortBy !== field) return undefined
  return sort.sortOrder === 'asc' ? 'ascend' : 'descend'
}

function VoiceStatusTag({ record, confirmedLabel, pendingLabel }: {
  record: CharacterItem | VoiceItem
  confirmedLabel: string
  pendingLabel: string
}) {
  if (hasConfirmedVoiceConfig(record)) {
    return <Tag color="green">{confirmedLabel}</Tag>
  }
  if (hasCustomizedVoiceConfig(record)) {
    return <Tag color="blue">待确认</Tag>
  }
  return <Tag color="orange">{pendingLabel}</Tag>
}

function CharacterBookDrawer({ open, onClose, charactersResponse }: {
  open: boolean
  onClose: () => void
  charactersResponse?: CharactersResponse
}) {
  const { message } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<{
    narrator_style: string
    genre: string
    key_terms: string
    normalize_script_speakers: boolean
    characters: Array<{
      name: string
      aliases: string
      traits: string
      voice_profile: string
      confidence?: number
    }>
  }>()
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importMerge, setImportMerge] = useState(true)
  const [importPreview, setImportPreview] = useState<Record<string, unknown> | null>(null)
  const [selectedCharacterKeys, setSelectedCharacterKeys] = useState<number[]>([])

  useEffect(() => {
    if (!open || !charactersResponse) return
    form.setFieldsValue({
      narrator_style: charactersResponse.narrator_style ?? '',
      genre: charactersResponse.genre ?? '',
      key_terms: (charactersResponse.key_terms ?? []).join('、'),
      normalize_script_speakers: true,
      characters: (charactersResponse.characters ?? []).map((character) => ({
        name: character.name,
        aliases: (character.aliases ?? []).join('、'),
        traits: character.traits ?? '',
        voice_profile: character.voice_profile ?? '',
        confidence: typeof character.confidence === 'number' ? character.confidence : undefined,
      })),
    })
  }, [charactersResponse, form, open])

  const invalidateCharacters = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['characters'] }),
      queryClient.invalidateQueries({ queryKey: ['voices'] }),
      queryClient.invalidateQueries({ queryKey: ['chunks'] }),
      queryClient.invalidateQueries({ queryKey: ['script-issues'] }),
      queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
    ])
  }

  const saveMutation = useMutation({
    mutationFn: (values: ReturnType<typeof form.getFieldsValue>) => api.saveCharacters({
      narrator_style: values.narrator_style ?? '',
      genre: values.genre ?? '',
      key_terms: splitList(values.key_terms),
      normalize_script_speakers: values.normalize_script_speakers ?? true,
      characters: (values.characters ?? [])
        .filter((item) => item?.name?.trim())
        .map((item) => ({
          name: item.name.trim(),
          aliases: splitList(item.aliases),
          traits: item.traits ?? '',
          voice_profile: item.voice_profile ?? '',
          confidence: item.confidence,
        })),
    }),
    onSuccess: async () => {
      message.success('人物池已保存')
      await invalidateCharacters()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const importMutation = useMutation({
    mutationFn: (dryRun: boolean) => api.importCharacters({
      content: importText,
      merge: importMerge,
      normalize_script_speakers: true,
      dry_run: dryRun,
    }),
    onSuccess: async (result, dryRun) => {
      setImportPreview(result)
      if (dryRun) {
        message.success('人物池导入预览已生成')
        return
      }
      message.success('人物池已导入')
      setImportOpen(false)
      setImportText('')
      await invalidateCharacters()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const getCharacterRows = () => form.getFieldValue('characters') as Array<{
    name?: string
    aliases?: string
    traits?: string
    voice_profile?: string
    confidence?: number
  }> ?? []

  const mergeSelectedCharacters = (fields: Array<{ key: number; name: number }>) => {
    const selectedFields = fields.filter((field) => selectedCharacterKeys.includes(field.key))
    if (selectedFields.length < 2) {
      message.warning('请至少选择两个角色再合并')
      return
    }

    const rows = getCharacterRows()
    const selectedIndices = selectedFields.map((field) => field.name).sort((a, b) => a - b)
    const targetIndex = selectedIndices[0]
    const target = { ...(rows[targetIndex] ?? {}) }
    const aliases = new Set(splitList(target.aliases))
    const traitParts = splitList(target.traits)
    const voiceParts = splitList(target.voice_profile)

    selectedIndices.slice(1).forEach((index) => {
      const row = rows[index]
      if (!row) return
      if (row.name?.trim()) aliases.add(row.name.trim())
      splitList(row.aliases).forEach((alias) => aliases.add(alias))
      splitList(row.traits).forEach((trait) => traitParts.push(trait))
      splitList(row.voice_profile).forEach((profile) => voiceParts.push(profile))
    })

    const selectedSet = new Set(selectedIndices.slice(1))
    const nextRows = rows
      .map((row, index) => index === targetIndex ? {
        ...target,
        aliases: [...aliases].join('、'),
        traits: [...new Set(traitParts)].join('；'),
        voice_profile: [...new Set(voiceParts)].join('；'),
      } : row)
      .filter((_, index) => !selectedSet.has(index))

    form.setFieldsValue({ characters: nextRows })
    setSelectedCharacterKeys([])
    message.success('已合并到第一个选中的角色，保存后生效')
  }

  const deleteSelectedCharacters = (fields: Array<{ key: number; name: number }>) => {
    const selectedFields = fields.filter((field) => selectedCharacterKeys.includes(field.key))
    if (!selectedFields.length) {
      message.warning('请先选择要删除的角色')
      return
    }

    const selectedIndices = new Set(selectedFields.map((field) => field.name))
    const nextRows = getCharacterRows().filter((_, index) => !selectedIndices.has(index))
    form.setFieldsValue({ characters: nextRows })
    setSelectedCharacterKeys([])
    message.success('已删除选中角色，保存后生效')
  }

  return (
    <Drawer
      open={open}
      title="人物池编辑"
      width={840}
      onClose={onClose}
      extra={
        <Space>
          <Button onClick={() => setImportOpen(true)}>导入 JSON</Button>
          <Button type="primary" icon={<SaveOutlined />} loading={saveMutation.isPending} onClick={() => form.submit()}>
            保存
          </Button>
        </Space>
      }
    >
      <Form form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
        <Row gutter={12}>
          <Col span={12}><Form.Item name="genre" label="类型/题材"><Input /></Form.Item></Col>
          <Col span={12}><Form.Item name="key_terms" label="关键术语"><Input /></Form.Item></Col>
          <Col span={24}><Form.Item name="narrator_style" label="旁白风格"><Input.TextArea rows={3} /></Form.Item></Col>
          <Col span={24}><Form.Item name="normalize_script_speakers" label="保存时规范化脚本说话人" valuePropName="checked" initialValue><Switch /></Form.Item></Col>
        </Row>
        <Form.List name="characters">
          {(fields, { add, remove }) => (
            <Space direction="vertical" size={12} className="full-width">
              <Flex gap={8} wrap>
                <Button icon={<PlusOutlined />} onClick={() => add({ name: '', aliases: '', traits: '', voice_profile: '' })}>
                  新增角色
                </Button>
                <Button disabled={selectedCharacterKeys.length < 2} onClick={() => mergeSelectedCharacters(fields)}>
                  合并选中
                </Button>
                <Button danger disabled={!selectedCharacterKeys.length} onClick={() => deleteSelectedCharacters(fields)}>
                  删除选中
                </Button>
                <Text type="secondary">已选 {formatCount(selectedCharacterKeys.length)} 个</Text>
              </Flex>
              {fields.map((field) => (
                <Card
                  key={field.key}
                  size="small"
                  title={
                    <Space>
                      <input
                        type="checkbox"
                        checked={selectedCharacterKeys.includes(field.key)}
                        onChange={(event) => {
                          setSelectedCharacterKeys((current) => event.target.checked
                            ? [...current, field.key]
                            : current.filter((key) => key !== field.key))
                        }}
                      />
                      <span>{`角色 ${field.name + 1}`}</span>
                    </Space>
                  }
                  extra={<Button danger size="small" icon={<DeleteOutlined />} onClick={() => {
                    setSelectedCharacterKeys((current) => current.filter((key) => key !== field.key))
                    remove(field.name)
                  }} />}
                >
                  <Row gutter={12}>
                    <Col span={8}><Form.Item {...field} name={[field.name, 'name']} label="名称" rules={[{ required: true, message: '请输入名称' }]}><Input /></Form.Item></Col>
                    <Col span={8}><Form.Item {...field} name={[field.name, 'aliases']} label="别名"><Input /></Form.Item></Col>
                    <Col span={8}><Form.Item {...field} name={[field.name, 'confidence']} label="置信度"><InputNumber min={0} max={1} step={0.05} className="full-width" /></Form.Item></Col>
                    <Col span={12}><Form.Item {...field} name={[field.name, 'traits']} label="人物特征"><Input.TextArea rows={3} /></Form.Item></Col>
                    <Col span={12}><Form.Item {...field} name={[field.name, 'voice_profile']} label="音色描述"><Input.TextArea rows={3} /></Form.Item></Col>
                  </Row>
                </Card>
              ))}
            </Space>
          )}
        </Form.List>
      </Form>
      <Modal
        open={importOpen}
        title="导入人物池 JSON"
        okText="导入"
        cancelText="关闭"
        confirmLoading={importMutation.isPending}
        onCancel={() => setImportOpen(false)}
        onOk={() => importMutation.mutate(false)}
      >
        <Space direction="vertical" size={12} className="full-width">
          <Flex justify="space-between" align="center">
            <Text>与现有人物池合并</Text>
            <Switch checked={importMerge} onChange={setImportMerge} />
          </Flex>
          <Input.TextArea
            rows={10}
            className="mono textarea-editor"
            value={importText}
            onChange={(event) => setImportText(event.target.value)}
            placeholder='{"characters":[{"canonical":"角色名","aliases":[],"traits":"","voice_profile":""}]}'
          />
          <Button disabled={!importText.trim()} loading={importMutation.isPending} onClick={() => importMutation.mutate(true)}>
            预览
          </Button>
          {importPreview ? <pre className="json-preview">{JSON.stringify(importPreview, null, 2)}</pre> : null}
        </Space>
      </Modal>
    </Drawer>
  )
}
