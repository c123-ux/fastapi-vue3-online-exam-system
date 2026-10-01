<template>
  <div class="dashboard">
    <el-row :gutter="20">
      <el-col :span="6" v-for="card in cards" :key="card.title">
        <el-card shadow="hover">
          <div class="stat">
            <div class="stat-title">{{ card.title }}</div>
            <div class="stat-value">{{ card.value }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="quick" header="快捷操作" style="margin-top: 20px;">
      <el-space wrap>
        <template v-if="auth.role === 'teacher'">
          <router-link to="/papers/create">
            <el-button type="primary">创建试卷</el-button>
          </router-link>
          <router-link to="/questions">
            <el-button>管理题库</el-button>
          </router-link>
        </template>
        <template v-if="auth.role === 'student'">
          <router-link to="/exams">
            <el-button type="primary">参加考试</el-button>
          </router-link>
        </template>
      </el-space>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()

const cards = [
  { title: '当前身份', value: auth.role === 'teacher' ? '教师' : '学生' },
  { title: '用户名', value: auth.username || '-' },
  { title: '系统状态', value: '正常' },
  { title: '版本', value: '1.0.0' },
]
</script>

<style scoped>
.stat-title { color: #909399; font-size: 14px; }
.stat-value { color: #303133; font-size: 24px; font-weight: 600; margin-top: 8px; }
.quick { margin-top: 20px; }
</style>
