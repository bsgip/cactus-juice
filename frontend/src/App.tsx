import { Navigate, Route, Routes } from 'react-router-dom'

import { AppLayout } from './layout/AppLayout'
import { ConfigPage } from './pages/ConfigPage'
import { TrocaConfigPage } from './pages/TrocaConfigPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/config" replace />} />
        <Route path="/config" element={<ConfigPage />} />
        <Route path="/troca-config" element={<TrocaConfigPage />} />
      </Route>
    </Routes>
  )
}
