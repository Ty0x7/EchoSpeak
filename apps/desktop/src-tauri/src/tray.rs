//! Keep running in the system tray, and show Windows notifications.
//!
//! When the owner turns on Settings › General › "Keep running in the tray when closed",
//! closing the main window hides it instead of quitting. EchoSpeak stays in the tray
//! (under "Show hidden icons"), so the local service keeps running and reminders fire.
//! The tray menu reopens the window or quits for real.
//!
//! The setting and the notifications come from one poll of the local service
//! (`POST /lean/notifications/claim`) every few seconds: it returns the reminders and
//! routine results to show as Windows notifications, and whether close-to-tray is on.
//! Doing this here rather than in the webview keeps it working while the window is
//! hidden (Chromium throttles hidden pages) or after a renderer crash.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::backend::DesktopState;

const TRAY_ID: &str = "echospeak-tray";
const POLL_EVERY: Duration = Duration::from_secs(5);

static CLOSE_TO_TRAY: AtomicBool = AtomicBool::new(false);

pub fn close_to_tray() -> bool {
    CLOSE_TO_TRAY.load(Ordering::Relaxed)
}

/// Bring the main window back (tray click, "Open EchoSpeak", a second launch).
pub fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Hide every EchoSpeak window; the app and its local service keep running in the tray.
pub fn hide_to_tray(app: &AppHandle) {
    for label in ["settings", "setup", "companion", "main"] {
        if let Some(window) = app.get_webview_window(label) {
            let _ = window.hide();
        }
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open EchoSpeak", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit EchoSpeak", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip("EchoSpeak")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main(app),
            // A real quit: the exit handler in lib.rs stops the local service.
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

/// Apply the owner's setting: show the tray icon and hide-on-close, or remove both.
pub fn set_close_to_tray(app: &AppHandle, enabled: bool) {
    let was = CLOSE_TO_TRAY.swap(enabled, Ordering::Relaxed);
    let present = app.tray_by_id(TRAY_ID).is_some();
    if enabled && !present {
        if let Err(error) = build_tray(app) {
            log::error!("Could not create the EchoSpeak tray icon: {error}");
            // Without an icon a hidden window could never be reopened, so don't hide on close.
            CLOSE_TO_TRAY.store(false, Ordering::Relaxed);
        }
    } else if !enabled && present {
        let _ = app.remove_tray_by_id(TRAY_ID);
        if was {
            // Turned off while the window may be hidden: bring it back so it can't be lost.
            show_main(app);
        }
    }
}

/// One claim from the local service: notifications to show, and the close-to-tray setting.
fn claim(port: u16, session_key: &str) -> Option<serde_json::Value> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(500)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    let request = format!(
        "POST /lean/notifications/claim HTTP/1.1\r\nHost: 127.0.0.1\r\nX-EchoSpeak-Key: {session_key}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.starts_with("HTTP/1.1 200") {
        return None;
    }
    let (_, body) = response.split_once("\r\n\r\n")?;
    serde_json::from_str(body).ok()
}

fn port_of(api_base: &str) -> Option<u16> {
    api_base.rsplit_once(':').and_then(|(_, port)| port.parse().ok())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn claim_sends_the_session_key_and_reads_items_and_the_tray_setting() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0u8; 1024];
            let read = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..read]).to_string();
            let body = r#"{"items":[{"seq":1,"title":"Reminder","body":"Raptors vs Timberwolves"}],"close_to_tray":true}"#;
            let reply = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(reply.as_bytes()).unwrap();
            request
        });
        let payload = claim(port, "secret-key").expect("a parsed claim");
        let request = server.join().unwrap();
        assert!(request.starts_with("POST /lean/notifications/claim HTTP/1.1"));
        assert!(request.contains("X-EchoSpeak-Key: secret-key"));
        assert_eq!(payload["close_to_tray"], serde_json::Value::Bool(true));
        assert_eq!(payload["items"][0]["body"], "Raptors vs Timberwolves");
    }

    #[test]
    fn nothing_is_claimed_when_the_service_is_not_there() {
        let port = TcpListener::bind(("127.0.0.1", 0)).unwrap().local_addr().unwrap().port();
        assert!(claim(port, "k").is_none());
        assert_eq!(port_of("http://127.0.0.1:8765"), Some(8765));
    }
}

/// Poll the local service for as long as the app runs.
pub fn start_poller(app: AppHandle, state: DesktopState) {
    std::thread::spawn(move || loop {
        std::thread::sleep(POLL_EVERY);
        let runtime = state.snapshot();
        if runtime.backend_phase != "ready" {
            continue;
        }
        let Some(port) = port_of(&runtime.api_base) else {
            continue;
        };
        let Some(payload) = claim(port, &runtime.api_session_key) else {
            continue;
        };
        let enabled = payload
            .get("close_to_tray")
            .and_then(|value| value.as_bool())
            .unwrap_or(false);
        if enabled != close_to_tray() || enabled != app.tray_by_id(TRAY_ID).is_some() {
            let handle = app.clone();
            // Tray icons belong to the main (event loop) thread.
            let _ = app.run_on_main_thread(move || set_close_to_tray(&handle, enabled));
        }
        for item in payload
            .get("items")
            .and_then(|value| value.as_array())
            .into_iter()
            .flatten()
        {
            let title = item.get("title").and_then(|v| v.as_str()).unwrap_or("EchoSpeak");
            let body = item.get("body").and_then(|v| v.as_str()).unwrap_or("");
            if let Err(error) = app.notification().builder().title(title).body(body).show() {
                log::warn!("Could not show a Windows notification: {error}");
            }
        }
    });
}
