<template>
  <div class="register-page">
    <el-card class="register-card">
      <template #header>
        <h2>注册账号</h2>
      </template>
      <el-form :model="form" :rules="rules" ref="formRef" @submit.prevent="onSubmit">
        <el-form-item prop="username">
          <el-input v-model="form.username" placeholder="用户名（3-50位字母数字下划线）" prefix-icon="User" />
        </el-form-item>
        <el-form-item prop="full_name">
          <el-input v-model="form.full_name" placeholder="姓名（可选）" prefix-icon="UserFilled" />
        </el-form-item>
        <el-form-item prop="password">
          <el-input v-model="form.password" type="password" placeholder="密码（8-64位）" prefix-icon="Lock" show-password />
        </el-form-item>
        <el-form-item prop="role">
          <el-select v-model="form.role" placeholder="选择角色" style="width: 100%">
            <el-option label="学生" value="student" />
            <el-option label="教师" value="teacher" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="form.role === 'teacher'" prop="teacher_code">
          <el-input v-model="form.teacher_code" placeholder="教师注册码" prefix-icon="Key" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" native-type="submit" style="width: 100%">注册</el-button>
        </el-form-item>
        <div class="footer">
          <span>已有账号？</span>
          <router-link to="/login">去登录</router-link>
        </div>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { ElMessage } from 'element-plus'

const router = useRouter()
const auth = useAuthStore()

const formRef = ref()
const loading = ref(false)
const form = reactive({
  username: '',
  password: '',
  role: 'student' as 'student' | 'teacher',
  full_name: '',
  teacher_code: '',
})

watch(() => form.role, (val) => {
  if (val !== 'teacher') form.teacher_code = ''
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }],
  teacher_code: [{ required: true, message: '请输入教师注册码', trigger: 'blur' }],
}

async function onSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  loading.value = true
  try {
    await auth.register(form)
    ElMessage.success('注册成功，请登录')
    router.push('/login')
  } catch (e: any) {
    // handled by interceptor
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.register-page {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}
.register-card {
  width: 420px;
}
.footer {
  display: flex;
  justify-content: center;
  gap: 8px;
  font-size: 14px;
  color: #606266;
}
</style>
