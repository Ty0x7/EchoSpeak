// Moved out of index.tsx (10.0 split). Kept verbatim.
import { colors } from "./runtime";

export const globalCss = `
         :root {
           /* ~112.5% browser zoom (~90% of prior 125% scale); shell size compensates so layout fits viewport */
           --ui-scale: 1.125;
         }
         @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Space+Grotesk:wght@500;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
         * { box-sizing: border-box; }
         html, body {
           margin: 0;
           width: 100%;
           height: 100%;
           overflow: hidden;
           background: ${colors.bg};
           font-family: 'Inter', 'Manrope', system-ui, sans-serif;
           -webkit-font-smoothing: antialiased;
         }
         #root {
           width: 100%;
           height: 100%;
           overflow: hidden;
         }
         * { scrollbar-width: thin; scrollbar-color: #333 transparent; }
         *::-webkit-scrollbar { width: 8px; height: 8px; }
         *::-webkit-scrollbar-track { background: transparent; }
         *::-webkit-scrollbar-thumb { background: #333; border-radius: 0; }
         *::-webkit-scrollbar-thumb:hover { background: #444; }
         ::selection { background: #fff; color: #000; }
         
         .chat-markdown p:first-of-type { margin-top: 0; }
         .chat-markdown p:last-of-type { margin-bottom: 0; }
         .chat-markdown {
           font-size: 15px;
           line-height: 1.65;
           letter-spacing: 0.01em;
           overflow-wrap: anywhere;
           word-break: break-word;
           min-width: 0;
           max-width: 100%;
         }
         .chat-markdown pre {
           max-width: 100%;
           overflow-x: auto;
           white-space: pre;
         }
         .chat-markdown code {
           overflow-wrap: anywhere;
           word-break: break-word;
         }
         .chat-markdown img,
         .chat-markdown table {
           max-width: 100%;
         }
         .chat-markdown table {
           display: block;
           overflow-x: auto;
         }
         .chat-text {
           font-size: 15px;
           line-height: 1.65;
           letter-spacing: 0.01em;
           white-space: pre-wrap;
           overflow-wrap: anywhere;
           word-break: break-word;
           min-width: 0;
           max-width: 100%;
         }
         .chat-line-user { color: rgba(255,255,255,0.55); text-align: right; }
         .chat-line-assistant { color: rgba(255,255,255,0.92); text-align: left; min-width: 0; max-width: 100%; }
         .chat-flat { background: transparent !important; border: none !important; box-shadow: none !important; border-radius: 0 !important; backdrop-filter: none !important; min-width: 0; }
         .chat-embeds,
         .chat-embed-footer {
           width: 100%;
           max-width: 100%;
           min-width: 0;
           box-sizing: border-box;
         }
         
         /* Layout units are pre-zoom; zoom scales the whole UI to ~125% while fitting the real viewport */
         .app-shell {
           width: calc(100vw / var(--ui-scale));
           height: calc(100vh / var(--ui-scale));
           height: calc(100dvh / var(--ui-scale));
           max-width: calc(100vw / var(--ui-scale));
           max-height: calc(100vh / var(--ui-scale));
           max-height: calc(100dvh / var(--ui-scale));
           display: grid;
           grid-template-rows: minmax(0, 1fr);
           gap: 0;
           padding: 0;
           margin: 0;
           background: ${colors.bg};
           zoom: var(--ui-scale);
           transform-origin: top left;
           overflow: hidden;
         }
         .app-shell > * {
           min-width: 0;
           min-height: 0;
           max-height: 100%;
           overflow: hidden;
         }
         .echo-sidebar {
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           overflow: hidden;
           z-index: 2;
           /* density lives on the component; do not add outer padding/gap here */
         }
         .visualizer-pane {
           display: flex;
           flex-direction: column;
           align-items: stretch;
           justify-content: flex-start;
           background: rgba(0,0,0,0.2);
           border-right: 1px solid ${colors.line};
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           overflow: hidden;
           position: relative;
           z-index: 1;
         }
         .visualizer-pane-body {
           flex: 1 1 auto;
           min-width: 0;
           min-height: 0;
           width: 100%;
           height: 100%;
           overflow: hidden;
           display: flex;
           flex-direction: column;
         }
         .visualizer-pane-body.is-avatar {
           align-items: center;
           justify-content: center;
         }
         .visualizer-pane-body.is-workspace {
           align-items: stretch;
           justify-content: flex-start;
         }
         /* Video fills the visualizer column; chat stays in glow-panel (agentic shell). */
         .video-workspace-pane {
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           max-height: 100%;
           overflow: hidden;
           display: flex;
           flex-direction: column;
           background: #000000;
           border-right: 1px solid ${colors.line};
           position: relative;
           z-index: 1;
         }
         .video-workspace-pane > * {
           flex: 1 1 auto;
           min-height: 0;
           min-width: 0;
           height: 100%;
         }
         .glow-panel {
           background: ${colors.panel};
           display: flex;
           flex-direction: column;
           width: 100%;
           height: 100%;
           min-width: 0;
           min-height: 0;
           overflow: hidden;
           transition: all 0.3s ease;
           z-index: 1;
         }
         @keyframes echo-square-spin {
           to { transform: rotate(360deg); }
         }
         @keyframes pulse {
           0%, 100% { opacity: 1; }
           50% { opacity: 0.5; }
         }
         .panel-header {
           display: none;
           align-items: center;
           justify-content: flex-end;
           min-height: 44px;
           padding: 9px 16px;
           border-bottom: 1px solid ${colors.line};
         }
         .panel-header .title {
           display: none;
           gap: 14px;
           align-items: center;
           font-family: 'Space Grotesk', sans-serif;
           font-size: 22px;
           font-weight: 700;
           letter-spacing: -0.02em;
           color: ${colors.text};
         }
         .panel-dot {
           width: 14px;
           height: 14px;
           background: #fff;
           border-radius: 0;
         }
         .panel-body {
          flex: 1 1 auto;
          display: flex;
          flex-direction: column;
          padding: 18px 10px 16px 14px;
          overflow: hidden;
          min-height: 0;
          min-width: 0;
          gap: 14px;
          width: 100%;
          height: 100%;
          box-sizing: border-box;
        }
        .research-panel {
          position: relative;
          display: flex;
          flex-direction: column;
          height: 100%;
          flex: 1;
          overflow: hidden;
          min-height: 0;
          gap: 12px;
        }
        .tab-bar {
          display: flex;
          flex-wrap: nowrap;
           gap: 10px;
           padding-bottom: 10px;
           border-bottom: 1px solid rgba(255,255,255,0.08);
           overflow-y: visible;
           overflow-x: auto;
         }
         .top-tab-groups {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
          width: 100%;
          min-width: 0;
        }
         .top-tab-group {
           position: relative;
           display: flex;
           align-items: center;
           min-width: 0;
         }
         .top-tab-group .tab-button {
           width: 100%;
           min-height: 48px;
           justify-content: center;
         }
         .tab-button {
           padding: 6px 12px;
           background: transparent;
           border: 1px solid transparent;
           border-radius: 999px;
           color: ${colors.textDim};
           font-size: 13px;
           font-weight: 500;
           cursor: pointer;
           transition: all 0.2s ease;
           position: relative;
         }
         .tab-button:hover {
           color: ${colors.text};
           background: rgba(255,255,255,0.05);
         }
         .tab-button.active {
           color: #fff;
           background: linear-gradient(135deg, rgba(45,108,255,0.2), rgba(45,108,255,0.05));
           border: 1px solid rgba(140,180,255,0.3);
           box-shadow: 0 4px 12px -4px rgba(45,108,255,0.3), inset 0 1px 0 rgba(255,255,255,0.1);
           text-shadow: 0 0 12px rgba(140,180,255,0.6);
         }
         @media (max-width: 1180px) {
           .top-tab-groups {
             display: flex;
             gap: 8px;
             overflow-x: auto;
           }
           .top-tab-group {
             flex: 0 0 auto;
           }
           .top-tab-group .tab-button {
             width: auto;
             min-width: max-content;
           }
         }
         .tab-group {
           display: flex;
           flex-wrap: wrap;
           gap: 4px;
           padding: 4px;
           background: rgba(255,255,255,0.02);
           border-radius: 8px;
         }
         .tab-group-label {
           font-size: 10px;
           color: ${colors.textDim};
           text-transform: uppercase;
           letter-spacing: 0.5px;
           padding: 2px 8px;
           opacity: 0.7;
         }
         .research-scroll {
           flex: 1;
           overflow-y: auto;
           display: flex;
           flex-direction: column;
           gap: 14px;
           padding: 0;
           width: 100%;
         }
         .research-card {
           background: rgba(255,255,255,0.03);
           border: 1px solid rgba(255, 255, 255, 0.08);
           border-radius: 4px;
           padding: 16px 18px;
           transition: border-color 0.2s ease, background 0.2s ease;
           box-shadow: none;
           backdrop-filter: none;
         }
         .research-card:hover {
           border-color: rgba(255, 255, 255, 0.16);
           transform: none;
           background: rgba(255,255,255,0.045);
           box-shadow: none;
         }
         /* ── EchoSpeak Settings modal (portaled to body, no chrome bleed) ── */
         .studio-shell {
           position: fixed;
           inset: 0;
           z-index: 100000;
           width: 100vw;
           height: 100vh;
           height: 100dvh;
           max-width: 100vw;
           max-height: 100dvh;
           display: flex;
           flex-direction: column;
           background:
             radial-gradient(ellipse 80% 50% at 50% -20%, rgba(255,255,255,0.06), transparent 55%),
             radial-gradient(ellipse 40% 30% at 100% 100%, rgba(255,255,255,0.03), transparent 45%),
             #030406;
           color: #fff;
           overflow: hidden;
         }
         .app-shell.is-studio-covered {
           pointer-events: none;
           filter: blur(2px);
         }
         .studio-shell::before {
           content: "";
           pointer-events: none;
           position: absolute;
           inset: 0;
           background-image:
             linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px),
             linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
           background-size: 48px 48px;
           mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, #000 20%, transparent 75%);
           opacity: 0.7;
         }
         .studio-top {
           position: relative;
           z-index: 2;
           display: flex;
           align-items: center;
           justify-content: space-between;
           gap: 16px;
           padding: 18px 28px 14px;
           border-bottom: 1px solid rgba(255,255,255,0.08);
           background: rgba(0,0,0,0.4);
         }
         .studio-brand {
           display: flex;
           align-items: center;
           gap: 14px;
           min-width: 0;
         }
         .studio-brand-mark {
           width: 28px;
           height: 28px;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.2);
           display: flex;
           align-items: center;
           justify-content: center;
           background: rgba(255,255,255,0.04);
           flex-shrink: 0;
         }
         .studio-brand-mark img {
           width: 16px;
           height: 16px;
           border-radius: 1px;
         }
         .studio-title {
           font-family: 'Space Grotesk', Inter, sans-serif;
           font-size: 13px;
           font-weight: 700;
           letter-spacing: 0.14em;
           text-transform: uppercase;
         }
         .studio-sub {
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 10px;
           letter-spacing: 0.08em;
           color: rgba(255,255,255,0.38);
           margin-top: 2px;
         }
         .studio-x {
           width: 40px;
           height: 40px;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.18);
           background: transparent;
           color: #fff;
           cursor: pointer;
           display: flex;
           align-items: center;
           justify-content: center;
           transition: background 0.15s ease, border-color 0.15s ease;
           flex-shrink: 0;
         }
         .studio-x:hover {
           background: rgba(255,255,255,0.08);
           border-color: rgba(255,255,255,0.35);
         }
         .studio-nav {
           position: relative;
           z-index: 5;
           display: flex;
           align-items: stretch;
           justify-content: stretch;
           border-bottom: 1px solid rgba(255,255,255,0.06);
           background: rgba(8,8,8,0.98);
           min-width: 0;
           width: 100%;
           flex: 0 0 auto;
         }
         .studio-nav-arrow {
           width: 36px;
           flex: 0 0 36px;
           border: 0;
           border-left: 1px solid rgba(255,255,255,0.06);
           border-right: 1px solid rgba(255,255,255,0.06);
           background: rgba(8,8,8,0.98);
           color: rgba(255,255,255,0.72);
           cursor: pointer;
           font-size: 18px;
           z-index: 2;
         }
         .studio-nav-arrow:hover { color: #fff; background: rgba(255,255,255,0.06); }
         .studio-nav-inner {
           display: flex;
           gap: 0;
           overflow-x: auto;
           overflow-y: hidden;
           min-width: 0;
           flex: 1 1 auto;
           width: auto;
           max-width: none;
           padding: 0 4px;
           scrollbar-width: thin;
           scrollbar-color: rgba(255,255,255,0.25) transparent;
           -webkit-overflow-scrolling: touch;
         }
         .studio-nav-inner::-webkit-scrollbar { height: 4px; }
         .studio-nav-inner::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.22); border-radius: 999px; }
         .studio-tab {
           height: 44px;
           padding: 0 14px;
           border: none;
           border-bottom: 2px solid transparent;
           background: transparent;
           color: rgba(255,255,255,0.4);
           font-size: 12px;
           font-weight: 600;
           letter-spacing: 0.04em;
           cursor: pointer;
           white-space: nowrap;
           flex: 0 0 auto;
           transition: color 0.15s ease;
           font-family: Inter, system-ui, sans-serif;
         }
         .studio-tab:hover { color: rgba(255,255,255,0.75); }
         .studio-tab.active {
           color: #fff;
           border-bottom-color: #fff;
         }
         .studio-body {
           position: relative;
           z-index: 1;
           flex: 1;
           min-height: 0;
           display: flex;
           justify-content: center;
           overflow: hidden;
         }
         .studio-column {
           width: 100%;
           max-width: 960px;
           height: 100%;
           min-height: 0;
           display: flex;
           flex-direction: column;
           padding: 24px 28px 40px;
           box-sizing: border-box;
           overflow: hidden;
         }
         .studio-hero {
           display: flex;
           align-items: baseline;
           justify-content: space-between;
           gap: 12px;
           margin-bottom: 18px;
           padding-bottom: 14px;
           border-bottom: 1px solid rgba(255,255,255,0.06);
           flex-shrink: 0;
         }
         .studio-hero h2 {
           margin: 0;
           font-family: 'Space Grotesk', Inter, sans-serif;
           font-size: 22px;
           font-weight: 700;
           letter-spacing: -0.02em;
         }
         .studio-hero span {
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 10px;
           letter-spacing: 0.1em;
           text-transform: uppercase;
           color: rgba(255,255,255,0.35);
         }
         .studio-dock {
           position: absolute;
           right: 24px;
           bottom: 24px;
           width: 88px;
           height: 88px;
           border-radius: 3px;
           overflow: hidden;
           border: 1px solid rgba(255,255,255,0.12);
           background: rgba(0,0,0,0.7);
           z-index: 4;
         }
         .studio-shell .research-scroll {
           align-items: stretch;
           width: 100%;
         }
          .studio-shell .research-card {
            width: 100%;
            box-sizing: border-box;
          }
          /* Reference-faithful Settings composition: title, vertical section rail, content. */
          .studio-backdrop {
            position: fixed;
            inset: 0;
            z-index: 100000;
            display: grid;
            place-items: center;
            padding: 42px;
            box-sizing: border-box;
            overflow: hidden;
            background: rgba(0,0,0,.72);
            backdrop-filter: blur(5px);
            -webkit-backdrop-filter: blur(5px);
          }
          .studio-shell {
            position: relative;
            inset: auto;
            z-index: 1;
            width: min(1060px, calc(100vw - 84px));
            height: min(760px, calc(100dvh - 84px));
            max-width: 1060px;
            max-height: 760px;
            display: grid;
            grid-template-columns: 220px minmax(0, 1fr);
            grid-template-rows: 58px 44px minmax(0, 1fr);
            background: #0a0a0a;
            border: 1px solid rgba(255,255,255,.13);
            border-radius: 12px;
            box-shadow: 0 28px 90px rgba(0,0,0,.68);
            overflow: hidden;
          }
          .studio-top {
            grid-column: 1 / -1;
            grid-row: 1;
            padding: 0 20px 0 22px;
            background: #0b0b0b;
          }
          .studio-title {
            font-family: 'Inter', 'Segoe UI Variable', sans-serif;
            font-size: 17px;
            font-weight: 520;
            letter-spacing: -.015em;
            text-transform: none;
          }
          .studio-x {
            width: 32px;
            height: 32px;
            border: 0;
          }
          .studio-nav {
            grid-column: 1;
            grid-row: 2 / 4;
            min-width: 0;
            width: auto;
            padding: 14px 12px;
            align-items: stretch;
            border-right: 1px solid rgba(255,255,255,.09);
            border-bottom: 0;
            background: #0a0a0a;
          }
          .studio-nav-inner {
            width: 100%;
            padding: 0;
            min-height: 0;
            overflow-x: hidden;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 5px;
            scrollbar-width: thin;
            scrollbar-color: rgba(255,255,255,.18) transparent;
          }
          .studio-nav .studio-tab {
            width: 100%;
            height: 42px;
            display: flex;
            align-items: center;
            gap: 11px;
            padding: 0 12px;
            border: 0;
            border-radius: 5px;
            color: rgba(255,255,255,.67);
            font-size: 12px;
            font-weight: 500;
            letter-spacing: 0;
            text-align: left;
          }
          .studio-nav .studio-tab.active {
            color: #fff;
            background: rgba(255,255,255,.08);
          }
          .studio-tab-icon {
            width: 18px;
            display: inline-grid;
            place-items: center;
            color: rgba(255,255,255,.7);
            font-size: 15px;
          }
          .studio-subnav {
            grid-column: 2;
            grid-row: 2;
            min-width: 0;
            display: flex;
            align-items: end;
            gap: 4px;
            padding: 6px 22px 0;
            overflow-x: auto;
            border-bottom: 1px solid rgba(255,255,255,.07);
            background: #0b0b0b;
          }
          .studio-subnav .studio-tab {
            height: 37px;
            padding: 0 10px;
            font-size: 10px;
            letter-spacing: .02em;
          }
          .studio-body {
            grid-column: 2;
            grid-row: 3;
            display: block;
            overflow: hidden;
          }
          .studio-column {
            max-width: none;
            padding: 20px 22px 28px;
          }
          .studio-hero {
            margin-bottom: 14px;
            padding-bottom: 12px;
          }
          .studio-hero h2 {
            font-family: 'Inter', 'Segoe UI Variable', sans-serif;
            font-size: 19px;
            font-weight: 550;
          }
          .studio-hero span {
            font-family: 'Inter', 'Segoe UI Variable', sans-serif;
            font-size: 10px;
            letter-spacing: .08em;
          }
          .settings-general-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
          }
          .settings-general-card {
            display: flex;
            flex-direction: column;
            gap: 14px;
            padding: 16px;
            border: 1px solid rgba(255,255,255,.09);
            border-radius: 8px;
            background: rgba(255,255,255,.025);
          }
          .settings-general-card-wide {
            grid-column: 1 / -1;
          }
          .settings-general-row {
            min-height: 46px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            padding-top: 12px;
            border-top: 1px solid rgba(255,255,255,.07);
            color: #fff;
          }
          .settings-general-row > span {
            display: grid;
            gap: 4px;
          }
          .settings-general-row strong {
            font-size: 12px;
            font-weight: 550;
          }
          .settings-general-row small {
            color: rgba(255,255,255,.44);
            font-size: 10px;
            line-height: 1.4;
          }
          .settings-general-row input[type="checkbox"] {
            width: 17px;
            height: 17px;
            accent-color: #fff;
            flex: 0 0 auto;
          }
          /* One visual language across every Settings group. Existing controls
             retain their backend ownership; this layer only normalizes layout. */
          .studio-shell .research-scroll {
            gap: 12px;
            padding: 0 2px 2px 0;
            scrollbar-gutter: stable;
          }
          .studio-shell .research-card {
            margin: 0;
            padding: 15px 16px;
            border-color: rgba(255,255,255,.09);
            border-radius: 8px;
            background: rgba(255,255,255,.024);
          }
          .studio-shell .research-card:hover {
            border-color: rgba(255,255,255,.14);
            background: rgba(255,255,255,.032);
          }
          .studio-shell .research-title {
            margin-bottom: 5px;
            font-size: 13px;
            font-weight: 560;
            letter-spacing: -.005em;
          }
          .studio-shell .research-snippet {
            font-size: 11px;
            line-height: 1.55;
            color: rgba(255,255,255,.48);
          }
          .studio-shell input:not([type="checkbox"]):not([type="radio"]),
          .studio-shell select,
          .studio-shell textarea {
            min-height: 36px;
            max-width: 100%;
            box-sizing: border-box;
            border: 1px solid rgba(255,255,255,.11);
            border-radius: 5px;
            color: rgba(255,255,255,.9);
            background: #111113;
            font: 500 11px/1.4 'Inter', 'Segoe UI Variable', sans-serif;
          }
          .studio-shell input:not([type="checkbox"]):not([type="radio"]):focus,
          .studio-shell select:focus,
          .studio-shell textarea:focus {
            outline: 1px solid rgba(255,255,255,.62);
            outline-offset: 1px;
            border-color: rgba(255,255,255,.32);
          }
          .studio-shell .icon-button {
            min-height: 32px;
            border-radius: 5px;
            border-color: rgba(255,255,255,.12);
            background: rgba(255,255,255,.035);
            font: 550 11px/1 'Inter', 'Segoe UI Variable', sans-serif;
          }
          @media (max-width: 900px), (max-height: 720px) {
            .studio-backdrop {
              padding: 16px;
            }
            .studio-shell {
              width: calc(100vw - 32px);
              height: calc(100dvh - 32px);
              max-width: none;
              max-height: none;
              grid-template-columns: 184px minmax(0, 1fr);
            }
            .studio-nav {
              padding: 10px 8px;
            }
            .studio-nav .studio-tab {
              height: 38px;
              padding: 0 9px;
              font-size: 11px;
            }
            .studio-column {
              padding: 16px 18px 22px;
            }
            .studio-hero {
              margin-bottom: 11px;
              padding-bottom: 10px;
            }
          }
          @media (max-width: 720px) {
            .studio-shell {
              grid-template-columns: minmax(0, 1fr);
              grid-template-rows: 56px 51px 42px minmax(0, 1fr);
            }
            .studio-top {
              grid-column: 1;
              grid-row: 1;
              padding: 0 14px 0 16px;
            }
            .studio-nav {
              grid-column: 1;
              grid-row: 2;
              width: 100%;
              padding: 6px 8px;
              overflow-x: auto;
              border-right: 0;
              border-bottom: 1px solid rgba(255,255,255,.08);
            }
            .studio-nav-inner {
              flex-direction: row;
              gap: 4px;
              overflow-x: auto;
              overflow-y: hidden;
            }
            .studio-nav .studio-tab {
              width: auto;
              flex: 0 0 auto;
              padding: 0 10px;
            }
            .studio-subnav {
              grid-column: 1;
              grid-row: 3;
              padding: 5px 12px 0;
            }
            .studio-body {
              grid-column: 1;
              grid-row: 4;
            }
            .studio-column {
              padding: 14px 14px 20px;
            }
          }
          @media (max-width: 520px) {
            .studio-backdrop {
              padding: 0;
            }
            .studio-shell {
              width: 100vw;
              height: 100dvh;
              border: 0;
              border-radius: 0;
            }
            .settings-general-grid {
              grid-template-columns: minmax(0, 1fr);
            }
            .settings-general-card-wide {
              grid-column: auto;
            }
            .settings-general-card,
            .studio-shell .research-card {
              padding: 13px;
            }
            .settings-general-row {
              align-items: flex-start;
              gap: 12px;
            }
            .studio-hero span {
              display: none;
            }
          }
         .research-title {
           font-size: 16px;
           font-weight: 600;
           color: ${colors.text};
           margin-bottom: 8px;
           line-height: 1.4;
         }
         .research-snippet {
           font-size: 15px;
           line-height: 1.7;
           color: ${colors.textDim};
           white-space: pre-wrap;
         }
         .research-source {
           margin-top: 12px;
           font-size: 11px;
           font-weight: 500;
           color: ${colors.accent};
           text-decoration: none;
           opacity: 0.8;
           display: block;
           overflow: hidden;
           text-overflow: ellipsis;
         }
         .chat-embed-source-link:hover {
           color: rgba(255,255,255,0.62) !important;
           border-bottom-color: rgba(255,255,255,0.35) !important;
         }
         .research-source:hover {
           opacity: 1;
           text-decoration: underline;
         }
         .chat-scroll {
           flex: 1;
           overflow-y: auto;
           overflow-x: hidden;
           display: flex;
           flex-direction: column;
           gap: 12px;
           width: 100%;
           /* Right padding sits inside stable gutter so text clears the scrollbar */
           padding: 4px 16px 8px 4px;
           scrollbar-gutter: stable;
           scrollbar-width: thin;
           scrollbar-color: rgba(255,255,255,0.18) transparent;
         }
         .chat-scroll::-webkit-scrollbar {
           width: 10px;
         }
         .chat-scroll::-webkit-scrollbar-track {
           background: transparent;
           margin: 8px 0;
         }
         .chat-scroll::-webkit-scrollbar-thumb {
           background: rgba(255,255,255,0.14);
           border-radius: 2px;
           border: 2px solid transparent;
           background-clip: padding-box;
         }
         .chat-scroll::-webkit-scrollbar-thumb:hover {
           background: rgba(255,255,255,0.28);
           background-clip: padding-box;
           border: 2px solid transparent;
         }

         .live-run {
           width: 100%;
           min-width: 0;
           box-sizing: border-box;
           margin: 8px 0 12px;
           padding: 10px 12px;
           border: 1px solid rgba(255,255,255,0.09);
           border-radius: 6px;
           background: rgba(255,255,255,0.022);
         }
         .live-run-header {
           display: flex;
           align-items: center;
           justify-content: space-between;
           gap: 14px;
           min-width: 0;
         }
         .live-run-heading {
           display: flex;
           align-items: center;
           gap: 9px;
           min-width: 0;
           color: rgba(255,255,255,0.82);
           font-size: 12px;
           line-height: 1.35;
         }
         .live-run-heading > strong {
           min-width: 0;
           overflow: hidden;
           text-overflow: ellipsis;
           white-space: nowrap;
           font-weight: 560;
         }
         .live-run-meta {
           flex: 0 0 auto;
           color: rgba(255,255,255,0.36);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 9px;
           letter-spacing: 0.04em;
         }
         .live-run-actions {
           display: flex;
           align-items: center;
           gap: 5px;
           flex: 0 0 auto;
         }
         .live-run-live-text {
           display: grid;
           gap: 8px;
           margin: 9px 0 0 21px;
           padding: 9px 10px;
           border-left: 1px solid rgba(255,255,255,0.16);
           background: rgba(255,255,255,0.018);
           color: rgba(255,255,255,0.68);
           font-size: 11px;
           line-height: 1.48;
         }
         .live-run-live-text > div {
           min-width: 0;
         }
         .live-run-live-text span {
           display: block;
           margin-bottom: 3px;
           color: rgba(255,255,255,0.34);
           font: 600 8px/1.2 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: .08em;
           text-transform: uppercase;
         }
         .live-run-live-text p {
           margin: 0;
           white-space: pre-wrap;
           overflow-wrap: anywhere;
         }
         .live-run-live-thought {
           color: rgba(255,255,255,0.48);
         }
         .live-run-live-reply {
           color: rgba(255,255,255,0.86);
         }
         .live-run-action {
           min-height: 28px;
           padding: 0 9px;
           border: 1px solid rgba(255,255,255,0.12);
           border-radius: 4px;
           color: rgba(255,255,255,0.68);
           background: rgba(255,255,255,0.025);
           font: 550 10px/1 Inter, 'Segoe UI Variable', sans-serif;
           cursor: pointer;
           transition: color .15s ease, border-color .15s ease, background .15s ease;
         }
         .live-run-action:hover {
           color: #fff;
           border-color: rgba(255,255,255,0.25);
           background: rgba(255,255,255,0.07);
         }
         .live-run-action.is-stop {
           color: rgba(255,255,255,0.88);
           border-color: rgba(255,255,255,0.2);
         }
         .live-run-action.is-toggle {
           color: rgba(255,255,255,0.46);
           background: transparent;
         }
         .live-run-details {
           display: grid;
           grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 10px 16px;
           margin-top: 10px;
           padding: 10px 0 0 21px;
           border-top: 1px solid rgba(255,255,255,0.065);
         }
         .live-run-detail {
           min-width: 0;
           color: rgba(255,255,255,0.65);
           font-size: 11px;
           line-height: 1.45;
           overflow-wrap: anywhere;
         }
         .live-run-detail > span {
           display: block;
           margin-bottom: 3px;
           color: rgba(255,255,255,0.32);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 8px;
           letter-spacing: 0.09em;
           text-transform: uppercase;
         }
         .live-run-measures {
           display: flex;
           flex-wrap: wrap;
           gap: 6px;
           margin: 9px 0 0 21px;
         }
         .live-run-measures > span {
           padding: 4px 7px;
           border: 1px solid rgba(255,255,255,0.075);
           border-radius: 4px;
           color: rgba(255,255,255,0.38);
           background: rgba(255,255,255,0.018);
           font: 500 8px/1.2 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: .04em;
           text-transform: uppercase;
         }
         .live-run-measures b {
           color: rgba(255,255,255,0.72);
           font-weight: 650;
         }
         .live-run-requirements,
         .live-run-timeline {
           display: grid;
           gap: 5px;
           margin: 9px 0 0 21px;
           padding-top: 8px;
           border-top: 1px solid rgba(255,255,255,0.05);
         }
         .live-run-requirements > div,
         .live-run-timeline > div {
           display: grid;
           grid-template-columns: 8px minmax(0, 1fr) auto;
           align-items: center;
           gap: 7px;
           min-width: 0;
           color: rgba(255,255,255,0.58);
           font-size: 10px;
           line-height: 1.35;
         }
         .live-run-requirements i,
         .live-run-timeline i {
           width: 5px;
           height: 5px;
           border: 1px solid rgba(255,255,255,0.35);
           border-radius: 50%;
           box-sizing: border-box;
         }
         .live-run-requirements > div[data-status="satisfied"] i,
         .live-run-timeline i[data-status="succeeded"] {
           border-color: rgba(255,255,255,0.8);
           background: rgba(255,255,255,0.8);
         }
         .live-run-requirements > div[data-status="weak"] i,
         .live-run-requirements > div[data-status="blocked"] i,
         .live-run-requirements > div[data-status="exhausted"] i,
         .live-run-timeline i[data-status="failed"] {
           border-style: dashed;
           opacity: .7;
         }
         .live-run-requirements span,
         .live-run-timeline span {
           min-width: 0;
           overflow: hidden;
           text-overflow: ellipsis;
           white-space: nowrap;
         }
         .live-run-requirements small {
           color: rgba(255,255,255,0.3);
           font: 500 8px/1 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: .05em;
           text-transform: uppercase;
         }
         .live-run-timeline > div {
           grid-template-columns: 8px minmax(0, 1fr);
           color: rgba(255,255,255,0.4);
           font-size: 9px;
         }
         .live-run-sources {
           display: grid;
           grid-template-columns: 58px minmax(0,1fr);
           gap: 8px;
           margin: 9px 0 0 21px;
           padding-top: 8px;
           border-top: 1px solid rgba(255,255,255,0.05);
         }
         .live-run-sources > span {
           color: rgba(255,255,255,0.3);
           font: 600 8px/1.5 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: .08em;
           text-transform: uppercase;
         }
         .live-run-sources > div {
           display: flex;
           flex-wrap: wrap;
           gap: 5px 10px;
           min-width: 0;
         }
         .live-run-sources a,
         .live-run-sources small {
           max-width: 230px;
           overflow: hidden;
           color: rgba(255,255,255,0.48);
           font-size: 9px;
           line-height: 1.4;
           text-decoration: none;
           text-overflow: ellipsis;
           white-space: nowrap;
         }
         .live-run-sources a:hover {
           color: rgba(255,255,255,0.82);
           text-decoration: underline;
         }
         .live-run-trace {
           display: flex;
           flex-wrap: wrap;
           gap: 5px 12px;
           margin-top: 8px;
           padding-left: 21px;
         }
         .live-run-trace > div {
           display: inline-flex;
           gap: 5px;
           min-width: 0;
           color: rgba(255,255,255,0.34);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 9px;
           line-height: 1.45;
         }
         .live-run-trace strong {
           max-width: 320px;
           overflow: hidden;
           color: rgba(255,255,255,0.56);
           font-weight: 500;
           text-overflow: ellipsis;
           white-space: nowrap;
         }

         /* ── Composer dock ──
            Row 1: [ Ask Echo anything ........ ] [ctx] [send]
            Row 2: [ Provider ▾ ] [mic][mon][viz] [ Model ▾ ]
         */
         .input-bar {
           margin-top: auto;
           display: flex;
           flex-direction: column;
           gap: 8px;
           padding-top: 10px;
           border-top: 1px solid rgba(255,255,255,0.06);
           min-width: 0;
           width: 100%;
           overflow: visible;
         }
         .input-row {
           display: flex;
           flex-direction: row;
           flex-wrap: nowrap;
           align-items: flex-end;
           gap: 8px;
           width: 100%;
           min-width: 0;
           overflow: visible;
         }
         /* Session strip + textarea share one column so edges always line up */
         .composer-input-stack {
           flex: 1 1 auto;
           min-width: 0;
           display: flex;
           flex-direction: column;
           gap: 0;
         }
         .session-folder-strip {
           display: inline-flex;
           align-items: center;
           flex-wrap: wrap;
           gap: 12px;
           width: fit-content;
           max-width: 100%;
           box-sizing: border-box;
           padding: 5px 10px;
           margin: 0;
           font-size: 10px;
           line-height: 1.3;
           color: rgba(255,255,255,0.48);
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           border: 1px solid rgba(255,255,255,0.10);
           border-bottom: none;
           background: rgba(16,16,16,0.5);
           border-radius: 3px 3px 0 0;
           position: relative;
           z-index: 1;
         }
         .session-folder-strip.is-drop-active {
           border-color: rgba(255,255,255,0.65);
           border-style: dashed;
           background: rgba(255,255,255,0.07);
         }
         .input-field {
           box-sizing: border-box;
           width: 100%;
           min-width: 0;
           background: rgba(255,255,255,0.03);
           border: 1px solid rgba(255, 255, 255, 0.12);
           border-radius: 0 3px 3px 3px;
           padding: 10px 14px;
           color: ${colors.text};
           font-size: 15px;
           outline: none;
           transition: border-color 0.15s ease, background 0.15s ease;
           box-shadow: none;
         }
         .input-field:focus {
           background: rgba(255,255,255,0.045);
           border-color: rgba(255,255,255,0.28);
         }
         textarea.input-field {
           min-height: 40px;
           max-height: 148px;
           height: 40px;
           resize: none;
           line-height: 1.4;
           overflow-y: auto;
           font-family: inherit;
         }
         .composer-trailing {
           display: flex;
           flex-direction: row;
           flex-wrap: nowrap;
           align-items: center;
           gap: 6px;
           flex: 0 0 auto;
           overflow: visible;
         }
         .composer-square,
         .send-button,
         .mic-button {
           width: 36px;
           height: 36px;
           flex: 0 0 36px;
           display: grid;
           place-items: center;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.14);
           background: rgba(255,255,255,0.03);
           color: #fff;
           cursor: pointer;
           transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
           box-shadow: none;
           padding: 0;
           box-sizing: border-box;
         }
         .send-button {
           width: 40px;
           height: 40px;
           flex: 0 0 40px;
           background: rgba(255,255,255,0.08);
           border-color: rgba(255,255,255,0.22);
         }
         .composer-square:hover:not(:disabled),
         .mic-button:hover:not(:disabled) {
           background: rgba(255,255,255,0.05);
           border-color: rgba(255,255,255,0.22);
         }
         .send-button:hover:not(:disabled) {
           background: rgba(255,255,255,0.14);
           border-color: rgba(255,255,255,0.4);
         }
         .composer-square:active:not(:disabled),
         .send-button:active:not(:disabled),
         .mic-button:active:not(:disabled) {
           background: rgba(255,255,255,0.1);
         }
         .mic-button.active {
            background: rgba(255,255,255,0.12);
            border-color: rgba(255,255,255,0.42);
            color: #fff;
          }
         .composer-square.active {
           background: rgba(255,255,255,0.1);
           border-color: rgba(255,255,255,0.28);
         }
         .context-meter-wrap {
           width: 40px;
           height: 40px;
           flex: 0 0 40px;
           display: grid;
           place-items: center;
           overflow: visible;
           position: relative;
           z-index: 5;
         }

         /* Bottom rail: [mic][mon][viz] [Provider] [Model] */
         .controls-row {
           display: flex;
           flex-direction: row;
           flex-wrap: wrap;
           align-items: stretch;
           gap: 6px;
           width: 100%;
           min-width: 0;
           background: transparent;
           border: 0;
           border-radius: 0;
           overflow: visible;
         }
         .composer-primary-controls {
           display: grid;
           grid-template-columns: auto minmax(112px, .75fr) minmax(156px, 1.25fr) minmax(98px, .6fr);
           flex: 1 1 570px;
           min-width: min(100%, 520px);
           overflow: hidden;
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 4px;
           background: #0a0a0a;
         }
         .composer-mode-controls {
           display: flex;
           align-items: stretch;
           gap: 4px;
           flex: 0 0 auto;
           min-height: 48px;
           padding: 4px;
           box-sizing: border-box;
           border: 1px solid rgba(255,255,255,0.1);
           border-radius: 4px;
           background: #0a0a0a;
         }
         .control-slot {
           display: flex;
           flex-direction: column;
           align-items: stretch;
           min-width: 0;
           background: #0a0a0a;
           position: relative;
         }
         .control-slot::before {
           content: attr(data-label);
           display: block;
           padding: 6px 10px 0;
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           font-size: 9px;
           font-weight: 600;
           letter-spacing: 0.12em;
           text-transform: uppercase;
           color: rgba(255,255,255,0.32);
           line-height: 1;
           white-space: nowrap;
           overflow: hidden;
           text-overflow: ellipsis;
         }
         .provider-slot {
           flex: 0 1 38%;
           min-width: 110px;
           max-width: 220px;
           border-right: 1px solid rgba(255,255,255,0.08);
         }
         .model-slot {
           flex: 1 1 auto;
           min-width: 120px;
           border-right: 1px solid rgba(255,255,255,0.08);
         }
         .composer-tools-slot {
           display: flex;
           flex-direction: row;
           flex-wrap: nowrap;
           align-items: center;
           justify-content: flex-start;
           gap: 5px;
           flex: 0 0 auto;
           background: #0a0a0a;
           padding: 0 8px;
           align-self: stretch;
           border-right: 1px solid rgba(255,255,255,0.08);
          }
          .voice-transport-status {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            max-width: 108px;
            min-width: 0;
            padding: 0 4px;
            color: rgba(255,255,255,.52);
            font: 550 9px/1 Inter, 'Segoe UI Variable', sans-serif;
            letter-spacing: .01em;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .voice-transport-status i {
            width: 5px;
            height: 5px;
            flex: 0 0 5px;
            border-radius: 50%;
            background: rgba(255,255,255,.52);
            box-shadow: 0 0 0 3px rgba(255,255,255,.045);
            transition: transform 90ms linear, background .15s ease;
          }
          .voice-transport-status[data-state="listening"],
          .voice-transport-status[data-state="speaking"] {
            color: rgba(255,255,255,.86);
          }
          .voice-transport-status[data-state="listening"] i,
          .voice-transport-status[data-state="speaking"] i {
            background: #fff;
          }
          .voice-transport-status[data-state="error"] {
            color: rgba(255,255,255,.58);
          }
         .composer-mode-button {
           min-width: 54px;
           height: 38px;
           display: inline-flex;
           align-items: center;
           justify-content: center;
           gap: 6px;
           padding: 0 9px;
           border: 1px solid transparent;
           border-radius: 3px;
           color: rgba(255,255,255,0.48);
           background: transparent;
           font: 550 10px/1 Inter, 'Segoe UI Variable', sans-serif;
           cursor: pointer;
           transition: color .15s ease, background .15s ease, border-color .15s ease;
         }
         .composer-mode-button svg {
           width: 14px;
           height: 14px;
           flex: 0 0 auto;
         }
         .composer-mode-button:hover {
           color: rgba(255,255,255,0.82);
           background: rgba(255,255,255,0.045);
         }
         .composer-mode-button.active {
           color: #fff;
           border-color: rgba(255,255,255,0.15);
           background: rgba(255,255,255,0.08);
         }
         .composer-mode-label {
           white-space: nowrap;
         }

         .icon-button, .provider-picker, .model-picker, .mode-picker, .toolbar-button {
           position: relative;
           overflow: hidden;
           background: transparent;
           border: 1px solid transparent;
           box-shadow: none;
           color: #fff;
           cursor: pointer;
           transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
         }
         .icon-button:hover:not(:disabled), .provider-picker:hover:not(:disabled), .model-picker:hover:not(:disabled), .mode-picker:hover:not(:disabled), .toolbar-button:hover:not(:disabled) {
           background: rgba(255,255,255,0.05);
         }
         .icon-button:active:not(:disabled), .provider-picker:active:not(:disabled), .model-picker:active:not(:disabled), .mode-picker:active:not(:disabled), .toolbar-button:active:not(:disabled) {
           background: rgba(255,255,255,0.07);
         }
         .icon-button {
           display: flex;
           align-items: center;
           justify-content: center;
           border-radius: 3px;
           border: 1px solid rgba(255,255,255,0.12);
         }
         .inline-switcher {
           display: flex;
           align-items: center;
           width: 100%;
           min-width: 0;
         }
         .switcher-dot {
           width: 6px;
           height: 6px;
           border-radius: 50%;
           background: #475569;
           flex: 0 0 auto;
         }
         .switcher-dot.online { background: #22c55e; box-shadow: 0 0 8px #22c55e44; }
         .switcher-dot.offline { background: #ef4444; box-shadow: 0 0 8px #ef444444; }

         .provider-picker, .model-picker, .mode-picker, .toolbar-button {
           height: 34px;
           border-radius: 0;
           font-size: 11px;
           font-weight: 500;
           outline: none;
           padding: 0 10px 6px;
           line-height: 1.2;
           min-width: 0;
           font-family: 'JetBrains Mono', ui-monospace, monospace;
           letter-spacing: 0.01em;
         }
         .provider-picker, .model-picker, .mode-picker {
           width: 100%;
           max-width: none;
           appearance: none;
           background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='rgba(255,255,255,0.45)' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
           background-repeat: no-repeat;
           background-position: right 8px center;
           padding-right: 24px;
         }
         .provider-picker option, .model-picker option, .mode-picker option {
           background: #111;
           color: ${colors.text};
         }
         select {
           color: ${colors.text};
         }
         select option {
           background: #111;
           color: ${colors.text};
         }

         @media (max-width: 760px) {
           .live-run-header {
             align-items: flex-start;
             flex-direction: column;
           }
           .live-run-actions {
             width: 100%;
           }
           .live-run-action.is-toggle {
             margin-left: auto;
           }
           .live-run-details {
             grid-template-columns: minmax(0, 1fr);
             padding-left: 0;
           }
           .live-run-trace {
             padding-left: 0;
           }

           .composer-tools-slot {
             min-height: 46px;
             border-right: 1px solid rgba(255,255,255,0.08);
             border-bottom: 0;
           }
           .provider-slot,
           .model-slot,
           .effort-slot {
             min-width: 0;
           }

         }

       `;

