import http from './axios'

export const listMyRecords = (params: { page?: number; page_size?: number }) => http.get('/exams', { params }).then(r => r.data)
export const startExam = (paperId: number) => http.post(`/exams/${paperId}/start`).then(r => r.data)
export const resumeExam = (recordId: number) => http.get(`/exams/${recordId}`).then(r => r.data)
export const saveAnswer = (recordId: number, question_id: number, answer: any) => http.post(`/exams/${recordId}/answer`, { question_id, answer }).then(r => r.data)
export const submitExam = (recordId: number) => http.post(`/exams/${recordId}/submit`).then(r => r.data)
export const getScore = (recordId: number) => http.get(`/exams/${recordId}/score`).then(r => r.data)
export const getAnswers = (recordId: number) => http.get(`/exams/${recordId}/answers`).then(r => r.data)
export const reviewAnswer = (recordId: number, payload: { question_id: number; score: number; comment?: string }) => http.post(`/exams/${recordId}/review`, payload).then(r => r.data)
export const cheatReport = (recordId: number) => http.post(`/exams/${recordId}/cheat-report`).then(r => r.data)
