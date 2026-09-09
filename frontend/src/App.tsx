import { Navigate, Route, Routes } from 'react-router-dom'

import { AppLayout } from './layout/AppLayout'
import { ConfigPage } from './pages/ConfigPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/config" replace />} />
        <Route path="/config" element={<ConfigPage />} />
      </Route>
    </Routes>
  )
}
