<template>
  <el-config-provider :locale="zhCn">
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAuthStore } from './stores/auth'
import { initCsrf } from './utils/csrf'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'

const auth = useAuthStore()

onMounted(async () => {
  initCsrf()
  await auth.refresh()
})
</script>

<style>
html, body, #app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', Arial, sans-serif;
  background: #f5f7fa;
}
#app {
  height: 100%;
}
</style>
