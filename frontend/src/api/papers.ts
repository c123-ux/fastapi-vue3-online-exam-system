import http from './axios'

export const listPapers = () => http.get('/papers').then(r => r.data)
export const getPaper = (id: number) => http.get(`/papers/${id}`).then(r => r.data)
export const createPaper = (data: any) => http.post('/papers', data).then(r => r.data)
export const publishPaper = (id: number) => http.post(`/papers/${id}/publish`).then(r => r.data)
export const closePaper = (id: number) => http.post(`/papers/${id}/close`).then(r => r.data)
export const getPaperResults = (id: number) => http.get(`/papers/${id}/results`).then(r => r.data)
export const getPaperStats = (id: number) => http.get(`/papers/${id}/stats`).then(r => r.data)
