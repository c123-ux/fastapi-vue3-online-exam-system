declare module 'element-plus/dist/locale/zh-cn.mjs' {
  const locale: any
  export default locale
}

declare module 'element-plus' {
  const ElMessage: any
  const ElMessageBox: any
  const ElConfigProvider: any
  export { ElMessage, ElMessageBox, ElConfigProvider }
}
