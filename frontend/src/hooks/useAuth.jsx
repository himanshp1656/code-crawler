import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from '../lib/api'

const AuthContext = createContext(null)

// user states:
//   undefined  → still loading from server
//   null       → not logged in
//   { ... }    → logged-in user object

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined)

  const reload = useCallback(async () => {
    try {
      const u = await api.me()
      setUser(u)
    } catch {
      setUser(null)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const login = async (username, password) => {
    const u = await api.login(username, password)
    setUser(u)
    return u
  }

  const logout = async () => {
    await api.logout()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, login, logout, reload }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
