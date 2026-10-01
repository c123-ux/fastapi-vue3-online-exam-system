<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">在线考试系统</div>
      <el-menu
        :default-active="route.path"
        router
        background-color="#304156"
        text-color="#bfcbd9"
        active-text-color="#409eff"
      >
        <el-menu-item index="/dashboard">
          <el-icon><HomeFilled /></el-icon>
          <span>工作台</span>
        </el-menu-item>

        <template v-if="auth.role === 'student'">
          <el-menu-item index="/exams">
            <el-icon><Document /></el-icon>
            <span>我的考试</span>
          </el-menu-item>
        </template>

        <template v-if="auth.role === 'teacher'">
          <el-menu-item index="/papers">
            <el-icon><Notebook /></el-icon>
            <span>试卷管理</span>
          </el-menu-item>
          <el-menu-item index="/questions">
            <el-icon><Collection /></el-icon>
            <span>题库管理</span>
          </el-menu-item>
        </template>

        <el-menu-item @click="auth.logout">
          <el-icon><SwitchButton /></el-icon>
          <span>退出登录</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="breadcrumb">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/dashboard' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="route.meta.title">{{ route.meta.title }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>
        <div class="userinfo">
          <el-tag>{{ auth.role === 'teacher' ? '教师' : '学生' }}</el-tag>
          <span class="username">{{ auth.username }}</span>
        </div>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const auth = useAuthStore()
</script>

<style scoped>
.layout { height: 100vh; }
.aside { background: #304156; color: #fff; }
.logo { height: 56px; line-height: 56px; text-align: center; font-size: 18px; color: #fff; font-weight: 600; }
.header { display: flex; align-items: center; justify-content: space-between; background: #fff; border-bottom: 1px solid #e6e6e6; }
.breadcrumb { margin-left: 20px; }
.userinfo { display: flex; align-items: center; gap: 12px; margin-right: 20px; }
.main { background: #f5f7fa; padding: 20px; }
</style>
