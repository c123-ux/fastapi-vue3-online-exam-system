<template>
  <div class="page">
    <el-card header="我的考试记录">
      <el-table :data="records.items || []" style="width: 100%">
        <el-table-column prop="paper_title" label="试卷" />
        <el-table-column prop="status" label="状态">
          <template #default="{ row }">{{ statusMap[row.status] }}</template>
        </el-table-column>
        <el-table-column prop="submit_kind" label="交卷方式">
          <template #default="{ row }">{{ submitMap[row.submit_kind] || '-' }}</template>
        </el-table-column>
        <el-table-column prop="earned_score" label="得分">
          <template #default="{ row }">{{ row.earned_score ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="操作">
          <template #default="{ row }">
            <router-link :to="`/exams/${row.record_id}/score`">
              <el-button type="primary" link>查看成绩</el-button>
            </router-link>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        layout="total, prev, pager, next"
        :total="records.total || 0"
        :page-size="20"
        v-model:current-page="page"
        style="margin-top: 16px; justify-content: flex-end"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { listMyRecords } from '../../api/exams'
import { ElMessage } from 'element-plus'

const router = useRouter()
const records = ref<any>({ items: [], total: 0 })
const page = ref(1)

const statusMap: Record<string, string> = { in_progress: '进行中', final: '已结束' }
const submitMap: Record<string, string> = { normal: '正常', timeout: '超时' }

onMounted(async () => {
  try {
    const data = await listMyRecords({ page: page.value, page_size: 20 })
    records.value = data
  } catch (e: any) {
    // handled by interceptor
  }
})
</script>

<style scoped>
.page { max-width: 1000px; margin: 0 auto; }
</style>
