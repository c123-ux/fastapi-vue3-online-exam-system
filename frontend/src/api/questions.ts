import http from './axios'

export const listCategories = () => http.get('/categories').then(r => r.data)
export const createCategory = (name: string) => http.post('/categories', { name }).then(r => r.data)
export const listQuestions = (params?: any) => http.get('/questions', { params }).then(r => r.data)
export const getQuestion = (id: number) => http.get(`/questions/${id}`).then(r => r.data)
export const createQuestion = (data: any) => http.post('/questions', data).then(r => r.data)
export const updateQuestion = (id: number, data: any) => http.put(`/questions/${id}`, data).then(r => r.data)
export const deleteQuestion = (id: number) => http.delete(`/questions/${id}`).then(r => r.data)
