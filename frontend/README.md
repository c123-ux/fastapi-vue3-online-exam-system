# 在线考试系统 · 前端

Vue 3 + Vite + Element Plus 实现的教师/学生双端页面。

## 启动

```powershell
cd "D:\全栈开发\项目三实战在线考试系统\frontend"
npm install
npm run dev
```

打开 http://localhost:5173 ，API 已通过 Vite proxy 转发到 http://127.0.0.1:8000/api 。

## 账号

- 教师：注册时选择“教师”并输入注册码 `TC0DE-EXAM`
- 学生：注册时选择“学生”

## 页面

- `/login` 登录
- `/register` 注册
- `/dashboard` 工作台
- 学生：`/exams`、`/exams/:recordId/take`、`/exams/:recordId/score`
- 教师：`/papers`、`/papers/create`、`/papers/:paperId`、`/papers/:paperId/stats`、`/questions`、`/records/:recordId/review`
