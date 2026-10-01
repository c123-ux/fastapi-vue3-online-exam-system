<template>
  <div class="page">
    <el-card>
      <template #header><h3>批改简答题</h3></template>
      <div v-if="!recordId">加载中...</div>
      <div v-else>
        <el-alert :title="`待批改：${pendingCount}`" type="info" :closable="false" style="margin-bottom: 16px;" />
        <el-table :data="items" style="width: 100%">
          <el-table-column prop="sort_order" label="#" width="60" />
          <el-table-column prop="type" label="题型" width="100" />
          <el-table-column prop="full_score" label="满分" width="100" />
          <el-table-column prop="student_answer" label="学生答案" show-overflow-tooltip />
          <el-table-column prop="reference_answer" label="参考答案" show-overflow-tooltip />
          <el-table-column label="操作" width="260">
            <template #default="{ row }">
              <el-input-number v-model="row.score" :min="0" :max="row.full_score" :step="0.5" />
              <el-button type="primary" style="margin-left: 8px;" @click="submit(row)">提交</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getAnswers, reviewAnswer } from '../../api/exams'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const recordId = Number(route.params.recordId)
const items = ref<any[]>([])
const pendingCount = ref(0)

onMounted(async () => {
  const data = await getAnswers(recordId)
  items.value = data.items || []
  pendingCount.value = data.pending_review_count || 0
})

async function submit(row: any) {
  if (row.score === undefined || row.score === null) {
    ElMessage.error('请输入分值')
    return
  }
  await reviewAnswer(recordId, { question_id: row.question_id, score: row.score, comment: '' })
  ElMessage.success('批改成功')
  const data = await getAnswers(recordId)
  items.value = data.items || []
  pendingCount.value = data.pending_review_count || 0
}
</script>

<style scoped>
.page { max-width: 1200px; margin: 0 auto; }
</style>
