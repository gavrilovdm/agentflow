import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { isLive } from './api'

const LiveCtx = createContext(false)

export function LiveProvider({ children }: { children: ReactNode }) {
  const [live, setLive] = useState(false)
  useEffect(() => {
    let alive = true
    const check = () => isLive().then((v) => alive && setLive(v))
    void check()
    const t = setInterval(check, 15000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])
  return <LiveCtx.Provider value={live}>{children}</LiveCtx.Provider>
}

export const useLive = () => useContext(LiveCtx)
