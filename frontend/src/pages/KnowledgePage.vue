<script setup lang="ts">
import DataCard from '../components/DataCard.vue'
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { computed, onMounted, ref } from 'vue'
import StateBlock from '../components/StateBlock.vue'
import TablePager from '../components/TablePager.vue'
import { pageOf } from '../lib/paging'
import { statusLook } from '../lib/answer'
import { formatBytes, formatDate } from '../lib/format'
import { ACCEPTED, useDocumentsStore } from '../stores/documents'

const store = useDocumentsStore()
const { documents, canManage, maxMb, maxDocuments, loading, loaded, error, uploading, uploadError, working } =
  storeToRefs(store)

const file = ref<File | null>(null)
const title = ref('')
const confirming = ref('') // the id of the document about to be deleted
const page = ref(1)
const shownDocuments = computed(() => pageOf(documents.value, page.value))

function picked(value: File | File[] | null | undefined): void {
  file.value = Array.isArray(value) ? (value[0] ?? null) : (value ?? null)
}

async function send(): Promise<void> {
  if (await store.upload(file.value, title.value)) {
    file.value = null
    title.value = ''
  }
}

async function remove(id: string): Promise<void> {
  confirming.value = ''
  await store.remove(id)
}

onMounted(store.refresh)
</script>

<template>
  <div class="main-content main-content--narrow">
    <PageHeader
      title="Knowledge base"
      subtitle="Documents your company has uploaded: policies, procedures, notes. The Assistant answers questions from them and shows which passages it used. Spreadsheets are not accepted: numbers belong in the database."
    >
      <template #actions>
        <v-btn variant="tonal" size="small" prepend-icon="mdi-refresh" :loading="loading" @click="store.refresh">Refresh</v-btn>
      </template>
    </PageHeader>

    <DataCard v-if="canManage" title="Upload a document" class="mb-6">
      <v-row dense>
        <v-col cols="12" md="6">
          <v-file-input
            :model-value="file"
            :accept="ACCEPTED.join(',')"
            label="PDF, Word (DOCX), text or Markdown"
            show-size
            :disabled="uploading"
            :error-messages="uploadError"
            @update:model-value="picked"
          />
        </v-col>
        <v-col cols="12" md="4">
          <v-text-field v-model="title" label="Title (optional)" :disabled="uploading" maxlength="200" />
        </v-col>
        <v-col cols="12" md="2">
          <v-btn color="primary" block :loading="uploading" :disabled="!file" @click="send">Upload</v-btn>
        </v-col>
      </v-row>
      <p class="text-caption text-medium-emphasis mb-0">
        At most {{ maxMb }} MB a file and {{ maxDocuments }} documents. The file is read and indexed
        while it uploads; this can take a few seconds.
      </p>
    </DataCard>
    <v-alert v-else-if="loaded" type="info" variant="tonal" density="comfortable" class="mb-6">
      Only an administrator can upload, re-index or delete documents.
    </v-alert>

    <StateBlock
      :loading="loading && !loaded"
      :error="error"
      :empty="loaded && documents.length === 0"
      empty-text="No documents have been uploaded yet."
      skeleton="table-row@4"
      @retry="store.refresh"
    >
      <v-table density="comfortable" class="table-card">
        <thead>
          <tr>
            <th>Document</th>
            <th>Status</th>
            <th class="text-right">Size</th>
            <th class="text-right">Passages</th>
            <th>Uploaded</th>
            <th v-if="canManage" class="text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="document in shownDocuments" :key="document.id">
            <td class="py-2">
              <div class="font-weight-medium">{{ document.title }}</div>
              <div class="text-caption text-medium-emphasis">
                {{ document.filename }} · {{ document.kind.toUpperCase() }}<template v-if="document.page_count"> · {{ document.page_count }} pages</template>
              </div>
              <div v-if="document.error" class="text-caption text-error">{{ document.error }}</div>
            </td>
            <td>
              <v-chip size="small" class="status-chip" variant="tonal" :color="statusLook(document.status).color" :prepend-icon="statusLook(document.status).icon">
                {{ statusLook(document.status).label }}
              </v-chip>
            </td>
            <td class="text-right">{{ formatBytes(document.size) }}</td>
            <td class="text-right">{{ document.chunk_count }}</td>
            <td>{{ formatDate(document.created_at) }}</td>
            <td v-if="canManage" class="text-right text-no-wrap">
              <v-btn size="small" variant="text" :loading="working === document.id" :disabled="!!working" @click="store.reindex(document.id)">
                Re-index
              </v-btn>
              <v-btn size="small" variant="text" color="error" :disabled="!!working" @click="confirming = document.id">Delete</v-btn>
            </td>
          </tr>
        </tbody>
        <template #bottom>
          <TablePager v-model="page" :total="documents.length" />
        </template>
      </v-table>
    </StateBlock>

    <v-dialog :model-value="!!confirming" max-width="420" @update:model-value="confirming = ''">
      <v-card>
        <v-card-title class="head-fill">Delete this document?</v-card-title>
        <v-card-text class="pt-4">
          Its file and its passages are removed. The Assistant will no longer answer from it.
          This cannot be undone.
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="confirming = ''">Cancel</v-btn>
          <v-btn color="error" variant="flat" @click="remove(confirming)">Delete</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>
