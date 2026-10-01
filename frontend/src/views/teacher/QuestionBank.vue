<template>
  <div class="page">
    <el-card>
      <template #header>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h3>题库管理</h3>
          <el-button type="primary" @click="openEdit()">新建题目</el-button>
        </div>
      </template>

      <el-table :data="rows.items || []" style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="type" label="题型" width="100">
          <template #default="{ row }">{{ typeMap[row.type] }}</template>
        </el-table-column>
        <el-table-column prop="content" label="题干" show-overflow-tooltip />
        <el-table-column prop="difficulty" label="难度" width="100" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button type="primary" link @click="openEdit(row)">编辑</el-button>
            <el-button type="danger" link @click="remove(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination layout="total, prev, pager, next" :total="rows.total || 0" :page-size="20" v-model:current-page="page" />

      <el-dialog v-model="dialogVisible" :title="editing ? '编辑题目' : '新建题目'" width="640px">
        <QuestionForm :initial="editing" @submit="onSubmit" @cancel="dialogVisible = false" />
      </el-dialog>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { listQuestions, deleteQuestion } from '../../api/questions'
import { ElMessage } from 'element-plus'
import QuestionForm from '../../components/QuestionForm.vue'

const rows = ref<any>({ items: [], total: 0 })
const page = ref(1)
const dialogVisible = ref(false)
const editing = ref<any>(null)
const typeMap: Record<string, string> = { single: '单选', multiple: '多选', judge: '判断', short: '简答' }

onMounted(async () => {
  await load()
})

async function load() {
  rows.value = await listQuestions({ page: page.value, page_size: 20 })
}

async function remove(id: number) {
  await deleteQuestion(id)
  ElMessage.success('删除成功')
  await load()
}

function openEdit(row?: any) {
  editing.value = row || null
  dialogVisible.value = true
}

async function onSubmit() {
  dialogVisible.value = false
  await load()
}
</script>

<style scoped>
.page { max-width: 1200px; margin: 0 auto; }
</style>
