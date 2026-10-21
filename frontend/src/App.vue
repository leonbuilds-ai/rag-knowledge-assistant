<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ArrowDownToLine, ArrowRight, ArrowUp, BookOpen, Check, CheckCheck, ChevronDown, ChevronRight, CircleHelp, Clock3, Database, FileCheck2, FileSearch, FileText, FlaskConical, FolderOpen, Layers3, LoaderCircle, LogOut, Menu, MessageSquare, Plus, RefreshCw, Search, Settings2, ShieldCheck, Sparkles, Trash2, UploadCloud, X, Zap } from 'lucide-vue-next'
import { api, ApiError, API_BASE, downloadDocument, streamChat } from './api'
import GovernancePanel from './components/GovernancePanel.vue'
import DocumentVersions from './components/DocumentVersions.vue'
import { submitUploads, type UploadItem } from './uploadQueue'
import type { ChatResponse, Chunk, Citation, Comparison, Conversation, Document, Evaluation, EvaluationCase, Job, KnowledgeBase, Message, Runtime, User } from './api'

type View = 'library' | 'chat' | 'evaluations' | 'governance' | 'system'
const user = ref<User | null>(null)
const restoring = ref(true)
const authMode = ref<'login' | 'register'>('login')
const auth = ref({ email: '', password: '', name: '' })
const authenticating = ref(false)
const authError = ref('')
const view = ref<View>('library')
const mobileNav = ref(false)
const error = ref('')
const notice = ref('')
const busy = ref(false)
const runtime = ref<Runtime | null>(null)
const health = ref<'checking' | 'online' | 'degraded' | 'offline'>('checking')
function connectionText() { return health.value === 'online' ? '服务已连接' : health.value === 'checking' ? '正在检测服务' : health.value === 'degraded' ? '部分服务待恢复' : '服务未连接' }
const knowledgeBases = ref<KnowledgeBase[]>([])
const documents = ref<Document[]>([])
const selectedKbId = ref('')
const documentQuery = ref('')
const showKbModal = ref(false)
const organizations = ref<{ id: string; name: string; role: string }[]>([])
const kbForm = ref({ name: '', description: '', organization_id: '', visibility: 'organization' })
const versionDocument = ref<Document | null>(null)
const oidcEnabled = ref(false)
const chatDraft = ref('')
let chatController: AbortController | null = null
let lastAttempt: { key: string; body: string } | null = null
function canWrite(doc: Document) { return knowledgeBases.value.find(kb => kb.id === doc.knowledge_base_id)?.role !== 'reader' }
const creatingKb = ref(false)
const uploading = ref(false)
const uploadItems = ref<UploadItem[]>([])
let uploadController: AbortController | null = null
const uploadLabels = { waiting: '待提交', uploading: '正在提交', success: '已提交', duplicate: '已存在', failed: '提交失败' }
const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
const jobs = ref<Record<string, Job>>({})
const preview = ref<{ document: Document; chunks: Chunk[] } | null>(null)
const previewLoading = ref(false)
const previewingId = ref('')
const deleting = ref('')
const pendingDelete = ref<Document | null>(null)
const selectedKb = computed(() => knowledgeBases.value.find(kb => kb.id === selectedKbId.value))
const selectedDocuments = computed(() => documents.value.filter(doc => !selectedKbId.value || doc.knowledge_base_id === selectedKbId.value))
const filteredDocuments = computed(() => selectedDocuments.value.filter(doc => doc.name.toLowerCase().includes(documentQuery.value.toLowerCase())))
const readyDocuments = computed(() => documents.value.filter(doc => doc.active_revision_id && !isExpired(doc)).length)
function isExpired(doc: Document) { return !!doc.expires_at && new Date(doc.expires_at.endsWith('Z') ? doc.expires_at : doc.expires_at + 'Z').getTime() <= Date.now() }
function documentStatus(doc: Document) { return isExpired(doc) ? '已过期' : doc.active_revision_id && ['queued', 'processing'].includes(doc.status) ? '更新中 · 旧版可用' : statusText(doc.status) }
const chunkCount = computed(() => documents.value.reduce((total, doc) => total + (doc.chunk_count || 0), 0))
const totalSize = computed(() => documents.value.reduce((total, doc) => total + (doc.size_bytes || 0), 0))
const navItems: { id: View; label: string; en: string; icon: typeof BookOpen }[] = [
  { id: 'library', label: '知识空间', en: 'Knowledge', icon: BookOpen },
  { id: 'chat', label: '智能问答', en: 'Assistant', icon: MessageSquare },
  { id: 'evaluations', label: '评测中心', en: 'Evaluation', icon: FlaskConical },
  { id: 'governance', label: '团队与治理', en: 'Governance', icon: ShieldCheck },
]
const titles = { library: '知识空间', chat: '智能问答', evaluations: '评测中心', system: '系统与模型', governance: '团队与治理' }

const conversations = ref<Conversation[]>([])
const conversationId = ref('')
const messages = ref<Message[]>([])
const question = ref('')
const chatMode = ref<'standard' | 'agent'>('agent')
const chatKbIds = ref<string[]>([])
const chatLoading = ref(false)
const historyLoading = ref(false)
const topK = ref(5)
const focusedMessageId = ref('')
const focusedCitationId = ref('')
const traceOpen = ref(false)
const evidenceOpen = ref(false)
const lastResponse = ref<ChatResponse | null>(null)
const messageList = ref<HTMLElement | null>(null)
const focusMessage = computed(() => messages.value.find(msg => msg.id === focusedMessageId.value) || [...messages.value].reverse().find(msg => msg.role === 'assistant'))
const evidence = computed(() => focusMessage.value?.citations || [])
const trace = computed(() => focusMessage.value?.trace || [])

const evaluations = ref<Evaluation[]>([])
const evaluation = ref<Evaluation | null>(null)
const evaluationName = ref('')
const evaluationCases = ref<EvaluationCase[]>([{ question: '', reference: '', expected_document_ids: [] }])
const evaluationKbIds = ref<string[]>([])
const useRagas = ref(false)
const evaluating = ref(false)
const comparing = ref(false)
const comparison = ref<Comparison | null>(null)
const evaluationLoading = ref(false)
const metricNames: Record<string, string> = { hit_rate: '命中率', recall: '召回率', precision: '精确率', mrr: 'MRR', mean_reciprocal_rank: 'MRR', context_precision: '上下文精确率', context_recall: '上下文召回率', answer_relevancy: '答案相关性', faithfulness: '忠实度', answer_similarity: '答案相似度', citation_coverage: '引用覆盖率', avg_latency_ms: '平均延迟 (ms)', latency_ms: '延迟 (ms)', lexical_overlap: '词项重合度', reference_overlap: '参考答案重合度', case_count: '测试用例', count: '数量', retrieval_hit_rate: '检索命中率', hit_at_5: 'Hit@5 命中率', recall_at_5: 'Recall@5 召回率', mrr_at_5: 'MRR@5', labeled_cases: '已标注用例', total_cases: '总用例', reference_token_recall: '参考词项召回率', ragas_enabled: 'Ragas 评测', reciprocal_rank: '倒数排名', question: '问题', reference: '参考答案', answer: '生成答案', contexts: '检索上下文', citations: '来源引用', expected_document_ids: '期望来源 ID', retrieved_document_ids: '检索来源 ID', provider: '回答模型', cached: '缓存命中' }
let pollTimer: ReturnType<typeof setInterval> | undefined
let healthTimer: ReturnType<typeof setInterval> | undefined
let checkingHealth = false
let pollRunning = false
let noticeTimer: ReturnType<typeof setTimeout> | undefined

function notify(message: string) {
  notice.value = message
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => { notice.value = '' }, 4500)
}
function fail(reason: unknown) {
  if (reason instanceof ApiError && reason.status === 0) health.value = 'offline'
  if (reason instanceof ApiError && reason.status === 401) { logout(); authError.value = '登录已过期，请重新登录。'; return }
  error.value = reason instanceof Error ? reason.message : '操作失败，请重试。'
}
function display(value: unknown): string {
  if (value === undefined || value === null) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}
function metric(value: unknown) {
  if (typeof value === 'boolean') return value ? '已启用' : '未启用'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3)
  return display(value)
}
function comparisonMetrics(result: Record<string, unknown>): Record<string, unknown> {
  return result.metrics && typeof result.metrics === 'object' && !Array.isArray(result.metrics) ? result.metrics as Record<string, unknown> : result
}
function date(value?: string) {
  if (!value) return '—'
  const parsed = new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : value + 'Z')
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function bytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}
function statusText(status: string) {
  return ({ ready: '已就绪', completed: '已完成', queued: '排队中', pending: '排队中', processing: '处理中', cancelled: '已取消', running: '运行中', parsing: '解析中', indexing: '索引中', failed: '处理失败' } as Record<string, string>)[status] || status
}
function progress(doc: Document) {
  const value = doc.job_id ? jobs.value[doc.job_id]?.progress || 0 : 0
  return Math.max(0, Math.min(100, value > 0 && value <= 1 ? value * 100 : value))
}
function go(next: View) { view.value = next; mobileNav.value = false; error.value = '' }
function toggleId(ids: string[], id: string) { const index = ids.indexOf(id); index >= 0 ? ids.splice(index, 1) : ids.push(id) }
function selectKb(id: string) { selectedKbId.value = id; documentQuery.value = '' }

async function checkHealth() {
  if (checkingHealth) return
  checkingHealth = true
  try { await api('/ready'); health.value = 'online' }
  catch (reason) { health.value = reason instanceof ApiError && reason.status === 503 ? 'degraded' : 'offline' }
  finally { checkingHealth = false }
}
async function loadWorkspace() {
  busy.value = true
  organizations.value = await api<{ id: string; name: string; role: string }[]>('/organizations').catch(() => [])
  const results = await Promise.allSettled([
    api<KnowledgeBase[]>('/knowledge-bases'), api<Document[]>('/documents'), api<Runtime>('/system'),
    api<Conversation[]>('/conversations'), api<Evaluation[]>('/evaluations'),
  ])
  const [kbResult, docResult, runtimeResult, conversationResult, evaluationResult] = results
  if (kbResult.status === 'fulfilled') { knowledgeBases.value = kbResult.value; if (!kbResult.value.some(kb => kb.id === selectedKbId.value)) selectedKbId.value = kbResult.value[0]?.id || '' } else fail(kbResult.reason)
  if (docResult.status === 'fulfilled') documents.value = docResult.value; else fail(docResult.reason)
  if (runtimeResult.status === 'fulfilled') runtime.value = runtimeResult.value; else fail(runtimeResult.reason)
  if (conversationResult.status === 'fulfilled') conversations.value = conversationResult.value; else fail(conversationResult.reason)
  if (evaluationResult.status === 'fulfilled') evaluations.value = evaluationResult.value; else fail(evaluationResult.reason)
  busy.value = false
}
async function signIn() {
  authError.value = ''; authenticating.value = true
  try {
    const payload = authMode.value === 'register' ? auth.value : { email: auth.value.email, password: auth.value.password }
    const response = await api<{ access_token: string; user: User }>(`/auth/${authMode.value}`, { method: 'POST', body: JSON.stringify(payload) })
    localStorage.removeItem('zhiheng_token'); user.value = response.user
    await loadWorkspace(); await checkHealth()
  } catch (reason) { authError.value = reason instanceof Error ? reason.message : '登录失败' }
  finally { authenticating.value = false }
}
function logout() {
  void api('/auth/logout', { method: 'POST' }).catch(() => {})
  chatController?.abort()
  localStorage.removeItem('zhiheng_token'); pendingDelete.value = null; user.value = null; documents.value = []; knowledgeBases.value = []
  runtime.value = null; conversations.value = []; messages.value = []; evaluations.value = []; evaluation.value = null
  conversationId.value = ''; selectedKbId.value = ''; error.value = ''; auth.value.password = ''
}
async function createKb() {
  if (!kbForm.value.name.trim()) return
  creatingKb.value = true
  try {
    const kb = await api<KnowledgeBase>('/knowledge-bases', { method: 'POST', body: JSON.stringify({ ...kbForm.value, organization_id: kbForm.value.organization_id || null }) })
    knowledgeBases.value.push(kb); selectedKbId.value = kb.id; kbForm.value = { name: '', description: '', organization_id: '', visibility: 'organization' }; showKbModal.value = false
    notify('知识库已创建，可以开始上传文档。')
  } catch (reason) { fail(reason) } finally { creatingKb.value = false }
}
async function removeKb() {
  if (!selectedKb.value || !window.confirm(`删除“${selectedKb.value.name}”及其全部文档和索引？此操作无法撤销。`)) return
  deleting.value = selectedKb.value.id
  try {
    await api(`/knowledge-bases/${selectedKb.value.id}`, { method: 'DELETE' })
    chatKbIds.value = chatKbIds.value.filter(id => id !== selectedKbId.value)
    evaluationKbIds.value = evaluationKbIds.value.filter(id => id !== selectedKbId.value)
    await loadWorkspace(); notify('知识库已删除。')
  } catch (reason) { fail(reason) } finally { deleting.value = '' }
}
async function uploadFiles(files: FileList | File[] | null) {
  dragging.value = false
  if (!files?.length) return
  if (!selectedKbId.value) { error.value = '请先创建并选择一个知识库。'; return }
  if (uploading.value) return
  const limit = (runtime.value?.max_upload_mb || 50) * 1024 * 1024
  const added: UploadItem[] = Array.from(files).map(file => ({ id: crypto.randomUUID(), file, knowledgeBaseId: selectedKbId.value,
    status: file.size > limit ? 'failed' : 'waiting', attempts: 0, error: file.size > limit ? '文件超过上传大小上限' : '' }))
  uploadItems.value.push(...added)
  await runUploads(uploadItems.value.filter(item => item.status === 'waiting'))
}
async function runUploads(items: UploadItem[]) {
  if (uploading.value || !items.length) return
  uploading.value = true; error.value = ''; uploadController = new AbortController()
  try {
    await submitUploads(items, async (item, signal) => {
      if (item.file.size > (runtime.value?.max_upload_mb || 50) * 1024 * 1024) throw new Error('文件超过上传大小上限，请缩小文件后重新选择')
      const form = new FormData(); form.append('file', item.file); form.append('knowledge_base_id', item.knowledgeBaseId)
      return api<{ duplicate: boolean }>('/documents/upload', { method: 'POST', body: form, signal })
    }, uploadController.signal)
    await refreshDocuments()
    const completed = uploadItems.value.filter(item => ['success', 'duplicate'].includes(item.status)).length
    notify(`${completed} / ${uploadItems.value.length} 个文件已提交或已存在；失败项可单独重试。`)
  } catch (reason) { fail(reason); await refreshDocuments() }
  finally { uploading.value = false; uploadController = null; if (fileInput.value) fileInput.value.value = '' }
}
function retryUploads() { return runUploads(uploadItems.value.filter(item => ['failed', 'waiting'].includes(item.status))) }
function citationLocation(citation: Citation) {
  const loc = citation.locator
  if (loc?.line_start) return `${loc.section?.join(' / ') || '正文'} · 第 ${loc.line_start}–${loc.line_end || loc.line_start} 行`
  return citation.document_name.toLowerCase().endsWith('.pdf') && citation.page ? `第 ${citation.page} 页` : '文档片段'
}
async function refreshDocuments() {
  try {
    const [docs, bases] = await Promise.all([api<Document[]>('/documents'), api<KnowledgeBase[]>('/knowledge-bases')])
    documents.value = docs; knowledgeBases.value = bases
  } catch (reason) { fail(reason) }
}
async function pollJobs() {
  if (pollRunning || !user.value) return
  const active = documents.value.filter(doc => !['ready', 'completed', 'failed', 'cancelled'].includes(doc.status) && doc.job_id)
  if (!active.length) return
  pollRunning = true
  try {
    const results = await Promise.allSettled(active.map(async doc => {
      const job = await api<Job>(`/jobs/${doc.job_id}`); jobs.value[job.id] = job
    }))
    const authFailure = results.find(result => result.status === 'rejected' && result.reason instanceof ApiError && result.reason.status === 401)
    if (authFailure?.status === 'rejected') fail(authFailure.reason)
    if (user.value) await refreshDocuments()
  } finally { pollRunning = false }
}
async function removeDocument(doc: Document) {
  error.value = ''
  pendingDelete.value = doc
}
async function confirmDeleteDocument() {
  const doc = pendingDelete.value
  if (!doc) return
  deleting.value = doc.id
  try {
    await api(`/documents/${doc.id}`, { method: 'DELETE' })
    documents.value = documents.value.filter(item => item.id !== doc.id)
    pendingDelete.value = null
    await refreshDocuments(); notify('文档已删除，相关任务已停止；文件与向量将在后台清理。')
  }
  catch (reason) { fail(reason) } finally { deleting.value = '' }
}
async function retryDocument(doc: Document) {
  try { await api(`/documents/${doc.id}/retry`, { method: 'POST' }); await refreshDocuments(); notify('已重新提交处理。') }
  catch (reason) { fail(reason) }
}
async function openPreview(doc: Document) {
  previewLoading.value = true; previewingId.value = doc.id
  try { preview.value = { document: doc, chunks: await api<Chunk[]>(`/documents/${doc.id}/chunks`) } }
  catch (reason) { fail(reason) } finally { previewLoading.value = false; previewingId.value = '' }
}
async function download(id: string, name: string, revisionId?: string) {
  try { await downloadDocument(id, name, revisionId) } catch (reason) { fail(reason) }
}
function newConversation() { conversationId.value = ''; messages.value = []; question.value = ''; focusedMessageId.value = ''; focusedCitationId.value = ''; lastResponse.value = null; traceOpen.value = false }
async function openConversation(id: string) {
  if (chatLoading.value) return
  historyLoading.value = true
  try {
    const conversation = await api<Conversation & { messages: Message[] }>(`/conversations/${id}`)
    conversationId.value = id; messages.value = conversation.messages; focusedMessageId.value = ''; focusedCitationId.value = ''; lastResponse.value = null
  } catch (reason) { fail(reason) } finally { historyLoading.value = false }
}
async function deleteConversation(conversation: Conversation) {
  if (chatLoading.value || !window.confirm(`删除会话“${conversation.title}”？`)) return
  try {
    await api(`/conversations/${conversation.id}`, { method: 'DELETE' })
    conversations.value = conversations.value.filter(item => item.id !== conversation.id)
    if (conversationId.value === conversation.id) newConversation()
  } catch (reason) { fail(reason) }
}
async function askQuestion() {
  if (!question.value.trim() || chatLoading.value) return
  const text = question.value.trim(); question.value = ''; chatLoading.value = true; error.value = ''; chatDraft.value = ''
  const localId = `pending-${Date.now()}`
  messages.value.push({ id: localId, role: 'user', content: text }); scrollMessages()
  const body = { question: text, knowledge_base_ids: chatKbIds.value, top_k: Number(topK.value), mode: chatMode.value, ...(conversationId.value ? { conversation_id: conversationId.value } : {}) }
  const serialized = JSON.stringify(body)
  if (!lastAttempt || lastAttempt.body !== serialized) lastAttempt = { key: crypto.randomUUID(), body: serialized }
  chatController = new AbortController()
  try {
    const response = await streamChat({ ...body, request_id: lastAttempt.key }, chatController.signal, token => { chatDraft.value += token; scrollMessages() })
    conversationId.value = response.conversation_id; lastResponse.value = response; lastAttempt = null
    messages.value.push({ id: response.message_id, role: 'assistant', content: response.answer, citations: response.citations, trace: response.trace })
    focusedMessageId.value = response.message_id
    conversations.value = await api<Conversation[]>('/conversations'); scrollMessages()
  } catch (reason) {
    if (reason instanceof DOMException && reason.name === 'AbortError') notify('已停止生成。')
    else fail(reason)
    messages.value = messages.value.filter(item => item.id !== localId); question.value = text
  } finally { chatLoading.value = false; chatDraft.value = ''; chatController = null }
}
function scrollMessages() { setTimeout(() => messageList.value?.scrollTo({ top: messageList.value.scrollHeight, behavior: 'smooth' }), 80) }
function focusCitation(messageId: string, citation: Citation) { focusedMessageId.value = messageId; focusedCitationId.value = citation.chunk_id; evidenceOpen.value = true }
function addCase() { evaluationCases.value.push({ question: '', reference: '', expected_document_ids: [] }) }
function addHistoryCase(message: Message) {
  const messageIndex = messages.value.findIndex(item => item.id === message.id)
  const originalQuestion = messages.value.slice(0, messageIndex).reverse().find(item => item.role === 'user')?.content
  if (!originalQuestion) { error.value = '未找到这条历史答案对应的问题，请重新打开会话后重试。'; return }
  if (evaluationCases.value.some(item => item.message_id === message.id)) { go('evaluations'); notify('这条历史答案已经在评测用例中。'); return }
  const testCase: EvaluationCase = { question: originalQuestion, reference: '', expected_document_ids: [], message_id: message.id }
  const initial = evaluationCases.value[0]
  if (evaluationCases.value.length === 1 && initial && !initial.question && !initial.reference && !initial.message_id) evaluationCases.value = [testCase]
  else evaluationCases.value.push(testCase)
  evaluationKbIds.value = [...chatKbIds.value]
  if (!evaluationName.value) evaluationName.value = '历史问答离线复评'
  evidenceOpen.value = false; go('evaluations'); notify('已加入历史答案，填写参考答案后即可复评，无需重新生成。')
}
function validCases(): EvaluationCase[] | null {
  if (!evaluationCases.value.length || evaluationCases.value.some(item => !item.question.trim() || !item.reference.trim())) {
    error.value = '请为每个测试用例填写问题和参考答案。'; return null
  }
  return evaluationCases.value.map(item => ({ question: item.question.trim(), reference: item.reference.trim(), expected_document_ids: item.expected_document_ids || [], ...(item.message_id ? { message_id: item.message_id } : {}) }))
}
async function runEvaluation() {
  const cases = validCases(); if (!cases) return
  const previousIds = new Set(evaluations.value.map(item => item.id))
  evaluating.value = true; error.value = ''
  try {
    const result = await api<Evaluation>('/evaluations', { method: 'POST', body: JSON.stringify({ name: evaluationName.value.trim() || `评测 ${new Date().toLocaleString('zh-CN')}`, cases, knowledge_base_ids: evaluationKbIds.value, use_ragas: useRagas.value }) })
    evaluation.value = result; evaluations.value = await api<Evaluation[]>('/evaluations'); notify('评测已提交，结果显示在下方。')
  } catch (reason) {
    fail(reason)
    try {
      evaluations.value = await api<Evaluation[]>('/evaluations')
      const savedFailure = evaluations.value.find(item => !previousIds.has(item.id))
      if (savedFailure) evaluation.value = savedFailure
    } catch { /* Preserve the actionable error from the original evaluation request. */ }
  } finally { evaluating.value = false }
}
let evalPollRunning = false
async function pollEvaluations() {
  if (!user.value || evalPollRunning || !evaluations.value.some(item => ['queued', 'running'].includes(item.status))) return
  evalPollRunning = true
  try {
    evaluations.value = await api<Evaluation[]>('/evaluations')
    if (evaluation.value && ['queued', 'running'].includes(evaluation.value.status)) evaluation.value = await api<Evaluation>(`/evaluations/${evaluation.value.id}`)
  } catch (reason) { fail(reason) } finally { evalPollRunning = false }
}
async function compareBaseline() {
  const cases = validCases(); if (!cases) return
  if (cases.some(item => !item.expected_document_ids?.length)) { error.value = '比较检索命中率前，请为每个用例选择至少一篇期望命中的文档。'; return }
  comparing.value = true; error.value = ''
  try { comparison.value = await api<Comparison>('/evaluations/compare', { method: 'POST', body: JSON.stringify({ cases: cases.map(({ message_id: _messageId, ...item }) => item), knowledge_base_ids: evaluationKbIds.value }) }) }
  catch (reason) { fail(reason) } finally { comparing.value = false }
}
async function openEvaluation(id: string) {
  evaluationLoading.value = true
  try { evaluation.value = await api<Evaluation>(`/evaluations/${id}`) } catch (reason) { fail(reason) } finally { evaluationLoading.value = false }
}
async function refreshSystem() { busy.value = true; try { runtime.value = await api<Runtime>('/system'); await checkHealth() } catch (reason) { fail(reason) } finally { busy.value = false } }
onMounted(async () => {
  await checkHealth()
  localStorage.removeItem('zhiheng_token')
  oidcEnabled.value = (await api<{ enabled: boolean }>('/auth/oidc/config').catch(() => ({ enabled: false }))).enabled
  {
    try { user.value = await api<User>('/auth/me'); await loadWorkspace() }
    catch (reason) { localStorage.removeItem('zhiheng_token'); if (!(reason instanceof ApiError && reason.status === 401)) authError.value = reason instanceof Error ? reason.message : '无法恢复登录' }
  }
  restoring.value = false
  pollTimer = setInterval(() => { void pollJobs(); void pollEvaluations() }, 2500)
  healthTimer = setInterval(() => { void checkHealth() }, 30_000)
})
onUnmounted(() => { clearInterval(pollTimer); clearInterval(healthTimer); clearTimeout(noticeTimer); chatController?.abort(); uploadController?.abort() })
</script>

<template>
  <div v-if="pendingDelete" class="modal-backdrop" @click.self="!deleting && (pendingDelete=null)"><section class="modal deletion-dialog" role="dialog" aria-modal="true" aria-label="删除文档确认"><h2>删除文档</h2><p>{{pendingDelete.name}}</p><p>将取消该文档的解析或索引任务，并删除文档与版本引用。原文件和向量由后台清理，此操作无法撤销。</p><p v-if="error" class="gov-error" role="alert">{{error}}</p><div class="platform-actions"><button class="btn secondary" :disabled="!!deleting" @click="pendingDelete=null">保留文档</button><button class="btn primary" :disabled="!!deleting" @click="confirmDeleteDocument">{{deleting ? '正在取消任务并删除…' : '确认删除'}}</button></div></section></div>
  <div v-if="restoring" class="restore-screen"><div class="brand-mark"><Layers3 :size="27" /></div><LoaderCircle class="spin" :size="21" /><p>正在连接知识工作台…</p></div>
  <div v-else-if="!user" class="auth-shell">
    <div class="auth-story">
      <a class="brand auth-brand" href="#"><span class="brand-mark"><Layers3 :size="25" /></span><span>知衡<small>KNOWLEDGE OS</small></span></a>
      <div class="story-copy"><span class="eyebrow"><span class="mini-line"></span> KNOWLEDGE, WITH EVIDENCE.</span><h1>让每一份知识，<br>成为有据可循的答案<span>。</span></h1><p>连接分散的文档，检索真正相关的证据，<br>在一个工作台里完成知识管理、智能问答与质量评测。</p></div>
      <div class="story-diagram"><div class="diagram-source"><FileText :size="22" /><span>企业文档</span><small>PDF · Word · 图片 · 文本</small></div><span class="diagram-line"></span><div class="diagram-engine"><Layers3 :size="29" /><span>知衡</span></div><span class="diagram-line"></span><div class="diagram-source"><ShieldCheck :size="22" /><span>可追溯的答案</span><small>原文引用 · 检索轨迹</small></div></div>
      <div class="story-footer"><span><ShieldCheck :size="15" /> 用户数据隔离</span><span><FileSearch :size="15" /> 原文证据溯源</span><span><FlaskConical :size="15" /> 真实质量评测</span></div>
    </div>
    <div class="auth-panel"><div class="auth-card"><span class="eyebrow">YOUR KNOWLEDGE WORKSPACE</span><h2>{{ authMode === 'login' ? '欢迎回来' : '创建你的知识空间' }}</h2><p>{{ authMode === 'login' ? '登录知衡，继续探索你的知识。' : '从第一份文档开始，积累可信赖的知识。' }}</p>
      <div class="auth-tabs"><button :class="{ active: authMode === 'login' }" @click="authMode = 'login'; authError = ''">登录账号</button><button :class="{ active: authMode === 'register' }" @click="authMode = 'register'; authError = ''">注册账号</button></div>
      <form class="auth-form" @submit.prevent="signIn"><label v-if="authMode === 'register'">你的名字<input v-model="auth.name" autocomplete="name" placeholder="如何称呼你" required maxlength="80" /></label><label>邮箱地址<input v-model="auth.email" type="email" autocomplete="email" placeholder="name@company.com" required /></label><label>密码<input v-model="auth.password" type="password" :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'" placeholder="至少 8 位字符" minlength="8" maxlength="128" required /></label><div v-if="authError" class="alert error-alert" role="alert">{{ authError }}</div><button type="submit" class="btn primary auth-submit" :disabled="authenticating"><LoaderCircle v-if="authenticating" :size="17" class="spin" />{{ authenticating ? '正在连接…' : authMode === 'login' ? '进入工作台' : '创建账号并进入' }}<ArrowRight v-if="!authenticating" :size="17" /></button></form>
      <a v-if="oidcEnabled" class="btn secondary" :href="`${API_BASE}/auth/oidc/start`">使用企业账号登录</a><div class="auth-connect"><span class="status-dot" :class="health"></span>{{ connectionText() }}<button class="text-button" @click="checkHealth">重新检测</button></div>
    </div><p class="auth-copyright">知衡 · 让知识流动，让答案可信。</p></div>
  </div>

  <div v-else class="app-shell">
    <div v-if="mobileNav" class="sidebar-overlay" @click="mobileNav = false"></div>
    <aside class="sidebar" :class="{ mobile: mobileNav }">
      <button class="brand" @click="go('library')"><span class="brand-mark"><Layers3 :size="23" /></span><span>知衡<small>KNOWLEDGE OS</small></span></button>
      <span class="nav-heading">工作空间 <span>WORKSPACE</span></span>
      <nav><button v-for="item in navItems" :key="item.id" class="nav-link" :class="{ active: view === item.id }" @click="go(item.id)"><component :is="item.icon" :size="19" /><span>{{ item.label }}</span><span v-if="item.id === 'library' && knowledgeBases.length" class="nav-count">{{ knowledgeBases.length }}</span><ChevronRight v-if="view === item.id" class="nav-arrow" :size="15" /></button></nav>
      <div class="sidebar-note"><span class="note-icon"><Sparkles :size="16" /></span><strong>每个答案，都有出处。</strong><p>从原文到洞察，<br>构建你的可信知识网络。</p><button @click="go('chat')">开始一次提问 <ArrowRight :size="14" /></button></div>
      <div class="sidebar-bottom"><button class="nav-link" :class="{ active: view === 'system' }" @click="go('system')"><Settings2 :size="18" /><span>系统与模型</span><span class="status-dot" :class="health"></span></button><div class="profile"><span class="avatar">{{ user.name?.slice(0, 1) || 'U' }}</span><div><strong>{{ user.name || '知识探索者' }}</strong><small :title="user.email">{{ user.email }}</small></div><button class="icon-button" title="退出登录" aria-label="退出登录" @click="logout"><LogOut :size="17" /></button></div></div>
    </aside>

    <div class="main-shell"><header class="topbar"><div class="breadcrumb"><button class="icon-button mobile-menu" aria-label="打开导航" @click="mobileNav = true"><Menu :size="20" /></button><span>工作空间</span><ChevronRight :size="13" /><strong>{{ titles[view] }}</strong></div><div class="topbar-right"><span class="connection"><span class="status-dot" :class="health"></span>{{ connectionText() }}</span><span class="topbar-divider"></span><span class="workspace-label"><ShieldCheck :size="15" /> 私有知识空间</span><span class="avatar small">{{ user.name?.slice(0, 1) || 'U' }}</span></div></header>
      <div v-if="error" class="global-error alert error-alert" role="alert"><span>{{ error }}</span><button class="icon-button" aria-label="关闭错误提示" @click="error = ''"><X :size="16" /></button></div>

      <main v-if="view === 'library'" class="page library-page">
        <div class="page-heading"><div><span class="eyebrow">YOUR KNOWLEDGE, CONNECTED</span><h1>知识空间<span class="heading-dot">.</span></h1><p>把分散的文档，转化为随时可用的知识。</p></div><button class="btn primary" @click="showKbModal = true"><Plus :size="17" />新建知识库</button></div>
        <div class="stats-grid"><div class="stat-card"><span class="stat-icon teal"><Layers3 :size="19" /></span><div><span>知识库</span><strong>{{ knowledgeBases.length }}<small>个</small></strong></div><span class="stat-caption">按主题组织知识</span></div><div class="stat-card"><span class="stat-icon blue"><FileText :size="19" /></span><div><span>文档总量</span><strong>{{ documents.length }}<small>份</small></strong></div><span class="stat-caption">{{ bytes(totalSize) }} 已存储</span></div><div class="stat-card"><span class="stat-icon violet"><Database :size="19" /></span><div><span>知识分块</span><strong>{{ chunkCount.toLocaleString() }}<small>段</small></strong></div><span class="stat-caption">可检索的知识单元</span></div><div class="stat-card"><span class="stat-icon amber"><FileCheck2 :size="19" /></span><div><span>可用文档</span><strong>{{ readyDocuments }}<small>份</small></strong></div><span class="stat-caption">已发布且有效的版本</span></div></div>
        <section class="section-block"><div class="section-heading"><h2>我的知识库 <span>{{ knowledgeBases.length }}</span></h2><button class="text-button" :disabled="busy" @click="loadWorkspace"><RefreshCw :size="14" :class="{ spin: busy }" /> 刷新</button></div>
          <div v-if="busy && !knowledgeBases.length" class="empty-card"><LoaderCircle class="spin" :size="25" /><p>正在加载知识空间…</p></div>
          <div v-else-if="!knowledgeBases.length" class="empty-card kb-empty"><span class="empty-icon"><FolderOpen :size="30" /></span><h3>为你的知识，建立第一个空间</h3><p>创建知识库后，上传产品手册、研究资料或团队文档。</p><button class="btn primary" @click="showKbModal = true"><Plus :size="16" />创建知识库</button></div>
          <div v-else class="kb-grid"><button v-for="(kb, index) in knowledgeBases" :key="kb.id" class="kb-card" :class="{ selected: selectedKbId === kb.id }" @click="selectKb(kb.id)"><div class="kb-card-top"><span class="kb-icon" :class="['teal', 'blue', 'violet', 'amber'][index % 4]"><BookOpen :size="21" /></span><span class="kb-selected"><Check v-if="selectedKbId === kb.id" :size="14" /><ChevronRight v-else :size="16" /></span></div><h3>{{ kb.name }}</h3><p>{{ kb.description || '让相关文档在这里汇集，成为可追溯的知识。' }}</p><div class="kb-meta"><span><FileText :size="13" />{{ kb.document_count }} 份文档</span><span>{{ date(kb.created_at).split(' ')[0] }}</span></div></button></div>
        </section>
        <section class="document-panel"><div class="document-heading"><div><h2>{{ selectedKb?.name || '文档管理' }}<span class="subtle-tag">文档</span></h2><p>{{ selectedKb?.description || '上传并管理知识库中的原始文档' }}</p></div><div class="button-row"><button v-if="selectedKb && selectedKb.role === 'admin'" class="icon-button danger-button" title="删除当前知识库" aria-label="删除当前知识库" :disabled="deleting === selectedKbId" @click="removeKb"><Trash2 :size="17" /></button><button class="btn secondary" :disabled="!selectedKbId || uploading || selectedKb?.role === 'reader'" @click="fileInput?.click()"><UploadCloud :size="16" />上传文档</button></div></div>
          <input ref="fileInput" type="file" multiple class="hidden" accept=".pdf,.docx,.txt,.md,.csv,.json,.html,.htm,.png,.jpg,.jpeg,.webp" @change="uploadFiles(($event.target as HTMLInputElement).files)" />
          <button class="upload-zone" :class="{ dragging, disabled: !selectedKbId || uploading }" :disabled="!selectedKbId || uploading || selectedKb?.role === 'reader'" @click="fileInput?.click()" @dragover.prevent="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent="uploadFiles($event.dataTransfer?.files || null)"><span class="upload-illustration"><LoaderCircle v-if="uploading" :size="25" class="spin" /><UploadCloud v-else :size="26" /></span><span><strong>{{ uploading ? '正在提交文件…' : '将文件拖到这里，或' }} <em v-if="!uploading">点击上传</em></strong><small>PDF、Word、文本、图片等多格式 · 单文件不超过 {{ runtime?.max_upload_mb || 50 }} MB</small></span><span class="upload-tag">异步解析 · 自动索引</span></button>
          <p class="upload-hint">Markdown 图片引用会保留说明；识别图片中的文字、表格或图示，请同时另行上传图片文件。</p>
          <section v-if="uploadItems.length" class="upload-results" aria-label="批量上传结果" aria-live="polite">
            <div class="upload-results-heading"><strong>文件提交 {{ uploadItems.filter(item => ['success','duplicate'].includes(item.status)).length }} / {{ uploadItems.length }}</strong><div class="button-row"><button v-if="uploading" class="btn secondary" @click="uploadController?.abort()">暂停提交</button><button v-else-if="uploadItems.some(item => ['failed','waiting'].includes(item.status))" class="btn secondary" @click="retryUploads">重试失败项 / 继续</button><button v-if="!uploading" class="text-button" @click="uploadItems=[]">收起结果</button></div></div>
            <ul><li v-for="item in uploadItems" :key="item.id"><span>{{ item.file.name }}</span><span :class="{ 'upload-failed': item.status === 'failed' }">{{ uploadLabels[item.status] }}<small v-if="item.error"> · {{ item.error }}</small></span></li></ul>
          </section>
          <div class="table-toolbar"><span>文档列表 <strong>{{ selectedDocuments.length }}</strong></span><label class="search-input"><Search :size="16" /><input v-model="documentQuery" placeholder="搜索文档名称…" aria-label="搜索文档" /></label></div>
          <div class="table-scroll"><table class="documents-table"><thead><tr><th>文档名称</th><th>状态</th><th>知识分块</th><th>上传时间</th><th class="align-right">操作</th></tr></thead><tbody><tr v-for="doc in filteredDocuments" :key="doc.id"><td><div class="document-name"><span class="file-icon"><FileText :size="21" /></span><div><button class="doc-link" @click="openPreview(doc)">{{ doc.name }}</button><small>{{ bytes(doc.size_bytes) }} <span v-if="doc.error" class="document-error" :title="doc.error">· {{ doc.error }}</span></small></div></div></td><td><span class="status-badge" :class="doc.status"><span class="status-dot"></span>{{ documentStatus(doc) }}</span><small v-if="doc.job_id && jobs[doc.job_id] && ['processing','queued'].includes(doc.status)" class="document-progress">{{jobs[doc.job_id].stage || '等待处理'}} · {{jobs[doc.job_id].progress}}%<span v-if="jobs[doc.job_id].total_pages"> · {{jobs[doc.job_id].processed_pages}} / {{jobs[doc.job_id].total_pages}} 页</span></small><div v-if="!['ready','completed','failed'].includes(doc.status) && progress(doc)" class="progress-bar"><span :style="{ width: `${progress(doc)}%` }"></span></div></td><td class="muted tabular">{{ doc.chunk_count || '—' }}<small v-if="doc.chunk_count"> 段</small></td><td class="muted tabular">{{ date(doc.created_at) }}</td><td><div class="table-actions"><button v-if="canWrite(doc) && ['failed', 'ready'].includes(doc.status)" class="icon-button" :title="doc.status === 'ready' ? '重新索引' : '重试解析'" :aria-label="doc.status === 'ready' ? '重新索引' : '重试解析'" @click="retryDocument(doc)"><RefreshCw :size="16" /></button><button class="icon-button" title="版本与有效期" aria-label="版本与有效期" @click="versionDocument = doc"><Clock3 :size="16" /></button><button class="icon-button" title="查看解析内容" aria-label="查看解析内容" :disabled="previewLoading && previewingId === doc.id" @click="openPreview(doc)"><LoaderCircle v-if="previewLoading && previewingId === doc.id" :size="16" class="spin" /><FileSearch v-else :size="16" /></button><button class="icon-button" title="下载原文" aria-label="下载原文" @click="download(doc.id, doc.name)"><ArrowDownToLine :size="16" /></button><button v-if="canWrite(doc)" class="icon-button danger-button" title="删除文档" aria-label="删除文档" :disabled="deleting === doc.id" @click="removeDocument(doc)"><Trash2 :size="15" /></button></div></td></tr></tbody></table></div>
          <div v-if="!filteredDocuments.length" class="table-empty"><FileText :size="29" /><h3>{{ documentQuery ? '没有找到匹配文档' : '知识积累，从第一份文档开始' }}</h3><p>{{ documentQuery ? '试试其他关键词。' : selectedKbId ? '上传一份文档，系统将自动解析、切块并建立索引。' : '先创建一个知识库，再上传你的文档。' }}</p></div>
          <div class="table-footer"><span><ShieldCheck :size="14" />文档与检索结果按知识库授权范围可见</span><span>共 {{ filteredDocuments.length }} 份文档</span></div>
        </section>
        <div class="page-footer"><span>KNOWLEDGE BECOMES CLARITY.</span><p>知衡 · 连接知识与答案</p></div>
      </main>

      <main v-else-if="view === 'chat'" class="chat-page"><div class="chat-workspace"><aside class="conversation-sidebar"><div class="conversation-heading"><h2>我的会话</h2><button class="icon-button" title="新建会话" aria-label="新建会话" :disabled="chatLoading" @click="newConversation"><Plus :size="18" /></button></div><button class="btn secondary new-chat" :disabled="chatLoading" @click="newConversation"><Plus :size="16" />新建对话</button><span class="nav-heading">历史记录</span><div class="conversation-list"><div v-if="!conversations.length" class="history-empty"><MessageSquare :size="21" /><p>你的提问，<br>会在这里留下记录。</p></div><div v-for="conversation in conversations" :key="conversation.id" class="conversation-item" :class="{ active: conversationId === conversation.id }"><button :disabled="chatLoading" @click="openConversation(conversation.id)"><MessageSquare :size="15" /><span>{{ conversation.title }}</span></button><button class="icon-button delete-conversation" title="删除会话" aria-label="删除会话" :disabled="chatLoading" @click="deleteConversation(conversation)"><Trash2 :size="13" /></button></div></div></aside>
          <section class="chat-main"><div class="chat-top"><div><span class="eyebrow">ASK YOUR KNOWLEDGE</span><h2>智能问答 <Sparkles :size="17" /></h2></div><div class="segmented"><button :class="{ active: chatMode === 'standard' }" :disabled="chatLoading" @click="chatMode = 'standard'">标准检索</button><button :class="{ active: chatMode === 'agent' }" :disabled="chatLoading" @click="chatMode = 'agent'"><Zap :size="13" />Agent</button></div></div>
            <div class="mobile-chat-tools"><button class="text-button" :disabled="chatLoading" @click="newConversation"><Plus :size="13" />新会话</button><select :value="conversationId" aria-label="会话历史" :disabled="chatLoading || historyLoading" @change="($event.target as HTMLSelectElement).value ? openConversation(($event.target as HTMLSelectElement).value) : newConversation()"><option value="">当前新会话</option><option v-for="item in conversations" :key="item.id" :value="item.id">{{ item.title }}</option></select><button class="text-button evidence-toggle" @click="evidenceOpen = true"><FileSearch :size="13" />证据 / 轨迹</button></div>
            <details class="scope-selector"><summary><Layers3 :size="14" /><span>{{ chatKbIds.length ? `已选择 ${chatKbIds.length} 个知识库` : '检索全部知识库' }}</span><ChevronDown :size="13" /></summary><div class="scope-options"><p>选择检索范围；不勾选时检索全部知识库。</p><label v-for="kb in knowledgeBases" :key="kb.id"><input type="checkbox" :checked="chatKbIds.includes(kb.id)" :disabled="chatLoading" @change="toggleId(chatKbIds, kb.id)" />{{ kb.name }}</label><span v-if="!knowledgeBases.length" class="muted">还没有知识库，请先上传文档。</span><label class="top-k">返回证据数量<select v-model="topK" :disabled="chatLoading"><option :value="3">3 段</option><option :value="5">5 段</option><option :value="8">8 段</option><option :value="10">10 段</option></select></label></div></details>
            <div ref="messageList" class="messages"><div v-if="historyLoading" class="chat-loading"><LoaderCircle class="spin" :size="24" />正在读取会话…</div><div v-else-if="!messages.length" class="chat-welcome"><span class="welcome-symbol"><Sparkles :size="31" /></span><span class="eyebrow">FROM KNOWLEDGE TO CLARITY</span><h1>你的知识，<br>值得一个好答案。</h1><p>向你的文档提问。每个回答都附带来源证据，<br>让洞察可以回到原文，让答案可以被验证。</p><div class="welcome-features"><span><Search :size="15" />混合检索</span><span><FileSearch :size="15" />原文引用</span><span><Layers3 :size="15" />推理轨迹</span></div><button v-if="!readyDocuments" class="text-button" @click="go('library')">先去上传一份文档 <ArrowRight :size="15" /></button></div>
              <article v-for="message in messages" :key="message.id" class="message" :class="message.role"><span class="message-avatar"><Sparkles v-if="message.role === 'assistant'" :size="18" /><span v-else>{{ user.name?.slice(0, 1) || '我' }}</span></span><div class="message-body"><div class="message-author">{{ message.role === 'assistant' ? '知衡助手' : user.name || '我' }}<small v-if="message.role === 'assistant'">基于知识库证据</small></div><div class="message-content">{{ message.content }}</div><div v-if="message.citations?.length" class="message-citations"><button v-for="(citation, index) in message.citations" :key="citation.chunk_id" :class="{ active: focusedMessageId === message.id && focusedCitationId === citation.chunk_id }" @click="focusCitation(message.id, citation)"><span>{{ index + 1 }}</span>{{ citation.document_name }}<small>{{ citationLocation(citation) }}</small></button></div><button v-if="message.role === 'assistant'" class="message-evidence-button" @click="focusedMessageId = message.id; traceOpen = true; evidenceOpen = true"><Layers3 :size="12" />查看检索轨迹 <ChevronRight :size="12" /></button><button v-if="message.role === 'assistant'" class="message-evaluation-button" @click="addHistoryCase(message)"><FlaskConical :size="12" />加入评测</button></div></article>
              <article v-if="chatLoading && chatDraft" class="message assistant"><div class="message-body"><div class="message-author">知衡助手<small>生成中 · 待引用校验</small></div><div class="message-content">{{ chatDraft }}</div></div></article><div v-if="chatLoading" class="message assistant"><span class="message-avatar"><Sparkles :size="18" /></span><div class="thinking"><span></span><span></span><span></span><small>正在检索证据并组织答案…</small></div></div>
            </div>
            <div class="composer-wrap"><button v-if="chatLoading" class="btn secondary" type="button" @click="chatController?.abort()">停止生成</button><div v-if="lastResponse" class="response-meta"><span><CheckCheck :size="12" />{{ lastResponse.provider }}</span><span v-if="lastResponse.cached">缓存命中</span><span v-if="lastResponse.verification">{{ lastResponse.abstained ? '证据不足 · 已拒答' : !lastResponse.verification.passed ? '已回退原文摘录' : lastResponse.verification.semantic_checked ? '事实核验通过' : lastResponse.verification.mode === 'numeric' ? '数字核对通过' : '原文摘录' }}</span><span>{{ lastResponse.citations.length }} 处来源</span></div><form class="composer" @submit.prevent="askQuestion"><textarea v-model="question" placeholder="提问，让知识给出答案…" aria-label="输入问题" rows="2" maxlength="8000" :disabled="chatLoading || historyLoading" @keydown.enter.exact.prevent="askQuestion" /><div class="composer-bottom"><span><ShieldCheck :size="13" />基于你的私有知识库回答</span><button class="send-button" type="submit" aria-label="发送问题" :disabled="!question.trim() || chatLoading || historyLoading"><LoaderCircle v-if="chatLoading" :size="18" class="spin" /><ArrowUp v-else :size="19" /></button></div></form><p class="composer-note">Enter 发送 · Shift + Enter 换行 · 请结合原文核实重要信息</p></div>
          </section>
          <div v-if="evidenceOpen" class="evidence-drawer-backdrop" @click="evidenceOpen = false"></div><aside class="evidence-panel" :class="{ open: evidenceOpen }"><button class="icon-button evidence-close" aria-label="关闭证据栏" @click="evidenceOpen = false"><X :size="18" /></button><div class="evidence-heading"><h3><FileSearch :size="17" />参考证据</h3><span>{{ evidence.length }}</span></div><p class="evidence-description">答案的每一步，都能找到依据。</p><div v-if="!evidence.length" class="evidence-empty"><div class="evidence-paper"><FileSearch :size="34" /></div><h4>等待发现相关知识</h4><p>提问后，相关文档片段、<br>来源页码与检索轨迹将在这里展示。</p></div><div v-else class="evidence-list"><article v-for="(citation, index) in evidence" :key="citation.chunk_id" class="evidence-card" :class="{ focused: focusedCitationId === citation.chunk_id }"><div class="evidence-card-title"><span>{{ index + 1 }}</span><strong>{{ citation.document_name }}</strong></div><div class="evidence-card-meta"><span>{{ citationLocation(citation) }}</span><span v-if="Number.isFinite(citation.score)" :title="'检索排序分数，不代表准确率'">排序分 {{ citation.score.toFixed(4) }}</span></div><p v-for="warning in citation.locator?.warnings || []" :key="warning" class="upload-hint">{{ warning }}</p><p>{{ citation.text }}</p><button class="text-button" @click="download(citation.document_id, citation.document_name, citation.revision_id)">查看原文 <ArrowDownToLine :size="12" /></button></article></div><details class="trace-panel" :open="traceOpen"><summary @click.prevent="traceOpen = !traceOpen"><Layers3 :size="15" />检索轨迹 <span>{{ trace.length }} 步</span><ChevronDown :size="13" /></summary><div v-if="!trace.length" class="trace-empty">运行一次问答后展示真实检索过程。</div><ol v-else><li v-for="(item, index) in trace" :key="index"><strong>{{ item.step }}</strong><pre>{{ display(item.detail) }}</pre></li></ol></details></aside>
        </div></main>

      <main v-else-if="view === 'evaluations'" class="page evaluation-page"><div class="page-heading"><div><span class="eyebrow">MEASURE WHAT MATTERS</span><h1>评测中心<span class="heading-dot">.</span></h1><p>用真实测试用例，衡量检索与回答的每一次进步。</p></div><span class="outline-chip"><FlaskConical :size="15" />Evidence over assumptions</span></div>
        <div class="evaluation-intro"><span class="stat-icon teal"><FlaskConical :size="22" /></span><div><h3>好的答案，需要可验证的质量。</h3><p>填写问题、参考答案与期望文档，运行检索评测。配置真实模型后，可启用 Ragas 答案质量评测。</p></div><span class="outline-chip">无预设结果</span></div>
        <section class="panel evaluation-builder"><div class="section-heading"><h2>创建评测 <span>TEST SUITE</span></h2><button class="text-button" :disabled="evaluating || comparing" @click="addCase"><Plus :size="15" />添加用例</button></div><div class="evaluation-config"><label>评测名称<input v-model="evaluationName" placeholder="例如：产品知识库回归评测" maxlength="120" /></label><div><span class="field-label">知识库范围 <small>不选时使用全部</small></span><div class="kb-chips"><button v-for="kb in knowledgeBases" :key="kb.id" :class="{ selected: evaluationKbIds.includes(kb.id) }" @click="toggleId(evaluationKbIds, kb.id)"><Check v-if="evaluationKbIds.includes(kb.id)" :size="12" /><BookOpen v-else :size="12" />{{ kb.name }}</button><span v-if="!knowledgeBases.length" class="muted">尚无知识库</span></div></div></div>
          <div class="case-list"><div v-for="(testCase, index) in evaluationCases" :key="index" class="evaluation-case"><div class="case-heading"><span>CASE <strong>{{ String(index + 1).padStart(2, '0') }}</strong><em v-if="testCase.message_id" class="history-case-badge"><Clock3 :size="11" />历史答案 · 不重新生成</em></span><button class="icon-button danger-button" aria-label="删除用例" :disabled="evaluationCases.length === 1 || evaluating || comparing" @click="evaluationCases.splice(index, 1)"><Trash2 :size="15" /></button></div><div class="case-fields"><label>测试问题<textarea v-model="testCase.question" :readonly="!!testCase.message_id" :title="testCase.message_id ? '历史复评使用存档问题' : ''" rows="2" placeholder="希望从知识库中回答的问题" maxlength="8000" /></label><label>参考答案<textarea v-model="testCase.reference" rows="2" placeholder="用于比较的正确答案或关键要点" maxlength="20000" /></label></div><details class="expected-documents"><summary><FileCheck2 :size="14" />期望命中的文档 <span>{{ testCase.expected_document_ids?.length || 0 }} 份</span><ChevronDown :size="13" /></summary><p>用于真实命中率与 Dense / Hybrid 比较；请选中问题应引用的来源。</p><div class="expected-list"><label v-for="doc in documents.filter(d => !evaluationKbIds.length || evaluationKbIds.includes(d.knowledge_base_id))" :key="doc.id"><input type="checkbox" :checked="testCase.expected_document_ids?.includes(doc.id)" @change="toggleId(testCase.expected_document_ids!, doc.id)" /><span>{{ doc.name }}</span></label><span v-if="!documents.length" class="muted">上传文档后即可设置检索目标。</span></div></details></div></div>
          <div class="evaluation-actions"><label class="checkbox-label"><input v-model="useRagas" type="checkbox" />启用 Ragas <small>需要配置真实模型</small></label><div class="button-row"><button class="btn secondary" :disabled="evaluating || comparing || !readyDocuments" @click="compareBaseline"><LoaderCircle v-if="comparing" :size="15" class="spin" /><Layers3 v-else :size="15" />比较检索基线</button><button class="btn primary" :disabled="evaluating || comparing || !readyDocuments" @click="runEvaluation"><LoaderCircle v-if="evaluating" :size="16" class="spin" /><FlaskConical v-else :size="16" />{{ evaluating ? '评测运行中…' : '运行评测' }}</button></div></div>
        </section>
        <section v-if="comparison" class="panel comparison-panel"><div class="section-heading"><h2>检索基线比较</h2><span class="outline-chip">同一测试集 · 同一知识范围</span></div><div class="comparison-grid"><div><span class="eyebrow">DENSE</span><h3>向量检索</h3><dl><template v-for="(value, key) in comparisonMetrics(comparison.dense)" :key="key"><dt>{{ metricNames[String(key)] || key }}</dt><dd>{{ metric(value) }}</dd></template></dl></div><div><span class="eyebrow">HYBRID</span><h3>混合检索</h3><dl><template v-for="(value, key) in comparisonMetrics(comparison.hybrid)" :key="key"><dt>{{ metricNames[String(key)] || key }}</dt><dd>{{ metric(value) }}</dd></template></dl></div><div class="comparison-delta"><span>命中率相对变化</span><strong>{{ typeof comparison.relative_hit_rate_change === 'number' ? `${comparison.relative_hit_rate_change > 0 ? '+' : ''}${(comparison.relative_hit_rate_change * 100).toFixed(1)}%` : '无法计算' }}</strong><p>仅反映当前测试集的实际结果。<br>基线为零时，相对变化可能无法计算。</p></div></div></section>
        <div class="evaluation-results-layout"><section class="panel evaluation-results"><div class="section-heading"><h2>评测结果</h2><span v-if="evaluation" class="status-badge" :class="evaluation.status"><span class="status-dot"></span>{{ statusText(evaluation.status) }}</span></div><div v-if="evaluationLoading" class="empty-card"><LoaderCircle :size="22" class="spin" /><p>正在读取评测结果…</p></div><div v-else-if="!evaluation" class="empty-card"><span class="empty-icon"><FlaskConical :size="28" /></span><h3>让数据，给出判断</h3><p>运行评测或选择历史记录，查看真实指标与逐条结果。</p></div><template v-else><div class="result-title"><h3>{{ evaluation.name }}</h3><span>{{ date(evaluation.created_at) }}</span></div><div v-if="evaluation.error" class="alert error-alert">{{ evaluation.error }}</div><div v-if="Object.keys(evaluation.metrics || {}).length" class="result-metrics"><div v-for="(value, key) in evaluation.metrics" :key="key"><span>{{ metricNames[String(key)] || key }}</span><strong>{{ metric(value) }}</strong></div></div><p v-else class="muted result-note">当前运行尚未返回指标。</p><details v-for="(result, index) in evaluation.results || []" :key="index" class="case-result"><summary><span class="case-number">{{ index + 1 }}</span><strong>{{ result.question || `用例 ${index + 1}` }}</strong><ChevronDown :size="15" /></summary><dl><template v-for="(value, key) in result" :key="key"><dt>{{ metricNames[String(key)] || key }}</dt><dd><pre>{{ display(value) }}</pre></dd></template></dl></details></template></section><aside class="panel evaluation-history"><div class="section-heading"><h2>历史运行</h2><span>{{ evaluations.length }}</span></div><div v-if="!evaluations.length" class="history-empty"><Clock3 :size="25" /><p>评测运行记录<br>会自动保存在这里。</p></div><button v-for="run in evaluations" :key="run.id" class="evaluation-history-item" :class="{ active: evaluation?.id === run.id }" @click="openEvaluation(run.id)"><span class="history-run-icon"><FlaskConical :size="16" /></span><span><strong>{{ run.name }}</strong><small>{{ date(run.created_at) }} · {{ statusText(run.status) }}</small></span><ChevronRight :size="14" /></button></aside></div>
      </main>

      <GovernancePanel v-else-if="view === 'governance'" :on-changed="loadWorkspace" /><main v-else class="page system-page"><div class="page-heading"><div><span class="eyebrow">TRANSPARENT BY DESIGN</span><h1>系统与模型<span class="heading-dot">.</span></h1><p>了解当前运行环境，让每一项能力都有真实依据。</p></div><button class="btn secondary" :disabled="busy" @click="refreshSystem"><RefreshCw :size="15" :class="{ spin: busy }" />刷新状态</button></div><div class="runtime-banner"><span class="stat-icon teal"><ShieldCheck :size="25" /></span><div><h3>{{ runtime ? `当前运行模式：${runtime.mode}` : '暂未读取到运行状态' }}</h3><p>下列配置直接来自后端。演示模式、模型供应商与可选能力均按真实运行状态展示。</p></div><span class="status-badge" :class="health === 'online' ? 'ready' : health === 'degraded' || health === 'checking' ? 'queued' : 'failed'"><span class="status-dot"></span>{{ connectionText() }}</span></div><div v-if="runtime" class="provider-grid"><div v-for="provider in [{ name: 'Embedding', label: '文本向量模型', icon: Database, value: runtime.embedding_model ? `${runtime.embedding_provider} · ${runtime.embedding_model}` : runtime.embedding_provider }, { name: 'Language model', label: '回答生成模型', icon: Sparkles, value: runtime.llm_backend ? `${runtime.llm_backend} · ${runtime.llm_model || runtime.llm_provider}` : runtime.llm_provider }, { name: 'Reranker', label: '重排模型', icon: Layers3, value: runtime.reranker }, { name: 'Vector store', label: '向量存储', icon: Database, value: runtime.vector_store }, { name: 'Task queue', label: '异步任务队列', icon: Clock3, value: runtime.queue }]" :key="provider.name" class="panel provider-card"><component :is="provider.icon" :size="22" /><span class="eyebrow">{{ provider.name }}</span><h3>{{ provider.label }}</h3><pre>{{ display(provider.value) }}</pre></div></div><section v-if="runtime" class="panel feature-panel"><div class="section-heading"><h2>运行能力</h2><span class="outline-chip">LIVE CONFIGURATION</span></div><div class="feature-list"><div v-for="(value, key) in runtime.features" :key="key"><span>{{ key }}</span><span v-if="typeof value === 'boolean'" class="feature-value" :class="{ enabled: value }"><Check v-if="value" :size="14" />{{ value ? '已启用' : '未启用' }}</span><pre v-else>{{ display(value) }}</pre></div><div><span>单文件上传上限</span><strong>{{ runtime.max_upload_mb }} MB</strong></div><div><span>前端 API 地址</span><code>{{ API_BASE }}</code></div></div></section><div class="configuration-help"><CircleHelp :size="20" /><div><h3>需要切换模型或启用进阶能力？</h3><p>在项目根目录的 .env 中配置模型、Qdrant、Redis、OCR 或重排模型后，重启后端服务。密钥仅保存在服务端。</p></div></div></main>
    </div>

    <DocumentVersions v-if="versionDocument" :document="versionDocument" :writable="canWrite(versionDocument)" :on-changed="refreshDocuments" @close="versionDocument = null" /><div v-if="showKbModal" class="modal-backdrop" @click.self="showKbModal = false"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="kb-modal-title"><div class="modal-header"><span class="stat-icon teal"><BookOpen :size="22" /></span><button class="icon-button" aria-label="关闭" @click="showKbModal = false"><X :size="19" /></button></div><h2 id="kb-modal-title">新建知识库</h2><p>给相关的知识一个专属空间。</p><form @submit.prevent="createKb"><label>所属空间<select v-model="kbForm.organization_id"><option value="">个人空间</option><option v-for="org in organizations.filter(item => item.role !== 'reader')" :key="org.id" :value="org.id">{{ org.name }}</option></select></label><label v-if="kbForm.organization_id">可见范围<select v-model="kbForm.visibility"><option value="organization">组织全员可见</option><option value="restricted">仅指定成员可见</option></select></label><label>知识库名称<input v-model="kbForm.name" placeholder="例如：产品与技术文档" required maxlength="120" autofocus /></label><label>描述 <small>选填</small><textarea v-model="kbForm.description" rows="3" maxlength="1000" placeholder="简单介绍这个知识库的内容和用途" /></label><div class="modal-actions"><button type="button" class="btn secondary" @click="showKbModal = false">取消</button><button type="submit" class="btn primary" :disabled="creatingKb || !kbForm.name.trim()"><LoaderCircle v-if="creatingKb" class="spin" :size="15" />创建知识库<ArrowRight v-if="!creatingKb" :size="15" /></button></div></form></section></div>
    <div v-if="preview" class="modal-backdrop" @click.self="preview = null"><section class="modal preview-modal" role="dialog" aria-modal="true" aria-labelledby="preview-title"><div class="modal-header"><span class="stat-icon blue"><FileText :size="22" /></span><button class="icon-button" aria-label="关闭预览" @click="preview = null"><X :size="19" /></button></div><h2 id="preview-title">{{ preview.document.name }}</h2><p>{{ preview.chunks.length }} 个知识分块 · {{ bytes(preview.document.size_bytes) }}</p><p v-for="warning in preview.chunks[0]?.locator?.warnings || []" :key="warning" class="upload-hint">{{ warning }}</p><div class="preview-chunks"><div v-for="chunk in preview.chunks" :key="chunk.id"><span class="eyebrow">CHUNK {{ chunk.ordinal + 1 }} <small v-if="chunk.locator?.line_start">· {{ chunk.locator.section?.join(" / ") }} · 第 {{ chunk.locator.line_start }}–{{ chunk.locator.line_end }} 行</small><small v-else-if="preview.document.name.toLowerCase().endsWith('.pdf') && chunk.page">· 第 {{ chunk.page }} 页</small></span><p>{{ chunk.text }}</p></div><div v-if="!preview.chunks.length" class="empty-card"><FileSearch :size="28" /><h3>暂无解析内容</h3><p>{{ preview.document.status === 'failed' ? preview.document.error : '文档处理完成后，知识分块将在这里展示。' }}</p></div></div><div class="modal-actions"><button class="btn secondary" @click="preview = null">关闭</button><button class="btn primary" @click="download(preview.document.id, preview.document.name)"><ArrowDownToLine :size="15" />下载原文</button></div></section></div>
    <div v-if="notice" class="toast" role="status"><span><Check :size="16" /></span>{{ notice }}<button class="icon-button" aria-label="关闭提示" @click="notice = ''"><X :size="15" /></button></div>
  </div>
</template>
