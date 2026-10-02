import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, MemoryRouter, Routes, Route } from 'react-router-dom';
import { Marketing } from './marketing.tsx';
import { isDesktopRuntime } from './desktop/bridge.ts';

// The website should not download the whole app; load it only on /app and in the desktop shell.
const Dashboard = lazy(() => import('./index.tsx').then((module) => ({ default: module.Dashboard })));
const DesktopApp = lazy(() => import('./desktop/DesktopApp.tsx').then((module) => ({ default: module.DesktopApp })));

/** Same markup as the boot splash in index.html, so loading the app chunk never flashes blank. */
const BootSplash = () => (
    <div className="es-boot-splash"><img src="/logo.png" alt="" width={44} height={44} /></div>
);

const App: React.FC = () => {
    if (isDesktopRuntime()) {
        return (
            <MemoryRouter initialEntries={['/app']}>
                <Suspense fallback={<BootSplash />}>
                    <DesktopApp />
                </Suspense>
            </MemoryRouter>
        );
    }
    return (
        <Router>
            <Routes>
                <Route path="/" element={<Marketing />} />
                <Route path="/app/*" element={<Suspense fallback={<BootSplash />}><Dashboard /></Suspense>} />
            </Routes>
        </Router>
    );
};

export default App;
