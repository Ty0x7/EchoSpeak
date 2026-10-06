import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.tsx';
import './theme/tokens.css';
import './theme/blueprint.css';
import { installThemeSync } from './theme/theme';

// Before the first render, so the first frame is already in the chosen theme.
installThemeSync();

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
    <React.StrictMode>
        <App />
    </React.StrictMode>
);
