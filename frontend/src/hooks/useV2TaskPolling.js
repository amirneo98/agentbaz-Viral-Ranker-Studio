import { useEffect, useRef } from 'react'
import api from '../services/api'

/**
 * Generic task poller for the v1.2 studio. Polls GET /api/tasks/<id>/
 * every 1.5s while active; each response is passed to onData(id, payload).
 * Stops on SUCCESS / FAILED (or network error, reported as FAILED).
 */
export default function useV2TaskPolling(taskId, active, onData, extraKey = null) {
  const onDataRef = useRef(onData)
  onDataRef.current = onData

  useEffect(() => {
    if (!taskId || !active) return undefined
    let cancelled = false
    let timer = null

    const poll = async () => {
      try {
        const data = await api.getTask(taskId)
        if (cancelled) return
        onDataRef.current(taskId, data, extraKey)
        if (data.status === 'SUCCESS' || data.status === 'FAILED') return
      } catch (err) {
        if (!cancelled) onDataRef.current(taskId, { status: 'FAILED', error: err.message }, extraKey)
        return
      }
      timer = setTimeout(poll, 1500)
    }

    poll()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, active])
}
