<template>
  <div class="page">
    <el-card>
      <template #header>
        <div class="header">
          <div>
            <h3>{{ data.paper_title }}</h3>
            <div class="meta">剩余时间：{{ formatSeconds(data.remaining_seconds) }}</div>
          </div>
          <div class="actions">
            <el-button type="danger" @click="onSubmit">交卷</el-button>
          </div>
        </div>
      </template>

      <el-space direction="vertical" style="width: 100%" size="large">
        <div v-for="item in data.questions" :key="item.question_id" class="question">
          <div class="q-title">
            <span class="sort">{{ item.sort_order }}.</span>
            <span>{{ item.content }}</span>
            <el-tag style="margin-left: 8px;">{{ typeMap[item.type] }}</el-tag>
            <span class="score">（{{ item.score }}分）</span>
          </div>

          <div class="q-body">
            <el-radio-group v-if="item.type === 'single'" v-model="answers[item.question_id]">
              <el-radio v-for="(opt, idx) in parseOptions(item.options)" :key="idx" :label="opt.label">
                {{ opt.label }}. {{ opt.text }}
              </el-radio>
            </el-radio-group>

            <el-checkbox-group v-else-if="item.type === 'multiple'" v-model="answers[item.question_id]">
              <el-checkbox v-for="(opt, idx) in parseOptions(item.options)" :key="idx" :label="opt.label">
                {{ opt.label }}. {{ opt.text }}
              </el-checkbox>
            </el-checkbox-group>

            <el-radio-group v-else-if="item.type === 'judge'" v-model="answers[item.question_id]">
              <el-radio label="T">正确</el-radio>
              <el-radio label="F">错误</el-radio>
            </el-radio-group>

            <el-input v-else type="textarea" :rows="4" v-model="answers[item.question_id]" placeholder="请输入答案" />
          </div>
        </div>
      </el-space>

      <div class="footer">
        <el-button type="primary" @click="saveAll">保存全部</el-button>
        <el-button type="danger" @click="onSubmit">交卷</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { startExam, resumeExam, saveAnswer, submitExam } from '../../api/exams'
import { ElMessage, ElMessageBox } from 'element-plus'

const route = useRoute()
const router = useRouter()
const paperId = Number(route.params.paperId)
const answers = reactive<Record<number, any>>({})

const data = ref<any>({ questions: [], remaining_seconds: 0 })
const timer = ref<number | null>(null)

const typeMap: Record<string, string> = { single: '单选', multiple: '多选', judge: '判断', short: '简答' }

function parseOptions(raw: any) {
  if (!raw) return []
  if (Array.isArray(raw)) return raw.map((t, i) => ({ label: String.fromCharCode(65 + i), text: t }))
  return []
}

function formatSeconds(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}分${s}秒`
}

async function load() {
  try {
    const res = await startExam(paperId)
    data.value = res
    for (const q of res.questions || []) {
      if (res.answers && res.answers[q.question_id]) {
        answers[q.question_id] = q.type === 'multiple' ? res.answers[q.question_id].split(',') : res.answers[q.question_id]
      }
    }
  } catch (e: any) {
    if (e?.response?.data?.code === 'ALREADY_SUBMITTED') {
      router.push('/exams')
    }
  }
}

async function saveAll() {
  const promises: Promise<any>[] = []
  for (const q of data.value.questions || []) {
    if (answers[q.question_id] !== undefined) {
      promises.push(saveAnswer(data.value.record_id, q.question_id, answers[q.question_id]))
    }
  }
  await Promise.all(promises)
  ElMessage.success('已保存')
}

async function onSubmit() {
  await ElMessageBox.confirm('交卷后将不可再修改，确认？', '交卷确认', { type: 'warning' })
  const res = await submitExam(data.value.record_id)
  ElMessage.success(`交卷成功，自动得分：${res.auto_score}`)
  router.push(`/exams/${data.value.record_id}/score`)
}

onMounted(() => {
  load()
  timer.value = window.setInterval(() => {
    if (data.value.remaining_seconds > 0) data.value.remaining_seconds--
  }, 1000)
})

onUnmounted(() => {
  if (timer.value) clearInterval(timer.value)
})
</script>

<style scoped>
.page { max-width: 1000px; margin: 0 auto; }
.header { display: flex; justify-content: space-between; align-items: center; }
.meta { color: #606266; margin-top: 6px; }
.question { border: 1px solid #ebeef5; padding: 16px; border-radius: 8px; }
.q-title { font-size: 16px; color: #303133; }
.sort { font-weight: 600; margin-right: 6px; }
.score { color: #f56c6c; margin-left: 4px; }
.q-body { margin-top: 12px; }
.footer { margin-top: 20px; display: flex; justify-content: flex-end; gap: 12px; }
</style>
