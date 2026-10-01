<template>
  <div class="page">
    <el-card>
      <template #header>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h3>{{ data.title }}</h3>
          <router-link :to="`/papers/${paperId}/stats`"><el-button type="primary">查看统计</el-button></router-link>
        </div>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="状态">{{ statusMap[data.status] }}</el-descriptions-item>
        <el-descriptions-item label="时长">{{ data.duration_minutes }} 分钟</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ data.start_at }}</el-descriptions-item>
        <el-descriptions-item label="结束时间">{{ data.end_at }}</el-descriptions-item>
        <el-descriptions-item label="满分">{{ data.full_score }}</el-descriptions-item>
      </el-descriptions>
      <h4 style="margin-top: 20px;">题目明细</h4>
      <el-table :data="items" style="width: 100%">
        <el-table-column prop="sort_order" label="#" width="60" />
        <el-table-column prop="type" label="题型" width="100">
          <template #default="{ row }">{{ typeMap[row.type] }}</template>
        </el-table-column>
        <el-table-column prop="content" label="题干" show-overflow-tooltip />
        <el-table-column prop="score" label="分值" width="100" />
        <el-table-column prop="correct_answer" label="正确答案" width="120" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { getPaper } from '../../api/papers'

const route = useRoute()
const paperId = Number(route.params.paperId)
const data = ref<any>({})
const items = ref<any[]>([])
const statusMap: Record<string, string> = { draft: '草稿', published: '已发布', closed: '已关闭' }
const typeMap: Record<string, string> = { single: '单选', multiple: '多选', judge: '判断', short: '简答' }

onMounted(async () => {
  const res = await getPaper(paperId)
  data.value = res
  items.value = res.items || []
})
</script>

<style scoped>
.page { max-width: 1000px; margin: 0 auto; }
</style>
