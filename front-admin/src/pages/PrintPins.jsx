/**
 * PrintPins Page – Klassenbasierter QR-Code-Druck
 *
 * Funktionsweise:
 *   1. Admin wählt eine Klasse — ODER lädt per Knopf ALLE Klassen (Sammeldruck,
 *      z.B. zum Schuljahresanfang).
 *   2. System lädt die Schüler mit PINs (fehlende PINs werden serverseitig erzeugt).
 *   3. Für jede Klasse eine eigene Druck-Sektion mit Klassenname im Titel;
 *      per CSS beginnt jede Klasse auf einer NEUEN Seite (nie 2 Klassen auf 1 Blatt).
 *   4. "Drucken" öffnet den Browser-Druckdialog.
 *
 * QR-Code: kommt von der EIGENEN API (/admin/print/qr.png) als blob:-URL —
 * bewusst kein Drittanbieter: die CSP (img-src 'self' data: blob:) lässt keine
 * externen Bildquellen zu, und externe Dienste haben im Schulkontext nichts im
 * Datenfluss verloren. Der QR-Inhalt ist für alle Karten identisch
 * (PWA-Login-URL der Schule) → EIN Request für alles.
 *
 * Print-Layout: 3 Spalten × n Zeilen; Karten mit break-inside: avoid.
 */

import { useState, useEffect, useRef } from 'react'
import Layout from '../components/Layout'

// Lehrer sind Student-Datensätze in dieser Klasse — beim Sammeldruck zum
// Schuljahresanfang bewusst außen vor (PINs fürs Kollegium laufen über den
// CSV-Serienmail-Export). Einzeln auswählbar bleibt die Klasse trotzdem.
const TEACHER_CLASS = 'Lehrerkollegium'

export default function PrintPins({ user, onLogout, apiUrl }) {
    // Klassenauswahl
    const [classes, setClasses] = useState([])
    const [selectedClassId, setSelectedClassId] = useState('')
    const [loadingClasses, setLoadingClasses] = useState(true)

    // Druckdaten: Liste von Klassen-Sektionen [{ id, name, students: [...] }]
    const [printGroups, setPrintGroups] = useState([])
    const [loadingStudents, setLoadingStudents] = useState(false)
    const [bulkProgress, setBulkProgress] = useState('')   // "Klasse 3/12 …"

    // Schul-Daten für QR-Code-URL
    const [schoolSlug, setSchoolSlug] = useState('')
    const [pwaBaseUrl, setPwaBaseUrl] = useState('')

    // QR-Code (blob:-URL von unserer API; für alle Karten identisch)
    const [qrSrc, setQrSrc] = useState('')

    // Ref für den druckbaren Bereich
    const printAreaRef = useRef(null)

    // Klassen + Schulinfo laden beim Mount
    useEffect(() => {
        fetchClasses()
        fetchSchoolSlug()
    }, [])

    // Login-URL, die im QR steckt (identisch für alle Karten der Schule)
    const loginUrl = `${pwaBaseUrl || import.meta.env.VITE_PWA_URL || 'http://localhost:5174'}${schoolSlug ? '/' + schoolSlug : ''}`

    // QR einmalig von der eigenen API holen (fetch → blob, wegen CSP img-src)
    useEffect(() => {
        let revoke = ''
        const loadQr = async () => {
            try {
                const res = await fetch(
                    `${apiUrl}/admin/print/qr.png?data=${encodeURIComponent(loginUrl)}`,
                    { credentials: 'include' }
                )
                if (!res.ok) return
                const blob = await res.blob()
                revoke = URL.createObjectURL(blob)
                setQrSrc(revoke)
            } catch (err) {
                console.error('QR laden fehlgeschlagen:', err)
            }
        }
        loadQr()
        return () => { if (revoke) URL.revokeObjectURL(revoke) }
    }, [apiUrl, loginUrl])

    const fetchSchoolSlug = async () => {
        try {
            const res = await fetch(
                `${apiUrl}/schools/${user.school_id}`,
                { credentials: 'include' }
            )
            if (res.ok) {
                const data = await res.json()
                setSchoolSlug(data.path_slug || '')
                setPwaBaseUrl(data.pwa_base_url || '')
            }
        } catch (err) {
            console.error('Schulinfo laden fehlgeschlagen:', err)
        }
    }

    const fetchClasses = async () => {
        setLoadingClasses(true)
        try {
            const res = await fetch(
                `${apiUrl}/admin/classes?school_id=${user.school_id}`,
                { credentials: 'include' }
            )
            if (!res.ok) throw new Error('Fehler beim Laden')
            const data = await res.json()
            setClasses(data)
        } catch (err) {
            console.error('Klassen laden fehlgeschlagen:', err)
        } finally {
            setLoadingClasses(false)
        }
    }

    // Druckdaten EINER Klasse laden
    const fetchPrintData = async (classId) => {
        const res = await fetch(
            `${apiUrl}/admin/classes/${classId}/print-data`,
            { credentials: 'include' }
        )
        if (!res.ok) throw new Error('Fehler beim Laden der Druckdaten')
        return res.json()
    }

    // Einzelklassen-Auswahl
    const handleClassChange = async (e) => {
        const classId = e.target.value
        setSelectedClassId(classId)
        setBulkProgress('')

        if (!classId) {
            setPrintGroups([])
            return
        }
        const cls = classes.find(c => String(c.id) === classId)
        setLoadingStudents(true)
        try {
            const students = await fetchPrintData(classId)
            setPrintGroups([{ id: classId, name: cls ? cls.name : '', students }])
        } catch (err) {
            console.error(err)
            alert('Fehler beim Laden der Druckdaten')
            setPrintGroups([])
        } finally {
            setLoadingStudents(false)
        }
    }

    // SAMMELDRUCK: alle Klassen nacheinander laden (ohne Lehrerkollegium)
    const handleLoadAllClasses = async () => {
        const targets = classes.filter(c => c.name !== TEACHER_CLASS && c.student_count > 0)
        if (targets.length === 0) {
            alert('Keine Klassen mit aktiven Schülern gefunden.')
            return
        }
        setSelectedClassId('')
        setLoadingStudents(true)
        setPrintGroups([])
        const groups = []
        const failed = []
        try {
            for (let i = 0; i < targets.length; i++) {
                const cls = targets[i]
                setBulkProgress(`Lade Klasse ${i + 1}/${targets.length} (${cls.name}) …`)
                try {
                    const students = await fetchPrintData(cls.id)
                    if (students.length > 0) {
                        groups.push({ id: cls.id, name: cls.name, students })
                    }
                } catch (err) {
                    console.error(`Klasse ${cls.name}:`, err)
                    failed.push(cls.name)
                }
            }
            setPrintGroups(groups)
            if (failed.length > 0) {
                alert(`Achtung: ${failed.length} Klasse(n) konnten nicht geladen werden und FEHLEN im Druck: ${failed.join(', ')}`)
            }
        } finally {
            setLoadingStudents(false)
            setBulkProgress('')
        }
    }

    // Drucken: Öffnet den Browser-Druckdialog
    const handlePrint = () => {
        window.print()
    }

    const totalCards = printGroups.reduce((n, g) => n + g.students.length, 0)

    return (
        <Layout user={user} onLogout={onLogout}>
            {/* Steuerungsbereich – wird beim Drucken ausgeblendet (via CSS: no-print) */}
            <div className="no-print">
                <div className="page-header">
                    <h1>🖨️ QR-Codes drucken</h1>
                    <p>Login-PINs als QR-Codes pro Klasse ausdrucken</p>
                </div>

                {/* Klassenauswahl + Sammeldruck + Drucken-Button */}
                <div className="card mb-md">
                    <div className="flex gap-md items-center" style={{ flexWrap: 'wrap' }}>
                        <div className="form-group" style={{ marginBottom: 0, minWidth: '250px' }}>
                            <label className="form-label">Klasse auswählen</label>
                            <select
                                className="form-input"
                                value={selectedClassId}
                                onChange={handleClassChange}
                                disabled={loadingClasses || loadingStudents}
                            >
                                <option value="">— Klasse wählen —</option>
                                {classes.map(cls => (
                                    <option key={cls.id} value={cls.id}>
                                        {cls.name} ({cls.student_count} Schüler)
                                    </option>
                                ))}
                            </select>
                        </div>

                        <button
                            className="btn btn-secondary"
                            onClick={handleLoadAllClasses}
                            disabled={loadingClasses || loadingStudents}
                            style={{ alignSelf: 'flex-end' }}
                            title="Alle Klassen (ohne Lehrerkollegium) nacheinander laden — jede Klasse beginnt im Druck auf einer neuen Seite"
                        >
                            📚 Alle Klassen (Sammeldruck)
                        </button>

                        {totalCards > 0 && !loadingStudents && (
                            <button
                                className="btn btn-primary"
                                onClick={handlePrint}
                                style={{ alignSelf: 'flex-end' }}
                            >
                                🖨️ Drucken ({totalCards} Karten{printGroups.length > 1 ? `, ${printGroups.length} Klassen` : ''})
                            </button>
                        )}
                    </div>

                    {/* Info-Text */}
                    {totalCards > 0 && !loadingStudents && (
                        <p className="text-sm opacity-70 mt-md">
                            Vorschau: {totalCards} QR-Karten
                            {printGroups.length > 1
                                ? <> in <strong>{printGroups.length} Klassen</strong> — jede Klasse beginnt auf einer neuen Seite.</>
                                : <> für Klasse <strong>{printGroups[0]?.name}</strong>.</>}
                            {' '}Schüler ohne PIN haben automatisch eine neue erhalten.
                        </p>
                    )}

                    {/* Warnung wenn PWA-URL nicht konfiguriert */}
                    {!pwaBaseUrl && totalCards > 0 && (
                        <div className="alert alert-error mt-md">
                            Keine PWA-URL konfiguriert — QR-Codes zeigen auf localhost. Bitte unter <strong>Schulenverwaltung</strong> die PWA-URL setzen.
                        </div>
                    )}
                </div>
            </div>

            {/* Lade-Indikator */}
            {loadingStudents && (
                <div className="text-center no-print" style={{ padding: '2rem' }}>
                    <div className="spinner" style={{ margin: '0 auto' }}></div>
                    <p className="mt-md">{bulkProgress || 'Lade Druckdaten...'}</p>
                </div>
            )}

            {/* ============================================================
                DRUCKBEREICH – deterministische Blätter (.print-sheet):
                Kopfzeile + max. 12 Karten pro Blatt, harter Umbruch dazwischen
                ============================================================ */}
            {printGroups.length > 0 && (
                <div className="print-area" ref={printAreaRef}>
                    {printGroups.flatMap((group) => {
                        /* DETERMINISTISCHE Blätter: Karten explizit in 12er-Blöcke
                           (4 Reihen × 3 Spalten) schneiden — jedes Blatt ist ein eigener
                           Container mit eigener Kopfzeile und hartem Seitenumbruch.
                           (Browser-Fragmentierung von Grids ist unzuverlässig — ein
                           nth-child(12n)-Zwangsumbruch würde z.B. 9/3-Seiten erzeugen.) */
                        const pages = []
                        for (let i = 0; i < group.students.length; i += 12) {
                            pages.push(group.students.slice(i, i + 12))
                        }
                        return pages.map((pageStudents, pi) => (
                            <div key={`${group.id}-${pi}`} className="print-sheet">
                                {/* Kopfzeile auf JEDEM Blatt: Klasse + Blattnummer
                                    (200 Seiten bleiben damit immer zuordenbar) */}
                                <div className="print-page-header">
                                    <span className="pph-title">Login-PINs – Klasse {group.name}</span>
                                    <span className="pph-meta">
                                        Blatt {pi + 1}/{pages.length} · {group.students.length} Karten · erstellt am {new Date().toLocaleDateString('de-DE')}
                                    </span>
                                </div>
                            {/* QR-Karten Grid: 3 Spalten pro Zeile */}
                            <div className="print-grid">
                                {pageStudents.map((student) => (
                                    <div key={student.id} className="print-card">
                                        <div className="print-card-name">
                                            {student.last_name}, {student.first_name}
                                        </div>

                                        <div className="print-card-class">
                                            Klasse {student.class_name}
                                        </div>

                                        {/* QR – verlinkt auf die PWA-Login-Seite der Schule,
                                            enthält KEINE PIN (die steht nur als Text auf der Karte) */}
                                        <div className="print-card-qr">
                                            {qrSrc
                                                ? <img src={qrSrc} alt="QR: Login-Seite" width={100} height={100}
                                                       style={{ imageRendering: 'pixelated' }} />
                                                : <div style={{ width: 100, height: 100, background: '#f1f5f9' }} />}
                                        </div>

                                        <div className="print-card-pin">
                                            {student.login_pin || '—'}
                                        </div>

                                        <div className="print-card-label">
                                            Login-PIN
                                        </div>
                                    </div>
                                ))}
                            </div>
                            </div>
                        ))
                    })}
                </div>
            )}

            {/* Leerzustand */}
            {!loadingStudents && printGroups.length === 0 && selectedClassId && (
                <div className="card text-center no-print" style={{ padding: '2rem' }}>
                    <p>Keine aktiven Schüler in dieser Klasse gefunden.</p>
                </div>
            )}
        </Layout>
    )
}
