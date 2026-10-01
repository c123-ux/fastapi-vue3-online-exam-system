import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { login as apiLogin, register as apiRegister, getMe } from '../api/auth'
import type { LoginPayload, RegisterPayload } from '../api/auth'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('token') || '')
  const user = ref<any>(null)

  const role = computed(() => user.value?.role)
  const username = computed(() => user.value?.username)
  const isLoggedIn = computed(() => !!token.value)

  async function login(payload: LoginPayload) {
    const data = await apiLogin(payload)
    token.value = data.access_token
    user.value = { role: data.role, username: payload.username }
    localStorage.setItem('token', data.access_token)
  }

  async function register(payload: RegisterPayload) {
    await apiRegister(payload)
  }

  async function refresh() {
    if (!token.value) return
    try {
      const me = await getMe()
      user.value = me
    } catch {
      logout()
    }
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
  }

  return { token, user, role, username, isLoggedIn, login, register, refresh, logout }
})
