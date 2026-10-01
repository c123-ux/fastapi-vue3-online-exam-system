<template>
  <div class="page">
    <el-card>
      <template #header><h3>创建试卷</h3></template>
      <el-form :model="form" label-width="120px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="时长（分）" required>
          <el-input-number v-model="form.duration_minutes" :min="1" :max="600" />
        </el-form-item>
        <el-form-item label="开始时间" required>
          <el-date-picker v-model="form.start_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
        </el-form-item>
        <el-form-item label="结束时间" required>
          <el-date-picker v-model="form.end_at" type="datetime" value-format="YYYY-MM-DDTHH:mm:ss" />
        </el-form-item>
        <el-form-item label="题目" required>
          <div v-for="(q, idx) in form.questions" :key="q.question_id" class="q-item">
            <div class="q-meta">#{{ idx + 1 }} · 题目ID：{{ q.question_id }} · {{ q.content || ('题目 ' + q.question_id) }}</div>
            <el-input v-model="q.score" type="number" style="width: 140px;" placeholder="分值">
              <template #append>分</template>
            </el-input>
            <el-button type="danger" link @click="form.questions.splice(idx, 1)">移除</el-button>
          </div>
          <div style="margin-top: 8px; display: flex; gap: 8px;">
            <el-select v-model="selectedQuestionId" filterable clearable placeholder="搜索并添加题目" style="width: 420px;">
              <el-option v-for="q in questionOptions" :key="q.id" :label="`${q.id} · ${q.content}`" :value="q.id" />
            </el-select>
            <el-button type="primary" @click="addQuestion">添加</el-button>
          </div>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="saving" @click="onSubmit">保存</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { createPaper } from '../../api/papers'
import { listQuestions } from '../../api/questions'
import { ElMessage } from 'element-plus'

const router = useRouter()
const saving = ref(false)
const selectedQuestionId = ref<number | null>(null)
const questionOptions = ref<any[]>([])
const form = reactive({
  title: '',
  duration_minutes: 60,
  start_at: '',
  end_at: '',
  questions: [] as any[],
})

onMounted(async () => {
  questionOptions.value = (await listQuestions({ page_size: 100 })).items || []
})

function addQuestion() {
  if (!selectedQuestionId.value) return
  const q = questionOptions.value.find(x => x.id === selectedQuestionId.value)
  if (!q) return
  form.questions.push({ question_id: q.id, score: 5, content: q.content })
  selectedQuestionId.value = null
}

async function onSubmit() {
  if (!form.title || !form.start_at || !form.end_at || !form.questions.length) {
    ElMessage.warning('请填写完整试卷信息并至少添加一题')
    return
  }
  saving.value = true
  try {
    const paper = await createPaper({
      title: form.title,
      duration_minutes: form.duration_minutes,
      start_at: form.start_at,
      end_at: form.end_at,
      questions: form.questions.map((q) => ({ question_id: q.question_id, score: Number(q.score) })),
    })
    ElMessage.success('创建成功')
    router.push(`/papers/${paper.id}`)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.page { max-width: 1000px; margin: 0 auto; }
.q-item { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
.q-meta { flex: 1; color: #606266; }
</style>
