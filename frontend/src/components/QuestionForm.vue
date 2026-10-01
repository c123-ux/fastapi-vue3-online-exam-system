<template>
  <el-form :model="form" label-width="100px">
    <el-form-item label="题型" required>
      <el-select v-model="form.type">
        <el-option label="单选" value="single" />
        <el-option label="多选" value="multiple" />
        <el-option label="判断" value="judge" />
        <el-option label="简答" value="short" />
      </el-select>
    </el-form-item>
    <el-form-item label="题干" required>
      <el-input v-model="form.content" type="textarea" :rows="3" />
    </el-form-item>
    <el-form-item v-if="form.type === 'single' || form.type === 'multiple'" label="选项">
      <div v-for="(opt, idx) in form.options" :key="idx" style="display:flex; gap:8px; margin-bottom:8px;">
        <el-input v-model="form.options[idx]" style="width: 260px;" placeholder="选项内容" />
        <el-button @click="form.options.splice(idx, 1)">删除</el-button>
      </div>
      <el-button @click="form.options.push('')">添加选项</el-button>
    </el-form-item>
    <el-form-item label="正确答案">
      <el-input v-model="form.correct_answer" placeholder="单选填 A/B/C/D，多选填 A,B，判断填 T/F" />
    </el-form-item>
    <el-form-item label="难度">
      <el-select v-model="form.difficulty">
        <el-option label="简单" value="easy" />
        <el-option label="中等" value="medium" />
        <el-option label="困难" value="hard" />
      </el-select>
    </el-form-item>
    <el-form-item>
      <el-button type="primary" @click="onSubmit">提交</el-button>
      <el-button @click="$emit('cancel')">取消</el-button>
    </el-form-item>
  </el-form>
</template>

<script setup lang="ts">
import { reactive, watch, onMounted } from 'vue'
import { createQuestion, updateQuestion } from '../api/questions'
import { ElMessage } from 'element-plus'

const props = defineProps<{ initial?: any }>()
const emit = defineEmits<{ submit: []; cancel: [] }>()

const form = reactive({
  type: 'single',
  content: '',
  options: ['', ''],
  correct_answer: '',
  difficulty: 'medium',
})

function reset() {
  if (props.initial) {
    form.type = props.initial.type || 'single'
    form.content = props.initial.content || ''
    form.options = Array.isArray(props.initial.options) ? [...props.initial.options] : ['', '']
    form.correct_answer = props.initial.correct_answer || ''
    form.difficulty = props.initial.difficulty || 'medium'
  } else {
    form.type = 'single'
    form.content = ''
    form.options = ['', '']
    form.correct_answer = ''
    form.difficulty = 'medium'
  }
}

watch(() => form.type, (val) => {
  if (val === 'single' || val === 'multiple') {
    if (!form.options?.length) form.options = ['', '']
  } else {
    form.options = []
  }
})

async function onSubmit() {
  try {
    if (props.initial?.id) {
      await updateQuestion(props.initial.id, { ...form })
      ElMessage.success('更新成功')
    } else {
      await createQuestion({ ...form })
      ElMessage.success('创建成功')
    }
    emit('submit')
  } catch (e: any) {
    // handled by interceptor
  }
}

onMounted(() => {
  reset()
})
</script>
