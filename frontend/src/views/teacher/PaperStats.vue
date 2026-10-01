<template>
  <div class="page">
    <el-card>
      <template #header><h3>考试统计</h3></template>
      <div v-if="!data.paper_id">加载中...</div>
      <div v-else>
        <el-row :gutter="20" style="margin-bottom: 20px;">
          <el-col :span="6" v-for="item in summaryCards" :key="item.title">
            <el-card shadow="hover">
              <div class="stat-title">{{ item.title }}</div>
              <div class="stat-value">{{ item.value }}</div>
            </el-card>
          </el-col>
        </el-row>
        <el-card header="成绩列表">
          <el-table :data="data.rows || []" style="width: 100%">
            <el-table-column prop="username" label="用户名" />
            <el-table-column prop="full_name" label="姓名" />
            <el-table-column prop="status" label="状态" width="120" />
            <el-table-column prop="submit_kind" label="交卷方式" width="120" />
            <el-table-column prop="earned_score" label="得分" width="120" />
            <el-table-column prop="cheat_count" label="切屏次数" width="120" />
          </el-table>
        </el-card>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import { getPaperStats } from '../../api/stats'

const route = useRoute()
const paperId = Number(route.params.paperId)
const data = ref<any>({})

const summaryCards = computed(() => [
  { title: '已交卷', value: data.value.finished_count || 0 },
  { title: '超时交卷', value: data.value.timeout_count || 0 },
  { title: '已定稿', value: data.value.graded_count || 0 },
  { title: '待批改', value: data.value.pending_review_count || 0 },
  { title: '平均分', value: data.value.avg_score ?? '-' },
  { title: '最高分', value: data.value.max_score ?? '-' },
  { title: '最低分', value: data.value.min_score ?? '-' },
])

onMounted(async () => {
  data.value = await getPaperStats(paperId)
})
</script>

<style scoped>
.page { max-width: 1200px; margin: 0 auto; }
.stat-title { color: #606266; font-size: 14px; }
.stat-value { color: #303133; font-size: 22px; font-weight: 700; margin-top: 6px; }
</style>
