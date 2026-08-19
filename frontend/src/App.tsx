import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './context/AuthContext'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Accounts from './pages/Accounts'
import Exports from './pages/Exports'
import Migration from './pages/Migration'
import ImportAssistant from './pages/ImportAssistant'
import TestImport from './pages/TestImport'
import ImportJobDetail from './pages/ImportJobDetail'

function RequireAuth({ children }: { children: JSX.Element }): JSX.Element {
  const { token } = useAuth()
  return token ? children : <Navigate to="/login" replace />
}

export default function App(): JSX.Element {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/exports" element={<Exports />} />
        <Route path="/migration" element={<Migration />} />
        <Route path="/import" element={<ImportAssistant />} />
        <Route path="/test-import" element={<TestImport />} />
        <Route path="/import/jobs/:id" element={<ImportJobDetail />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}