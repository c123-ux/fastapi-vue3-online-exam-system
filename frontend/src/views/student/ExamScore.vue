<template>
  <div class="page">
    <el-card>
      <template #header>
        <div class="header">
          <div>
            <h3>{{ data.paper_title }}</h3>
            <div class="meta">状态：{{ statusMap[data.status] }} · 交卷方式：{{ submitMap[data.submit_kind] || '-' }}</div>
          </div>
          <div class="score-box">
            <div class="label">总分</div>
            <div class="value">{{ data.earned_score ?? '-' }} / {{ data.full_score }}</div>
          </div>
        </div>
      </template>

      <el-table :data="data.details || []" style="width: 100%">
        <el-table-column prop="sort_order" label="# " width="60" />
        <el-table-column prop="type" label="题型" width="100">
          <template #default="{ row }">{{ typeMap[row.type] }}</template>
        </el-table-column>
        <el-table-column prop="full_score" label="满分" width="80" />
        <el-table-column prop="score" label="得分" width="80">
          <template #default="{ row }">{{ row.score ?? '-' }}</template>
        </el-table-column>
        <el-table-column prop="is_correct" label="是否正确" width="120">
          <template #default="{ row }">
            <el-tag :type="row.is_correct ? 'success' : (row.is_correct === false ? 'danger' : 'info')">
              {{ row.is_correct === true ? '正确' : (row.is_correct === false ? '错误' : '-') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="review_status" label="批改状态" width="140">
          <template #default="{ row }">{{ reviewMap[row.review_status] || '-' }}</template>
        </el-table-column>
        <el-table-column prop="review_comment" label="批改评语" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getScore } from '../../api/exams'

const route = useRoute()
const data = ref<any>({})

const statusMap: Record<string, string> = { in_progress: '进行中', final: '已结束' }
const submitMap: Record<string, string> = { normal: '正常', timeout: '超时' }
const typeMap: Record<string, string> = { single: '单选', multiple: '多选', judge: '判断', short: '简答' }
const reviewMap: Record<string, string> = { needs_review: '待批改', reviewed: '已批改', skipped: '已跳过' }

onMounted(async () => {
  const recordId = Number(route.params.recordId)
  data.value = await getScore(recordId)
})
</script>

<style scoped>
.page { max-width: 1000px; margin: 0 auto; }
.header { display: flex; justify-content: space-between; align-items: center; }
.meta { color: #606266; margin-top: 6px; }
.score-box { text-align: right; }
.score-box .label { color: #606266; font-size: 14px; }
.score-box .value { color: #f56c6c; font-size: 28px; font-weight: 700; }
</style>
