import { defineStore } from 'pinia'
import { ref } from 'vue'
import { deleteDocument, fetchDocuments, reindexDocument, uploadDocument } from '../api/bi'
import { messageOf } from '../api/client'
import type { DocumentInfo } from '../api/types'

export const ACCEPTED = ['.pdf', '.docx', '.txt', '.md']

/** Why a chosen file cannot be uploaded, or '' when it can. The server checks again. */
export function problemWithFile(file: File | null, maxMb: number): string {
  if (!file) return 'Choose a file first.'
  const name = file.name.toLowerCase()
  if (!ACCEPTED.some((ending) => name.endsWith(ending))) {
    return 'Only PDF, Word (DOCX), text and Markdown files are accepted.'
  }
  if (file.size === 0) return 'The file is empty.'
  if (file.size > maxMb * 1024 * 1024) return `A document may be at most ${maxMb} MB.`
  return ''
}

export const useDocumentsStore = defineStore('documents', () => {
  const documents = ref<DocumentInfo[]>([])
  const canManage = ref(false)
  const maxMb = ref(20)
  const maxDocuments = ref(200)
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref('')
  const uploading = ref(false)
  const uploadError = ref('')
  const working = ref('') // the id of a document being re-indexed or deleted

  async function refresh(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      const list = await fetchDocuments()
      documents.value = list.documents
      canManage.value = list.can_manage
      maxMb.value = list.max_mb
      maxDocuments.value = list.max_documents
      loaded.value = true
    } catch (problem) {
      error.value = messageOf(problem)
    } finally {
      loading.value = false
    }
  }

  /** True when the file was accepted (its indexing may still have failed: see its status). */
  async function upload(file: File | null, title: string): Promise<boolean> {
    uploadError.value = problemWithFile(file, maxMb.value)
    if (uploadError.value || !file) return false
    uploading.value = true
    try {
      const made = await uploadDocument(file, title)
      documents.value = [made, ...documents.value]
      return true
    } catch (problem) {
      uploadError.value = messageOf(problem)
      return false
    } finally {
      uploading.value = false
    }
  }

  async function reindex(id: string): Promise<void> {
    working.value = id
    error.value = ''
    try {
      const updated = await reindexDocument(id)
      documents.value = documents.value.map((document) => (document.id === id ? updated : document))
    } catch (problem) {
      error.value = messageOf(problem)
    } finally {
      working.value = ''
    }
  }

  async function remove(id: string): Promise<void> {
    working.value = id
    error.value = ''
    try {
      await deleteDocument(id)
      documents.value = documents.value.filter((document) => document.id !== id)
    } catch (problem) {
      error.value = messageOf(problem)
    } finally {
      working.value = ''
    }
  }

  return {
    documents, canManage, maxMb, maxDocuments, loading, loaded, error, uploading, uploadError,
    working, refresh, upload, reindex, remove,
  }
})
