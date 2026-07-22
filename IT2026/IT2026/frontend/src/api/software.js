import { softwareRequest } from './request'

export function getSoftwarePackages(params) {
  return softwareRequest({
    url: '/software/packages',
    method: 'get',
    params
  })
}

export function uploadSoftwarePackage(formData) {
  return softwareRequest({
    url: '/software/packages/upload',
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    },
    timeout: 300000
  })
}

export function getSoftwarePackageDetail(packageId) {
  return softwareRequest({
    url: `/software/packages/${packageId}`,
    method: 'get'
  })
}

export function updateSoftwarePackage(packageId, data) {
  return softwareRequest({
    url: `/software/packages/${packageId}`,
    method: 'put',
    data
  })
}

export function deleteSoftwarePackage(packageId) {
  return softwareRequest({
    url: `/software/packages/${packageId}`,
    method: 'delete'
  })
}

export function downloadSoftwarePackage(packageId) {
  return softwareRequest({
    url: `/software/packages/download/${packageId}`,
    method: 'get',
    responseType: 'blob'
  })
}

export function getSoftwarePackageCategories() {
  return softwareRequest({
    url: '/software/packages/categories',
    method: 'get'
  })
}

export function getSoftwarePackageStats() {
  return softwareRequest({
    url: '/software/packages/stats',
    method: 'get'
  })
}

export function createSoftwareTask(data) {
  return softwareRequest({
    url: '/software/tasks',
    method: 'post',
    data
  })
}

export function getSoftwareTasks(params) {
  return softwareRequest({
    url: '/software/tasks',
    method: 'get',
    params
  })
}

export function getSoftwareTaskStats(params) {
  return softwareRequest({
    url: '/software/tasks/stats',
    method: 'get',
    params
  })
}

export function getSoftwareTaskDetail(taskId) {
  return softwareRequest({
    url: `/software/tasks/${taskId}`,
    method: 'get'
  })
}

export function deleteSoftwareTask(taskId) {
  return softwareRequest({
    url: `/software/tasks/${taskId}`,
    method: 'delete'
  })
}

export function cancelSoftwareTask(taskId) {
  return softwareRequest({
    url: `/software/tasks/${taskId}/cancel`,
    method: 'put'
  })
}

export function retrySoftwareTask(taskId) {
  return softwareRequest({
    url: `/software/tasks/${taskId}/retry`,
    method: 'put'
  })
}

export function getComplianceChecks(params) {
  return softwareRequest({
    url: '/software/compliance/checks',
    method: 'get',
    params
  })
}

export function createComplianceCheck(data) {
  return softwareRequest({
    url: '/software/compliance/checks',
    method: 'post',
    data
  })
}

export function updateComplianceCheck(checkId, data) {
  return softwareRequest({
    url: `/software/compliance/checks/${checkId}`,
    method: 'put',
    data
  })
}

export function deleteComplianceCheck(checkId) {
  return softwareRequest({
    url: `/software/compliance/checks/${checkId}`,
    method: 'delete'
  })
}

export function getComplianceResults(params) {
  return softwareRequest({
    url: '/software/compliance/results',
    method: 'get',
    params
  })
}

export function getComplianceStats(params) {
  return softwareRequest({
    url: '/software/compliance/stats',
    method: 'get',
    params
  })
}

export function exportComplianceResults(params) {
  return softwareRequest({
    url: '/software/compliance/results/export',
    method: 'get',
    params,
    responseType: 'blob'
  })
}

export function triggerComplianceScan(data) {
  return softwareRequest({
    url: '/software/compliance/scan',
    method: 'post',
    data
  })
}
