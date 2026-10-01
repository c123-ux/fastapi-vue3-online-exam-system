<template>
  <div class="page">
    <el-card>
      <template #header>
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <h3>我的试卷</h3>
          <router-link to="/papers/create"><el-button type="primary">创建试卷</el-button></router-link>
        </div>
      </template>
      <el-table :data="papers" style="width: 100%">
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="status" label="状态">
          <template #default="{ row }">{{ statusMap[row.status] }}</template>
        </el-table-column>
        <el-table-column prop="duration_minutes" label="时长（分）" width="120" />
        <el-table-column prop="start_at" label="开始时间" width="180" />
        <el-table-column prop="end_at" label="结束时间" width="180" />
        <el-table-column label="操作" width="260">
          <template #default="{ row }">
            <router-link :to="`/papers/${row.id}`"><el-button type="primary" link>详情</el-button></router-link>
            <el-button v-if="row.status === 'draft'" type="success" link @click="publish(row.id)">发布</el-button>
            <el-button v-if="row.status === 'published'" type="warning" link @click="close(row.id)">关闭</el-button>
            <router-link :to="`/papers/${row.id}/stats`"><el-button type="primary" link>统计</el-button></router-link>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { listPapers, publishPaper, closePaper } from '../../api/papers'
import { ElMessage } from 'element-plus'

const papers = ref<any[]>([])
const statusMap: Record<string, string> = { draft: '草稿', published: '已发布', closed: '已关闭' }

onMounted(async () => {
  papers.value = await listPapers()
})

async function publish(id: number) {
  await publishPaper(id)
  ElMessage.success('已发布')
  papers.value = await listPapers()
}

async function close(id: number) {
  await closePaper(id)
  ElMessage.success('已关闭')
  papers.value = await listPapers()
}
</script>

<style scoped>
.page { max-width: 1200px; margin: 0 auto; }
</style>
