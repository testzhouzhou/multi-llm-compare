import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

// 获取模型列表
export function getModels() {
  return api.get('/models')
}

// 对比聊天（非流式）
export function compareChat(question, models) {
  return api.post('/chat/compare', { question, models })
}

// 获取历史记录
export function getHistory(limit = 20) {
  return api.get('/history', { params: { limit } })
}

// 获取历史记录详情
export function getHistoryDetail(id) {
  return api.get(`/history/${id}`)
}

// 删除历史记录
export function deleteHistory(id) {
  return api.delete(`/history/${id}`)
}

// 添加自定义模型
export function addCustomModel(data) {
  return api.post('/models/custom', data)
}

// 删除自定义模型
export function deleteCustomModel(id) {
  return api.delete(`/models/custom/${id}`)
}

// 更新模型（覆盖层）
export function updateModel(modelId, data) {
  return api.put(`/models/${modelId}`, data)
}

// 重置覆盖层
export function resetModelOverride(modelId) {
  return api.delete(`/models/${modelId}/override`)
}

// 测试模型连通性
export function testModel(modelId) {
  return api.post(`/models/${modelId}/test`)
}

function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || 'download'
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function filenameFromDisposition(disposition, fallback) {
  if (!disposition) return fallback
  const m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(disposition)
  if (m) {
    try {
      return decodeURIComponent(m[1] || m[2])
    } catch {
      return m[1] || m[2] || fallback
    }
  }
  return fallback
}

// 导出当前对比结果
export async function exportCurrent(format, payload) {
  const res = await api.post(`/export/current`, payload, {
    params: { format },
    responseType: 'blob',
  })
  const filename = filenameFromDisposition(
    res.headers['content-disposition'],
    `mlc_export.${format}`
  )
  triggerBlobDownload(res.data, filename)
  return { filename, size: res.data.size }
}

// 导出单条历史
export async function exportHistory(id, format = 'md') {
  const res = await api.get(`/export/history/${id}`, {
    params: { format },
    responseType: 'blob',
  })
  const filename = filenameFromDisposition(
    res.headers['content-disposition'],
    `mlc_history_${id}.${format}`
  )
  triggerBlobDownload(res.data, filename)
  return { filename, size: res.data.size }
}

// 导出全部历史 CSV
export async function exportAllHistoryCsv() {
  const res = await api.get('/history/export', {
    params: { format: 'csv' },
    responseType: 'blob',
  })
  const filename = filenameFromDisposition(
    res.headers['content-disposition'],
    'mlc_history_all.csv'
  )
  triggerBlobDownload(res.data, filename)
  return { filename, size: res.data.size }
}
