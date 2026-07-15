import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import App from './App';
import { I18nProvider } from './i18n';
import { STATIC_MODE } from './lib/api';
import './design/colors_and_type.css';
import './design/portal.css';
import './styles/index.css';

// Published static site → HashRouter: deep links like `/#/knowledge` work on
// any static host (GitHub Pages, object storage) with zero rewrite rules. The
// live server build keeps BrowserRouter (clean paths + SPA fallback).
const Router = STATIC_MODE ? HashRouter : BrowserRouter;

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Router>
      <I18nProvider>
        <App />
      </I18nProvider>
    </Router>
  </StrictMode>,
);
