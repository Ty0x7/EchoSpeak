import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, MemoryRouter, Routes, Route } from 'react-router-dom';
import { Marketing } from './marketing.tsx';
import { Docs } from './site/Docs.tsx';
import { controlDesktopWindow, isDesktopRuntime, openDesktopLogs } from './desktop/bridge.ts';

// The website should not download the whole app; load it only on /app and in the desktop shell.
const Dashboard = lazy(() => import('./index.tsx').then((module) => ({ default: module.Dashboard })));
const DesktopApp = lazy(() => import('./desktop/DesktopApp.tsx').then((module) => ({ default: module.DesktopApp })));

/** Same markup as the boot splash in index.html, so loading the app chunk never flashes blank. */
const BootSplash = () => (
    <div className="es-boot-splash"><div className="es-boot-face"><i /><i /></div></div>
);

function DesktopStartupFailure() {
    React.useEffect(() => {
        // The main window starts hidden. A rejected lazy import must still
        // reveal a useful recovery screen, even before DesktopApp can mount.
        void controlDesktopWindow('show').catch(() => undefined);
    }, []);
    return <div className="es-boot-splash"><section className="es-startup-error" role="alert">
        <h1>EchoSpeak couldn’t open</h1>
        <p>The desktop interface failed to load. Retry, or open the logs for troubleshooting.</p>
        <div><button onClick={() => window.location.reload()}>Retry</button>
            <button onClick={() => void openDesktopLogs().catch(() => undefined)}>Open logs</button>
            <button onClick={() => void controlDesktopWindow('close')}>Close</button></div>
    </section></div>;
}

class DesktopStartupBoundary extends React.Component<React.PropsWithChildren, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    componentDidCatch(error: Error) { console.error('EchoSpeak desktop startup failed', error); }
    render() { return this.state.failed ? <DesktopStartupFailure /> : this.props.children; }
}

const App: React.FC = () => {
    if (isDesktopRuntime()) {
        return (
            <MemoryRouter initialEntries={['/app']}>
                <DesktopStartupBoundary><Suspense fallback={<BootSplash />}>
                    <DesktopApp />
                </Suspense></DesktopStartupBoundary>
            </MemoryRouter>
        );
    }
    return (
        <Router>
            <Routes>
                <Route path="/" element={<Marketing />} />
                <Route path="/docs" element={<Docs />} />
                <Route path="/docs/:section" element={<Docs />} />
                <Route path="/app/*" element={<Suspense fallback={<BootSplash />}><Dashboard /></Suspense>} />
            </Routes>
        </Router>
    );
};

export default App;
