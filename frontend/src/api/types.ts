// The shapes the API returns (docs/api.md). Kept in one file so a change is seen once.

export type ColumnType = 'text' | 'number' | 'money' | 'percent' | 'date'

export interface Column {
  name: string
  type: ColumnType
}

export type Row = Record<string, unknown>

export interface Me {
  role: string
  company: string
  currency: string
  timezone: string
  all_outlets: boolean
  outlets: string[]
  permissions: string[]
  scope_note: string | null
  sign_in: string
}

export interface DemoUser {
  email: string
  role: string
  company: string
}

export interface Source {
  number: number
  document_id: string
  filename: string
  title: string
  page: number | null
  quote: string
  score: number
}

export interface Part {
  agent: string
  question: string
  status: string
  summary: string
  insights: string[]
  sql: string | null
  columns: Column[]
  data: Row[]
  row_count: number
  truncated: boolean
  assumptions: string[]
  sources: Source[]
}

export interface AssistantAnswer {
  question: string
  status: string
  route: string
  reason: string
  summary: string
  clarification: string | null
  results: Part[]
  scope_note: string | null
}

export interface AgentInfo {
  agent: string
  title: string
  responsibilities: string[]
  example_questions: string[]
}

export interface AgentAnswer {
  agent: string
  question: string
  status: string
  summary: string
  insights: string[]
  sql: string | null
  columns: Column[]
  data: Row[]
  row_count: number
  truncated: boolean
  assumptions: string[]
  scope_note: string | null
}

export interface ToolInfo {
  name: string
  title: string
  description: string
  group: string
  input_schema: Record<string, unknown>
}

export interface ToolList {
  tools: ToolInfo[]
  transport: string
}

export interface ToolResult {
  tool: string
  title: string
  status: 'ok' | 'refused' | 'error' | 'timeout'
  period: { name: string; start: string; end: string } | null
  columns: Column[]
  rows: Row[]
  row_count: number
  truncated: boolean
  notes: string[]
  scope_note: string | null
  code: string | null
  reason: string | null
}

export interface DocumentInfo {
  id: string
  filename: string
  title: string
  kind: 'pdf' | 'docx' | 'txt' | 'md'
  size: number
  status: 'processing' | 'ready' | 'failed'
  error: string | null
  chunk_count: number
  page_count: number | null
  created_at: string
  updated_at: string
}

export interface DocumentList {
  documents: DocumentInfo[]
  can_manage: boolean
  max_mb: number
  max_documents: number
}

export interface ActivityEntry {
  id: string
  created_at: string
  asked_by: string
  question: string
  status: string
  reason: string | null
  row_count: number | null
  duration_ms: number | null
  sql: string | null
}

export interface Activity {
  entries: ActivityEntry[]
  last_24_hours: Record<string, number>
}
