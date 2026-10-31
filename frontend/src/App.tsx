import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './context/AuthContext'
import Layout from './components/Layout'
import { Spinner } from './components/ui'
import Login from './pages/Login'
import Setup from './pages/Setup'
import Dashboard from './pages/Dashboard'
import Scan from './pages/Scan'
import VisionScan from './pages/VisionScan'
import Medicines from './pages/Medicines'
import MedicineForm from './pages/MedicineForm'
import Counting from './pages/Counting'
import Inventory from './pages/Inventory'
import Finance from './pages/Finance'
import Audits from './pages/Audits'
import Reports from './pages/Reports'
import Users from './pages/Users'
import AuditLogs from './pages/AuditLogs'
import ChangePassword from './pages/ChangePassword'

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading, setupAvailable } = useAuth()
  if (loading) return <Spinner label="Checking session…" />
  if (setupAvailable) return <Navigate to="/setup" replace />
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

function MustChangePassword({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (user?.must_change_password) return <Navigate to="/change-password" replace />
  return <>{children}</>
}

function Home() {
  const { user, loading } = useAuth()
  if (loading) return <Spinner />
  if (!user) return <Navigate to="/login" replace />
  return <Navigate to="/" replace />
}

function RoutesInner() {
  const { setupAvailable, user, loading } = useAuth()

  useEffect(() => {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (tz) localStorage.setItem('pharmacy_tz', tz)
  }, [])

  if (loading) return <Spinner label="Loading application…" />

  return (
    <Routes>
      <Route path="/login" element={setupAvailable ? <Navigate to="/setup" replace /> : user ? <Navigate to="/" replace /> : <Login />} />
      <Route path="/setup" element={setupAvailable ? <Setup /> : <Navigate to={user ? '/' : '/login'} replace />} />
      <Route
        path="/"
        element={
          <Protected>
            <MustChangePassword>
              <Layout />
            </MustChangePassword>
          </Protected>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="scan" element={<Scan />} />
        <Route path="vision" element={<VisionScan />} />
        <Route path="medicines" element={<Medicines />} />
        <Route path="medicines/new" element={<MedicineForm />} />
        <Route path="medicines/:id/edit" element={<MedicineForm />} />
        <Route path="counting" element={<Counting />} />
        <Route path="inventory" element={<Inventory />} />
        <Route path="stock-in" element={<Finance mode="stock-in" />} />
        <Route path="sales" element={<Finance mode="sales" />} />
        <Route path="expenses" element={<Finance mode="expenses" />} />
        <Route path="audits" element={<Audits />} />
        <Route path="reports" element={<Reports />} />
        <Route path="users" element={<Users />} />
        <Route path="audit-logs" element={<AuditLogs />} />
      </Route>
      <Route
        path="/change-password"
        element={
          <Protected>
            <ChangePassword />
          </Protected>
        }
      />
      <Route path="*" element={<Home />} />
    </Routes>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <RoutesInner />
      </AuthProvider>
    </BrowserRouter>
  )
}
