import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useAuth } from '../context/AuthContext'
import { Alert } from '../components/ui'

export default function Setup() {
  const { refresh } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({ username: '', password: '', confirm: '', full_name: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (form.password !== form.confirm) {
      setError('Passwords do not match')
      return
    }
    setBusy(true)
    try {
      await api.post('/api/auth/setup', {
        username: form.username,
        password: form.password,
        full_name: form.full_name || form.username,
      })
      await refresh()
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Setup failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="brand-row">
          <span className="brand-mark">Rx</span>
          <div>
            <strong>Initial Administrator Setup</strong>
            <div className="muted" style={{ fontSize: 12 }}>Create the first admin account (one-time)</div>
          </div>
        </div>
        {error && <Alert kind="error">{error}</Alert>}
        <form onSubmit={submit}>
          <label className="field">
            <span className="field-label">Username</span>
            <input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              pattern="[a-zA-Z0-9_.\-]{3,80}"
              required
            />
          </label>
          <label className="field">
            <span className="field-label">Full name</span>
            <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          </label>
          <label className="field">
            <span className="field-label">Password (min 8 chars)</span>
            <input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              minLength={8}
              required
            />
          </label>
          <label className="field">
            <span className="field-label">Confirm password</span>
            <input
              type="password"
              value={form.confirm}
              onChange={(e) => setForm({ ...form, confirm: e.target.value })}
              minLength={8}
              required
            />
          </label>
          <button className="btn btn-block" disabled={busy}>
            {busy ? 'Creating…' : 'Create administrator'}
          </button>
        </form>
      </div>
    </div>
  )
}
