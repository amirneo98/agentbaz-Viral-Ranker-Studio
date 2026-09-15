import { useEffect, useRef } from 'react'
import request from '../api/client'
import { useStudioStore } from '../store/useStudioStore'

/**
 * Polls GET /api/tasks/{id}/ every second while the task is active
 * (PENDING | PROCESSING). Each response is pushed into the store via
 * syncTask; polling stops on SUCCESS / FAILED (or error). Cleans up on unmount.
 */
export default function useTaskPolling(taskId, active = true) {
  const syncTask = useStudioStore((s) => s.syncTask)
  const busyRef = useRef(false)

  useEffect(() => {
    if (!taskId || !active) return undefined

    let cancelled = false
    let timer = null

    const poll = async () => {
      if (busyRef.current) return
      busyRef.current = true
      try {
        const data = await request(`/api/tasks/${taskId}/`)
        if (cancelled) return
        syncTask(taskId, data)
        if (data.status === 'SUCCESS' || data.status === 'FAILED') return
      } catch (err) {
        if (cancelled) return
        syncTask(taskId, { status: 'FAILED', progress: 0, error: err.message })
        return
      } finally {
        busyRef.current = false
      }
      timer = setTimeout(poll, 1000)
    }

    poll()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [taskId, active, syncTask])
}
