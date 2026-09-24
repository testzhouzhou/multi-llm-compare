<template>
  <div id="app">
    <a-row :gutter="[16, 16]">
      <!-- 左侧：输入 + 模型选择 -->
      <a-col :span="6">
        <a-card title="多模型对比" :bordered="false" style="background: #fafafa">
          <a-form layout="vertical">
            <a-form-item label="请输入你的问题">
              <a-textarea
                v-model:value="question"
                :auto-size="{ minRows: 4, maxRows: 8 }"
                placeholder="输入你想对比的问题..."
                @pressEnter.ctrl="handleCompare"
              />
              <div style="color: #999; font-size: 12px; margin-top: 4px">
                Ctrl + Enter 快速发送
              </div>
            </a-form-item>

            <a-form-item label="选择模型">
              <a-checkbox-group v-model:value="selectedModels">
                <a-row :gutter="[8, 8]">
                  <a-col :span="24" v-for="m in models" :key="m.id">
                    <a-checkbox :value="m.id" :disabled="!m.enabled">
                      {{ m.name }}
                      <a-tag v-if="m.is_custom" color="orange" style="font-size: 10px">自定义</a-tag>
                    </a-checkbox>
                  </a-col>
                </a-row>
              </a-checkbox-group>
            </a-form-item>

            <a-space direction="vertical" style="width: 100%">
              <a-button
                type="primary"
                block
                size="large"
                :loading="comparing"
                :disabled="!question || selectedModels.length === 0"
                @click="handleCompare"
              >
                <template #icon><ThunderboltOutlined /></template>
                开始对比 ({{ selectedModels.length }}个模型)
              </a-button>

              <a-button block @click="openModelManager">
                <template #icon><SettingOutlined /></template>
                模型管理
              </a-button>

              <a-button block @click="openHistory">
                <template #icon><ClockCircleOutlined /></template>
                历史记录
              </a-button>
            </a-space>
          </a-form>
        </a-card>
      </a-col>

      <!-- 右侧：对比结果 -->
      <a-col :span="18">
        <!-- 导出（在差异汇总上方） -->
        <div v-if="diffSummary || results.length" style="margin-bottom: 12px; text-align: right">
          <a-space>
            <a-button v-if="diffSummary" @click="copyText(diffSummary, '差异汇总')">
              <template #icon><CopyOutlined /></template>
              复制差异汇总
            </a-button>
            <a-dropdown>
              <a-button>
                导出
                <DownOutlined />
              </a-button>
              <template #overlay>
                <a-menu @click="handleExportCurrent">
                  <a-menu-item key="md">Markdown</a-menu-item>
                  <a-menu-item key="json">JSON</a-menu-item>
                  <a-menu-item key="csv">CSV</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </a-space>
        </div>

        <!-- 差异汇总（结构化 / 旧 markdown 降级） -->
        <a-card
          v-if="diffSummary || hasStructuredDiff"
          title="差异汇总"
          :bordered="false"
          style="margin-bottom: 16px; background: #f0f5ff"
        >
          <template v-if="hasStructuredDiff">
            <div class="diff-overall" v-if="diffDetail.overall">
              <strong>一句话总评：</strong>{{ diffDetail.overall }}
            </div>
            <div class="diff-overall" v-if="diffDetail.judge" style="margin-top: 8px; color: #666">
              评委：{{ diffDetail.judge }}（匿名评卷，不看厂商名）
            </div>

            <div v-if="(diffDetail.dimensions || []).length" style="margin-top: 16px">
              <h4 style="margin-bottom: 8px">横向维度对比</h4>
              <table class="diff-dim-table">
                <thead>
                  <tr>
                    <th style="width: 140px">维度</th>
                    <th>对比说明</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(d, i) in diffDetail.dimensions" :key="i">
                    <td><strong>{{ d.dimension }}</strong></td>
                    <td>{{ d.detail }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div style="margin-top: 16px">
              <h4 style="margin-bottom: 8px">各模型要点</h4>
              <a-row :gutter="[12, 12]">
                <a-col
                  :xs="24"
                  :sm="24"
                  :md="12"
                  v-for="(m, i) in diffDetail.models"
                  :key="m.name + i"
                >
                  <a-card size="small" :bordered="true" :class="['model-diff-card', modelCardClass(i)]">
                    <template #title>
                      <span>{{ modelCardIcon(i) }} {{ m.name }}</span>
                    </template>
                    <div class="model-diff-block"><b>核心观点</b><p>{{ m.core || '—' }}</p></div>
                    <div class="model-diff-block"><b>独有点</b><p>{{ m.unique || '—' }}</p></div>
                    <div class="model-diff-block"><b>与其他差异</b><p>{{ m.diff_vs_others || '—' }}</p></div>
                    <div class="model-diff-block"><b>优势</b><p>{{ m.pros || '—' }}</p></div>
                    <div class="model-diff-block"><b>短板</b><p>{{ m.cons || '—' }}</p></div>
                    <div class="model-diff-block"><b>适合场景</b><p>{{ m.best_for || '—' }}</p></div>
                  </a-card>
                </a-col>
              </a-row>
            </div>

            <div v-if="diffDetail.verdict" style="margin-top: 16px" class="diff-verdict">
              <h4>总结推荐</h4>
              <p>{{ diffDetail.verdict }}</p>
            </div>

            <a-alert
              v-if="diffDetail.parse_error"
              type="warning"
              show-icon
              message="结构化解析未成功，已降级显示原文"
              style="margin-top: 12px"
            />
          </template>
          <div v-else v-html="renderedDiff" class="diff-content"></div>
        </a-card>

        <!-- 模型结果卡片 -->
        <a-row :gutter="[16, 16]">
          <a-col
            :span="resultCols"
            v-for="result in results"
            :key="result.model_id"
          >
            <a-card :bordered="false" :class="{'result-card': true, 'has-error': result.error}">
              <template #title>
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 8px">
                  <span>
                    <CheckCircleOutlined v-if="!result.error && result.content" style="color: #52c41a" />
                    <LoadingOutlined v-if="result.loading" style="color: #1890ff" />
                    <CloseCircleOutlined v-if="result.error" style="color: #ff4d4f" />
                    {{ result.model }}
                  </span>
                  <span style="display: flex; align-items: center; gap: 8px; font-size: 12px; color: #999">
                    <template v-if="result.latency">{{ result.latency }}s</template>
                    <template v-if="result.tokens">{{ result.tokens }} tokens</template>
                    <a-button
                      v-if="result.content"
                      type="link"
                      size="small"
                      @click="copyText(result.content, result.model)"
                    >
                      <template #icon><CopyOutlined /></template>
                      复制
                    </a-button>
                  </span>
                </div>
              </template>

              <div v-if="result.loading && !result.content" class="loading-content">
                <a-spin />
                <div style="margin-top: 8px; color: #999">等待响应...</div>
              </div>

              <a-alert v-else-if="result.error" :message="result.error" type="error" show-icon />

              <div v-else-if="result.content" class="result-content" v-html="renderMarkdown(result.content)"></div>

              <a-empty v-else description="等待发送" :image="Empty.PRESENTED_IMAGE_SIMPLE" />
            </a-card>
          </a-col>
        </a-row>

        <a-empty
          v-if="!comparing && results.length === 0 && !diffSummary"
          description="选择模型，输入问题，开始对比"
          style="margin-top: 100px"
        />
      </a-col>
    </a-row>

    <!-- 模型管理 Drawer -->
    <a-drawer
      v-model:open="showModelManager"
      title="模型管理"
      width="720"
      placement="right"
    >
      <a-form layout="vertical">
        <a-form-item label="模型名称">
          <a-input v-model:value="newModel.name" placeholder="如：GPT-4o" />
        </a-form-item>
        <a-form-item label="API Base URL">
          <a-input v-model:value="newModel.base_url" placeholder="https://api.example.com/v1" />
        </a-form-item>
        <a-form-item label="模型名称（API 中的 model 参数）">
          <a-input v-model:value="newModel.model" placeholder="gpt-4o" />
        </a-form-item>
        <a-form-item label="API Key">
          <a-input-password v-model:value="newModel.api_key" placeholder="sk-..." />
        </a-form-item>
        <a-button type="primary" block @click="handleAddModel" :loading="addingModel">
          添加自定义模型
        </a-button>
      </a-form>

      <a-divider />

      <h4>全部模型</h4>
      <a-list :data-source="models" size="small" :locale="{ emptyText: '暂无模型' }">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-list-item-meta>
              <template #title>
                {{ item.name }}
                <a-tag v-if="item.is_custom" color="orange" style="margin-left: 6px">自定义</a-tag>
                <a-tag v-if="item.overridden" color="blue" style="margin-left: 6px">已覆盖</a-tag>
                <a-tag v-if="!item.enabled" style="margin-left: 6px">已禁用</a-tag>
              </template>
              <template #description>
                <div>{{ item.model }} · {{ item.base_url }}</div>
                <div style="margin-top: 4px">
                  Key：
                  <template v-if="item.has_key">
                    {{ item.key_masked }}
                    <span style="color: #999">
                      （{{ keySourceLabel(item.key_source) }}）
                    </span>
                  </template>
                  <template v-else>未配置</template>
                </div>
                <div v-if="testResults[item.id]" style="margin-top: 6px">
                  <a-tag :color="testResults[item.id].ok ? 'success' : 'error'">
                    {{ testResults[item.id].ok ? '通过' : '失败' }}
                    {{ testResults[item.id].status || '-' }}
                    · {{ testResults[item.id].latency }}s
                  </a-tag>
                  <a-alert
                    v-if="!testResults[item.id].ok && testResults[item.id].error"
                    type="error"
                    :message="testResults[item.id].error"
                    show-icon
                    style="margin-top: 6px"
                  />
                </div>
              </template>
            </a-list-item-meta>
            <template #actions>
              <a-button type="link" size="small" @click="openEditModel(item)">编辑</a-button>
              <a-button type="link" size="small" :loading="testingId === item.id" @click="handleTestModel(item.id)">测试</a-button>
              <a-button
                v-if="item.overridden"
                type="link"
                size="small"
                @click="handleResetOverride(item.id)"
              >重置</a-button>
              <a-button
                v-if="item.is_custom"
                type="link"
                danger
                size="small"
                @click="handleDeleteModel(item.id)"
              >删除</a-button>
            </template>
          </a-list-item>
        </template>
      </a-list>
    </a-drawer>

    <!-- 编辑模型 Modal -->
    <a-modal
      v-model:open="showEditModal"
      title="编辑模型"
      ok-text="保存"
      :confirm-loading="savingModel"
      @ok="handleSaveModel"
      destroy-on-close
    >
      <a-form layout="vertical" v-if="editForm">
        <a-form-item label="名称">
          <a-input v-model:value="editForm.name" />
        </a-form-item>
        <a-form-item label="Base URL">
          <a-input v-model:value="editForm.base_url" />
        </a-form-item>
        <a-form-item label="Model">
          <a-input v-model:value="editForm.model" />
        </a-form-item>
        <a-form-item label="API Key">
          <a-input-password
            v-model:value="editForm.api_key"
            :placeholder="editForm.key_placeholder || '留空则保留原 Key'"
          />
        </a-form-item>
        <a-form-item label="协议">
          <a-select v-model:value="editForm.protocol" style="width: 100%">
            <a-select-option value="openai">openai</a-select-option>
            <a-select-option value="anthropic">anthropic</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item label="启用">
          <a-switch v-model:checked="editForm.enabled" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 历史记录 Drawer -->
    <a-drawer
      v-model:open="showHistory"
      title="历史记录"
      width="600"
      placement="right"
    >
      <div style="margin-bottom: 12px">
        <a-button @click="handleExportAllHistory" :loading="exportingHistory">
          导出全部记录 CSV
        </a-button>
      </div>
      <a-list
        :data-source="historyList"
        :loading="loadingHistory"
        :locale="{ emptyText: '暂无历史记录' }"
      >
        <template #renderItem="{ item }">
          <a-list-item>
            <a-list-item-meta>
              <template #title>
                <a @click="handleViewHistory(item.id)">{{ item.question }}</a>
              </template>
              <template #description>
                {{ item.created_at }} · 模型: {{ item.models.join(', ') }}
              </template>
            </a-list-item-meta>
            <template #actions>
              <a-button type="link" danger size="small" @click="handleDeleteHistory(item.id)">删除</a-button>
            </template>
          </a-list-item>
        </template>
      </a-list>
    </a-drawer>

    <!-- 历史记录详情 Modal -->
    <a-modal
      v-model:open="showHistoryDetail"
      title="历史对比详情"
      width="90%"
      :footer="null"
      :destroyOnClose="true"
    >
      <div v-if="historyDetail">
        <div style="margin-bottom: 12px; text-align: right">
          <a-space>
            <a-button @click="copyText(historyDetail.diff_summary || '', '历史差异汇总')">
              <template #icon><CopyOutlined /></template>
              复制差异汇总
            </a-button>
            <a-button type="primary" @click="handleExportHistoryMd">导出 Markdown</a-button>
          </a-space>
        </div>
        <a-descriptions :column="1" bordered size="small">
          <a-descriptions-item label="问题">{{ historyDetail.question }}</a-descriptions-item>
          <a-descriptions-item label="对比时间">{{ historyDetail.created_at }}</a-descriptions-item>
        </a-descriptions>

        <a-divider>差异汇总</a-divider>
        <template v-if="historyHasStructuredDiff">
          <div class="diff-overall" v-if="historyDetail.diff_detail.overall">
            <strong>一句话总评：</strong>{{ historyDetail.diff_detail.overall }}
          </div>
          <div class="diff-overall" v-if="historyDetail.diff_detail.judge" style="margin-top: 8px; color: #666">
            评委：{{ historyDetail.diff_detail.judge }}（匿名评卷，不看厂商名）
          </div>
          <div v-if="(historyDetail.diff_detail.dimensions || []).length" style="margin-top: 12px">
            <table class="diff-dim-table">
              <thead><tr><th>维度</th><th>对比说明</th></tr></thead>
              <tbody>
                <tr v-for="(d, i) in historyDetail.diff_detail.dimensions" :key="'hd'+i">
                  <td><strong>{{ d.dimension }}</strong></td>
                  <td>{{ d.detail }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <a-row :gutter="[12, 12]" style="margin-top: 12px">
            <a-col :xs="24" :md="12" v-for="(m, i) in historyDetail.diff_detail.models" :key="'hm'+i">
              <a-card size="small" :class="['model-diff-card', modelCardClass(i)]">
                <template #title>{{ modelCardIcon(i) }} {{ m.name }}</template>
                <div class="model-diff-block"><b>核心观点</b><p>{{ m.core || '—' }}</p></div>
                <div class="model-diff-block"><b>独有点</b><p>{{ m.unique || '—' }}</p></div>
                <div class="model-diff-block"><b>与其他差异</b><p>{{ m.diff_vs_others || '—' }}</p></div>
                <div class="model-diff-block"><b>优势</b><p>{{ m.pros || '—' }}</p></div>
                <div class="model-diff-block"><b>短板</b><p>{{ m.cons || '—' }}</p></div>
                <div class="model-diff-block"><b>适合场景</b><p>{{ m.best_for || '—' }}</p></div>
              </a-card>
            </a-col>
          </a-row>
          <div v-if="historyDetail.diff_detail.verdict" class="diff-verdict" style="margin-top: 12px">
            <h4>总结推荐</h4>
            <p>{{ historyDetail.diff_detail.verdict }}</p>
          </div>
        </template>
        <div v-else v-html="renderedHistoryDiff" class="diff-content"></div>

        <a-divider>各模型回复</a-divider>
        <a-tabs>
          <a-tab-pane
            v-for="r in historyDetail.results"
            :key="r.model_id"
            :tab="r.model"
          >
            <div v-if="r.error" class="error-text">{{ r.error }}</div>
            <div v-else v-html="renderMarkdown(r.content)" class="result-content"></div>
          </a-tab-pane>
        </a-tabs>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { marked } from 'marked'
import { Empty } from 'ant-design-vue'
import {
  ThunderboltOutlined, SettingOutlined, ClockCircleOutlined,
  CheckCircleOutlined, LoadingOutlined, CloseCircleOutlined, DownOutlined,
  CopyOutlined
} from '@ant-design/icons-vue'
import {
  getModels, compareChat, getHistory, getHistoryDetail,
  deleteHistory, addCustomModel, deleteCustomModel,
  updateModel, resetModelOverride, testModel,
  exportCurrent, exportHistory, exportAllHistoryCsv
} from './api/index.js'

const question = ref('')
const models = ref([])
const selectedModels = ref([])
const results = ref([])
const diffSummary = ref('')
const diffDetail = ref(null)
const comparing = ref(false)

const showModelManager = ref(false)
const showHistory = ref(false)
const showHistoryDetail = ref(false)
const historyList = ref([])
const historyDetail = ref(null)
const loadingHistory = ref(false)
const exportingHistory = ref(false)

const newModel = ref({ name: '', base_url: '', model: '', api_key: '' })
const addingModel = ref(false)

const showEditModal = ref(false)
const editForm = ref(null)
const savingModel = ref(false)
const testingId = ref(null)
const testResults = ref({})

const resultCols = computed(() => {
  const count = results.value.length || 1
  if (count <= 2) return 12
  if (count <= 3) return 8
  return 6
})

const renderedDiff = computed(() => marked(stripEmoji(diffSummary.value || '')))

const hasStructuredDiff = computed(() => {
  const d = diffDetail.value
  if (!d || typeof d !== 'object') return false
  if (d.parse_error) return false
  return Array.isArray(d.models) && d.models.length > 0
})

const historyHasStructuredDiff = computed(() => {
  const d = historyDetail.value?.diff_detail
  if (!d || typeof d !== 'object') return false
  if (d.parse_error) return false
  return Array.isArray(d.models) && d.models.length > 0
})

const renderedHistoryDiff = computed(() => {
  if (!historyDetail.value) return ''
  return marked(stripEmoji(historyDetail.value.diff_summary || ''))
})

function stripEmoji(text) {
  if (text == null) return ''
  return String(text)
    .replace(/\p{Extended_Pictographic}/gu, '')
    .replace(/\uFE0F/g, '')
    .replace(/[（(]\s*[）)]/g, '')
    .replace(/[ \t]{2,}/g, ' ')
}

function stripEmojiDeep(obj) {
  if (typeof obj === 'string') return stripEmoji(obj)
  if (Array.isArray(obj)) return obj.map(stripEmojiDeep)
  if (obj && typeof obj === 'object') {
    const out = {}
    for (const k of Object.keys(obj)) out[k] = stripEmojiDeep(obj[k])
    return out
  }
  return obj
}

function renderMarkdown(text) {
  return marked(stripEmoji(text || ''))
}

function modelCardClass(i) {
  const classes = ['tone-a', 'tone-b', 'tone-c', 'tone-d']
  return classes[i % classes.length]
}

function modelCardIcon(i) {
  return ['①', '②', '③', '④', '⑤', '⑥', '⑦'][i] || '·'
}

async function copyText(text, label = '') {
  const value = stripEmoji(text == null ? '' : String(text))
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value)
    } else {
      const ta = document.createElement('textarea')
      ta.value = value
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      if (!ok) throw new Error('execCommand copy failed')
    }
    message.success('已复制')
  } catch (e) {
    console.warn('copy failed', label, e)
    message.error('复制失败')
  }
}

function keySourceLabel(source) {
  if (source === 'db') return '来自数据库'
  if (source === 'env') return '来自 .env'
  return '未配置'
}

async function loadModels() {
  try {
    const res = await getModels()
    if (res.data.code === 0) {
      models.value = res.data.data
      const enabledIds = models.value.filter(m => m.enabled).map(m => m.id)
      selectedModels.value = selectedModels.value.length
        ? selectedModels.value.filter(id => enabledIds.includes(id))
        : enabledIds
      if (!selectedModels.value.length) {
        selectedModels.value = enabledIds
      }
    }
  } catch (e) {
    message.error('加载模型列表失败')
  }
}

function openModelManager() {
  showModelManager.value = true
  loadModels()
}

function openHistory() {
  showHistory.value = true
  loadHistory()
}

async function handleCompare() {
  if (!question.value || selectedModels.value.length === 0) return

  comparing.value = true
  results.value = selectedModels.value.map(id => {
    const m = models.value.find(m => m.id === id)
    return {
      model_id: id,
      model: m ? m.name : id,
      content: '',
      tokens: 0,
      latency: 0,
      error: null,
      loading: true,
    }
  })
  diffSummary.value = ''
  diffDetail.value = null

  try {
    const res = await compareChat(question.value, selectedModels.value)
    if (res.data.code === 0) {
      const apiResults = stripEmojiDeep(res.data.data.results)
      results.value = results.value.map(r => {
        const apiResult = apiResults.find(a => a.model_id === r.model_id)
        if (apiResult) {
          return {
            ...r,
            content: apiResult.content || '',
            tokens: apiResult.tokens || 0,
            latency: apiResult.latency || 0,
            error: apiResult.error || null,
            loading: false,
          }
        }
        return { ...r, loading: false }
      })
      diffSummary.value = stripEmoji(res.data.data.diff_summary || '')
      diffDetail.value = stripEmojiDeep(res.data.data.diff_detail || null)
      message.success('对比完成！')
    }
  } catch (e) {
    message.error('对比失败: ' + (e.response?.data?.detail || e.message))
    results.value = results.value.map(r => ({ ...r, loading: false, error: '请求失败' }))
  } finally {
    comparing.value = false
  }
}

async function handleAddModel() {
  if (!newModel.value.name || !newModel.value.base_url || !newModel.value.model || !newModel.value.api_key) {
    message.warning('请填写完整信息')
    return
  }
  addingModel.value = true
  try {
    const res = await addCustomModel(newModel.value)
    if (res.data.code === 0) {
      message.success('模型添加成功')
      newModel.value = { name: '', base_url: '', model: '', api_key: '' }
      await loadModels()
    }
  } catch (e) {
    message.error('添加失败')
  } finally {
    addingModel.value = false
  }
}

async function handleDeleteModel(id) {
  try {
    await deleteCustomModel(id)
    message.success('已删除')
    await loadModels()
  } catch (e) {
    message.error('删除失败')
  }
}

function openEditModel(item) {
  editForm.value = {
    id: item.id,
    name: item.name,
    base_url: item.base_url,
    model: item.model,
    api_key: '',
    key_placeholder: item.key_masked ? `${item.key_masked}（留空保留）` : '留空则保留原 Key',
    protocol: item.protocol || 'openai',
    enabled: !!item.enabled,
  }
  showEditModal.value = true
}

async function handleSaveModel() {
  if (!editForm.value) return
  savingModel.value = true
  try {
    const body = {
      name: editForm.value.name,
      base_url: editForm.value.base_url,
      model: editForm.value.model,
      protocol: editForm.value.protocol,
      enabled: editForm.value.enabled,
      api_key: editForm.value.api_key ? editForm.value.api_key : '__KEEP__',
    }
    const res = await updateModel(editForm.value.id, body)
    if (res.data.code === 0) {
      message.success('已保存')
      showEditModal.value = false
      await loadModels()
    }
  } catch (e) {
    message.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    savingModel.value = false
  }
}

async function handleTestModel(id) {
  testingId.value = id
  try {
    const res = await testModel(id)
    if (res.data.code === 0) {
      testResults.value = { ...testResults.value, [id]: res.data.data }
      if (res.data.data.ok) {
        message.success(`测试通过 HTTP ${res.data.data.status}`)
      } else {
        message.error('测试失败')
      }
    }
  } catch (e) {
    testResults.value = {
      ...testResults.value,
      [id]: { ok: false, status: 0, latency: 0, error: e.message, content: '' },
    }
    message.error('测试请求失败')
  } finally {
    testingId.value = null
  }
}

async function handleResetOverride(id) {
  try {
    await resetModelOverride(id)
    message.success('已重置覆盖')
    delete testResults.value[id]
    await loadModels()
  } catch (e) {
    message.error('重置失败')
  }
}

async function handleExportCurrent({ key }) {
  try {
    const info = await exportCurrent(key, {
      question: question.value,
      results: results.value.map(r => ({
        model: r.model,
        model_id: r.model_id,
        content: r.content,
        tokens: r.tokens,
        latency: r.latency,
        error: r.error,
      })),
      diff_summary: diffSummary.value,
      diff_detail: diffDetail.value,
    })
    message.success(`已下载 ${info.filename}`)
  } catch (e) {
    message.error('导出失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function handleExportAllHistory() {
  exportingHistory.value = true
  try {
    const info = await exportAllHistoryCsv()
    message.success(`已下载 ${info.filename}`)
  } catch (e) {
    message.error('导出失败')
  } finally {
    exportingHistory.value = false
  }
}

async function handleExportHistoryMd() {
  if (!historyDetail.value) return
  try {
    const info = await exportHistory(historyDetail.value.id, 'md')
    message.success(`已下载 ${info.filename}`)
  } catch (e) {
    message.error('导出失败')
  }
}

async function loadHistory() {
  loadingHistory.value = true
  try {
    const res = await getHistory()
    if (res.data.code === 0) {
      historyList.value = res.data.data
    }
  } catch (e) {
    message.error('加载历史失败')
  } finally {
    loadingHistory.value = false
  }
}

async function handleViewHistory(id) {
  try {
    const res = await getHistoryDetail(id)
    if (res.data.code === 0) {
      historyDetail.value = stripEmojiDeep(res.data.data)
      showHistoryDetail.value = true
    }
  } catch (e) {
    message.error('加载详情失败')
  }
}

async function handleDeleteHistory(id) {
  try {
    await deleteHistory(id)
    message.success('已删除')
    await loadHistory()
  } catch (e) {
    message.error('删除失败')
  }
}

onMounted(() => {
  loadModels()
  // 供自动化打开指定历史（不影响正常 UI）
  window.__mlcViewHistory = (id) => handleViewHistory(id)
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

#app {
  padding: 16px;
  min-height: 100vh;
  background: #f5f5f5;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB',
    'Microsoft YaHei', sans-serif;
}

.result-card {
  transition: all 0.3s;
}

.result-card.has-error {
  border: 1px solid #ff4d4f !important;
}

.loading-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 0;
}

.result-content {
  line-height: 1.8;
  font-size: 14px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 600px;
  overflow-y: auto;
}

.result-content :deep(h1),
.result-content :deep(h2),
.result-content :deep(h3),
.result-content :deep(h4),
.result-content :deep(h5),
.result-content :deep(h6) {
  margin: 12px 0 8px;
}

.result-content :deep(code) {
  background: #f5f5f5;
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 13px;
}

.result-content :deep(pre) {
  background: #282c34;
  color: #abb2bf;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.result-content :deep(pre code) {
  background: none;
  padding: 0;
  color: inherit;
}

.result-content :deep(blockquote) {
  border-left: 3px solid #1890ff;
  padding-left: 12px;
  margin: 8px 0;
  color: #666;
}

.result-content :deep(ul),
.result-content :deep(ol) {
  padding-left: 20px;
  margin: 8px 0;
}

.result-content :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
}

.result-content :deep(th),
.result-content :deep(td) {
  border: 1px solid #ddd;
  padding: 8px;
  text-align: left;
}

.result-content :deep(th) {
  background: #f5f5f5;
}

.diff-content {
  line-height: 1.8;
  font-size: 14px;
}

.diff-content :deep(ul),
.diff-content :deep(ol) {
  padding-left: 20px;
}

.diff-content :deep(li) {
  margin: 4px 0;
}

.error-text {
  color: #ff4d4f;
  padding: 12px;
  background: #fff2f0;
  border-radius: 4px;
}

.diff-overall {
  font-size: 15px;
  line-height: 1.7;
  padding: 8px 12px;
  background: #fff;
  border-left: 3px solid #1677ff;
}

.diff-dim-table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  font-size: 13px;
}

.diff-dim-table th,
.diff-dim-table td {
  border: 1px solid #d9d9d9;
  padding: 8px 10px;
  vertical-align: top;
  text-align: left;
}

.diff-dim-table th {
  background: #fafafa;
}

.model-diff-card p {
  margin: 4px 0 10px;
  line-height: 1.6;
  color: #333;
  white-space: pre-wrap;
}

.model-diff-block b {
  color: #555;
  font-size: 12px;
}

.model-diff-card.tone-a {
  border-top: 3px solid #1677ff;
}
.model-diff-card.tone-b {
  border-top: 3px solid #52c41a;
}
.model-diff-card.tone-c {
  border-top: 3px solid #fa8c16;
}
.model-diff-card.tone-d {
  border-top: 3px solid #722ed1;
}

.diff-verdict {
  background: #fff;
  padding: 12px;
  border-radius: 4px;
  line-height: 1.7;
}
</style>
