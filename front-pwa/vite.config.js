import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
    plugins: [
        react(),
        VitePWA({
            registerType: 'autoUpdate',
            includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
            manifest: {
                name: 'Käthe-ID Schülerausweis',
                short_name: 'Käthe-ID',
                description: 'Digitaler Schülerausweis mit Offline-Support',
                theme_color: '#1a73e8',
                background_color: '#ffffff',
                display: 'standalone',
                orientation: 'portrait',
                start_url: '/',
                icons: [
                    {
                        src: 'pwa-192x192.png',
                        sizes: '192x192',
                        type: 'image/png',
                        purpose: 'any maskable'
                    },
                    {
                        src: 'pwa-512x512.png',
                        sizes: '512x512',
                        type: 'image/png',
                        purpose: 'any maskable'
                    }
                ]
            },
            workbox: {
                skipWaiting: true,
                clientsClaim: true,
                cleanupOutdatedCaches: true,
                // WICHTIG: Standard-Precaching für App-Dateien deaktivieren,
                // damit wir sie stattdessen via NetworkFirst laden können.
                globPatterns: [],

                // Alle Caching-Strategien manuell definieren
                runtimeCaching: [
                    // 1. App Shell (HTML, JS, CSS) -> Network First!
                    // Versucht immer erst das Netz, nimmt Cache nur als Fallback.
                    {
                        urlPattern: ({ request }) =>
                            request.destination === 'document' ||
                            request.destination === 'script' ||
                            request.destination === 'style' ||
                            request.url.endsWith('index.html'),
                        handler: 'NetworkFirst',
                        options: {
                            cacheName: 'app-shell-v1',
                            networkTimeoutSeconds: 2, // Nach 2s warten -> Cache
                            expiration: {
                                maxEntries: 50,
                                maxAgeSeconds: 60 * 60 * 24 * 30 // 30 Tage
                            }
                        }
                    },
                    // 2. Statische Assets (Bilder, Fonts) -> StaleWhileRevalidate
                    // Cache ist okay, aber aktualisiere im Hintergrund
                    {
                        urlPattern: ({ request }) =>
                            request.destination === 'image' ||
                            request.destination === 'font',
                        handler: 'StaleWhileRevalidate',
                        options: {
                            cacheName: 'assets',
                            expiration: {
                                maxEntries: 60,
                                maxAgeSeconds: 60 * 60 * 24 * 30
                            }
                        }
                    },
                    // 3. User API -> NetworkFirst
                    {
                        urlPattern: /\/pwa\/me$/,
                        handler: 'NetworkFirst',
                        options: {
                            cacheName: 'user-data',
                            expiration: {
                                maxEntries: 1,
                                maxAgeSeconds: 60 * 60 * 24 * 3
                            },
                            networkTimeoutSeconds: 3
                        }
                    },
                    // 4. Schüler-Fotos -> CacheFirst
                    {
                        urlPattern: /\/pwa\/photo\//,
                        handler: 'CacheFirst',
                        options: {
                            cacheName: 'photos',
                            expiration: {
                                maxEntries: 10,
                                maxAgeSeconds: 60 * 60 * 24 * 30
                            }
                        }
                    }
                ]
            }
        })
    ],
    server: {
        port: 5174,
        proxy: {
            '/api': {
                target: 'http://localhost:8000',
                changeOrigin: true,
                rewrite: (path) => path.replace(/^\/api/, '')
            }
        }
    },
    build: {
        outDir: 'dist',
        sourcemap: false
    }
})
