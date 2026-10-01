import http from './axios'

export const getPaperResults = (id: number) => http.get(`/papers/${id}/results`).then(r => r.data)
export const getPaperStats = (id: number) => http.get(`/papers/${id}/stats`).then(r => r.data)
