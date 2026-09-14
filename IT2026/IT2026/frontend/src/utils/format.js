// 容量自适应格式化（输入 MB 数值）
// 规则：>=1024MB 显示 GB，>=1MB 显示 MB，<1MB 显示 KB；非法值/0 显示 '-'
export function formatSizeMB(sizeMb) {
  if (sizeMb === null || sizeMb === undefined || sizeMb === '' || isNaN(Number(sizeMb))) return '-'
  const mb = Number(sizeMb)
  if (mb <= 0) return '-'
  if (mb >= 1024) return (mb / 1024).toFixed(mb / 1024 >= 100 ? 0 : 1) + ' GB'
  if (mb >= 1) return (mb >= 100 ? mb.toFixed(0) : mb.toFixed(1)) + ' MB'
  return (mb * 1024).toFixed(0) + ' KB'
}

// 通用字节格式化（输入字节数），用于任意来源的容量显示
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || bytes === '' || isNaN(Number(bytes))) return '-'
  const b = Number(bytes)
  if (b <= 0) return '-'
  if (b >= 1024 * 1024 * 1024) return (b / 1024 / 1024 / 1024).toFixed(2) + ' GB'
  if (b >= 1024 * 1024) return (b / 1024 / 1024).toFixed(1) + ' MB'
  if (b >= 1024) return (b / 1024).toFixed(0) + ' KB'
  return b.toFixed(0) + ' B'
}
