import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { Alert, Field } from '../components/ui'

export default function ChangePassword() {
  const { user, refresh, logout } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ current: '', next: '', confirm: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (form.next !== form.confirm) {
      setError('New password and confirmation do not match.')
      return
    }
    setBusy(true)
    try {
      await api.post('/api/auth/change-password', {
        current_password: form.current,
        new_password: form.next,
      })
      await refresh()
      navigate('/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError('Current password is incorrect.')
      } else {
        setError(err instanceof Error ? err.message : 'Failed to change password')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>Change your password</h1>
        {user?.must_change_password && (
          <Alert kind="warning">
            Your account was created with a bootstrap password. You must choose a new password before
            using the application.
          </Alert>
        )}
        {error && <Alert kind="error">{error}</Alert>}
        <form onSubmit={submit}>
          <Field label="Current password">
            <input
              type="password"
              autoComplete="current-password"
              value={form.current}
              onChange={(e) => setForm({ ...form, current: e.target.value })}
              required
            />
          </Field>
          <Field label="New password" hint="Minimum 8 characters, must differ from the current password and username.">
            <input
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={form.next}
              onChange={(e) => setForm({ ...form, next: e.target.value })}
              required
            />
          </Field>
          <Field label="Confirm new password">
            <input
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={form.confirm}
              onChange={(e) => setForm({ ...form, confirm: e.target.value })}
              required
            />
          </Field>
          <div className="btn-row">
            <button className="btn" disabled={busy}>{busy ? 'Saving…' : 'Set new password'}</button>
            <button type="button" className="btn btn-ghost" onClick={() => void logout()} disabled={busy}>
              Log out
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
