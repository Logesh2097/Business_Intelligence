// One function per API call the pages use. Stores call these; components never do.
import { get, post, remove, upload } from './client'
import type {
  Activity,
  AgentAnswer,
  AgentInfo,
  AssistantAnswer,
  DemoUser,
  DocumentInfo,
  DocumentList,
  Me,
  ToolList,
  ToolResult,
} from './types'

export const fetchMe = (): Promise<Me> => get('/api/v1/me')
export const fetchDemoUsers = (): Promise<DemoUser[]> => get('/api/v1/dev/users')

export const askAssistant = (question: string): Promise<AssistantAnswer> =>
  post('/api/v1/assistant/query', { question })

export const fetchAgents = (): Promise<AgentInfo[]> => get('/api/v1/agents')
export const askAgent = (agent: string, question: string): Promise<AgentAnswer> =>
  post(`/api/v1/agents/${encodeURIComponent(agent)}/query`, { question })

export const fetchTools = (): Promise<ToolList> => get('/api/v1/tools')
export const runTool = (name: string, args: Record<string, unknown> = {}): Promise<ToolResult> =>
  post(`/api/v1/tools/${encodeURIComponent(name)}`, { arguments: args })

export const fetchDocuments = (): Promise<DocumentList> => get('/api/v1/documents')
export function uploadDocument(file: File, title: string): Promise<DocumentInfo> {
  const query = new URLSearchParams({ filename: file.name })
  if (title.trim()) query.set('title', title.trim())
  return upload(`/api/v1/documents?${query.toString()}`, file)
}
export const reindexDocument = (id: string): Promise<DocumentInfo> =>
  post(`/api/v1/documents/${encodeURIComponent(id)}/reindex`)
export const deleteDocument = (id: string): Promise<void> =>
  remove(`/api/v1/documents/${encodeURIComponent(id)}`)

export const fetchActivity = (limit = 50): Promise<Activity> =>
  get(`/api/v1/activity?limit=${limit}`)
