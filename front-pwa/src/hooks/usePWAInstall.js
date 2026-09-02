/**
 * usePWAInstall Hook
 * 
 * Ermöglicht die Installation der PWA als native App.
 * 
 * Plattform-Unterschiede:
 * - Android/Chrome: Fängt 'beforeinstallprompt' Event ab und bietet promptInstall()
 * - iOS/Safari: Erkennt iOS und zeigt Anleitung (Apple erlaubt keinen programmatischen Install)
 * - Desktop: Funktioniert wie Android falls Browser es unterstützt
 * 
 * Usage:
 *   const { deferredPrompt, isIOS, isStandalone, promptInstall } = usePWAInstall();
 */

import { useState, useEffect } from 'react';

export const usePWAInstall = () => {
    // Das abgefangene Install-Event (nur Android/Chrome)
    const [deferredPrompt, setDeferredPrompt] = useState(null);

    // Ist das Gerät ein iOS-Gerät? (iPhone, iPad, iPod)
    const [isIOS, setIsIOS] = useState(false);

    // Läuft die App bereits als installierte PWA?
    const [isStandalone, setIsStandalone] = useState(false);

    useEffect(() => {
        // 1. Check ob App schon installiert ist (läuft im Standalone-Modus)
        // Das funktioniert sowohl für Android als auch iOS
        const isStandaloneMode =
            window.matchMedia('(display-mode: standalone)').matches ||
            window.navigator.standalone === true;  // iOS-spezifisch

        if (isStandaloneMode) {
            setIsStandalone(true);
            return; // Keine weiteren Checks nötig
        }

        // 2. iOS Erkennung
        // Wichtig: Muss vor dem Event-Listener passieren
        const userAgent = window.navigator.userAgent.toLowerCase();
        const isIosDevice = /iphone|ipad|ipod/.test(userAgent);
        setIsIOS(isIosDevice);

        // 3. Android/Chrome: 'beforeinstallprompt' Event abfangen
        // Dieses Event wird gefeuert wenn die PWA installierbar ist
        const handleBeforeInstallPrompt = (e) => {
            // Standard-Banner des Browsers unterdrücken
            e.preventDefault();
            // Event speichern für späteren Aufruf
            setDeferredPrompt(e);
        };

        window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);

        // 4. Cleanup
        return () => {
            window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
        };
    }, []);

    /**
     * Löst den nativen Install-Prompt aus (nur Android/Chrome)
     * 
     * Ruft das gespeicherte 'beforeinstallprompt' Event auf.
     * Nach erfolgreicher Installation wird deferredPrompt auf null gesetzt.
     */
    const promptInstall = async () => {
        if (!deferredPrompt) {
            console.warn('promptInstall() aufgerufen, aber kein Install-Event vorhanden');
            return;
        }

        // Nativen Install-Dialog zeigen
        deferredPrompt.prompt();

        // Auf User-Entscheidung warten
        const { outcome } = await deferredPrompt.userChoice;

        if (outcome === 'accepted') {
            console.log('PWA wurde installiert!');
            setDeferredPrompt(null);
        } else {
            console.log('PWA Installation abgelehnt');
        }
    };

    return {
        deferredPrompt,   // Truthy wenn Install möglich (Android/Chrome)
        isIOS,            // True wenn iOS-Gerät
        isStandalone,     // True wenn App bereits installiert
        promptInstall     // Funktion zum Auslösen des Install-Prompts
    };
};
