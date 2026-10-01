import http from './axios'

export interface LoginPayload { username: string; password: string }
export interface RegisterPayload { username: string; password: string; role: 'teacher' | 'student'; full_name?: string; teacher_code?: string }

export const login = (payload: LoginPayload) => http.post('/auth/token', payload).then(r => r.data)
export const register = (payload: RegisterPayload) => http.post('/auth/register', payload).then(r => r.data)
export const getMe = () => http.get('/auth/me').then(r => r.data)
