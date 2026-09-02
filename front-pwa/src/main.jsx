import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// PWA Service Worker registrieren
import { registerSW } from 'virtual:pwa-register'

// Auto-Update aktivieren
registerSW({
    onNeedRefresh() {
        // Neues Update verfügbar
        if (confirm('Neue Version verfügbar. Jetzt aktualisieren?')) {
            window.location.reload()
        }
    },
    onOfflineReady() {
        console.log('App bereit für Offline-Nutzung')
    }
})

ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
        <App />
    </React.StrictMode>,
)
