/**
 * InstallButton Komponente
 * 
 * Zeigt einen "App installieren" Button an, der je nach Plattform funktioniert:
 * - Android/Chrome: Löst den nativen Install-Prompt aus
 * - iOS/Safari: Zeigt eine Anleitung zum manuellen Hinzufügen zum Home-Bildschirm
 * 
 * Wird automatisch ausgeblendet wenn:
 * - Die App bereits als PWA installiert läuft
 * - Der Browser PWA-Installation nicht unterstützt (z.B. Firefox Desktop)
 */

import { useState } from 'react';
import { usePWAInstall } from '../hooks/usePWAInstall';
import './InstallButton.css';

export default function InstallButton() {
    const { deferredPrompt, isIOS, isStandalone, promptInstall } = usePWAInstall();

    // State für iOS-Anleitung Modal
    const [showIOSHint, setShowIOSHint] = useState(false);

    // App ist bereits installiert -> Button nicht anzeigen
    if (isStandalone) {
        return null;
    }

    // ===== FALL A: Android / Desktop Chrome =====
    // Wir haben das beforeinstallprompt Event -> Direkter Install möglich
    if (deferredPrompt) {
        return (
            <button
                onClick={promptInstall}
                className="install-button install-button-primary"
            >
                <span className="install-icon">📲</span>
                App installieren
            </button>
        );
    }

    // ===== FALL B: iOS / Safari =====
    // Kein programmatischer Install möglich -> Anleitung zeigen
    if (isIOS) {
        return (
            <>
                <button
                    onClick={() => setShowIOSHint(true)}
                    className="install-button install-button-ios"
                >
                    <span className="install-icon">📲</span>
                    Zum Home-Bildschirm
                </button>

                {/* iOS Anleitung Modal */}
                {showIOSHint && (
                    <div
                        className="ios-hint-overlay"
                        onClick={() => setShowIOSHint(false)}
                    >
                        <div
                            className="ios-hint-card"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <h3 className="ios-hint-title">
                                So installierst du die App:
                            </h3>

                            <ol className="ios-hint-steps">
                                <li>
                                    Tippe unten auf das <strong>Teilen-Symbol</strong>
                                    <span className="share-icon">⬆️</span>
                                </li>
                                <li>
                                    Scrolle im Menü etwas nach unten
                                </li>
                                <li>
                                    Wähle <strong>"Zum Home-Bildschirm"</strong>
                                </li>
                            </ol>

                            <button
                                className="ios-hint-close"
                                onClick={() => setShowIOSHint(false)}
                            >
                                Verstanden
                            </button>

                            {/* Pfeil der auf den Safari-Share-Button zeigt */}
                            <div className="ios-hint-arrow">⬇️</div>
                        </div>
                    </div>
                )}
            </>
        );
    }

    // ===== FALL C: Browser ohne PWA-Support =====
    // z.B. Firefox Desktop, alte Browser -> Nichts anzeigen
    return null;
}
