//! In-app updates from GitHub Releases.
//!
//! The release script publishes a signed installer plus `latest.json`. The app
//! checks that file, downloads the installer, verifies its signature against the
//! public key in `tauri.conf.json`, stops the Python service so no files are
//! locked, and hands over to the installer, which restarts EchoSpeak.

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::UpdaterExt;

use crate::backend::{self, DesktopState};

#[derive(Clone, Serialize)]
pub struct UpdateInfo {
    pub configured: bool,
    pub available: bool,
    pub current_version: String,
    pub version: String,
    pub notes: String,
    pub date: String,
}

#[derive(Clone, Serialize)]
struct UpdateProgress {
    phase: &'static str,
    downloaded: u64,
    total: Option<u64>,
}

/// Builds without a signing key have an empty public key and cannot verify updates.
fn updates_configured(app: &AppHandle) -> bool {
    app.config()
        .plugins
        .0
        .get("updater")
        .and_then(|value| value.get("pubkey"))
        .and_then(|value| value.as_str())
        .is_some_and(|key| !key.trim().is_empty())
}

fn readable(error: impl std::fmt::Display) -> String {
    let text = error.to_string();
    if text.contains("404") || text.contains("Could not fetch a valid release JSON") {
        "No published update was found. Check your internet connection and try again later.".into()
    } else {
        format!("Update failed: {text}")
    }
}

#[tauri::command]
pub async fn check_for_update(app: AppHandle) -> Result<UpdateInfo, String> {
    let current = app.package_info().version.to_string();
    if !updates_configured(&app) {
        return Ok(UpdateInfo {
            configured: false,
            available: false,
            current_version: current.clone(),
            version: current,
            notes: String::new(),
            date: String::new(),
        });
    }
    let update = app
        .updater()
        .map_err(readable)?
        .check()
        .await
        .map_err(readable)?;
    Ok(match update {
        Some(update) => UpdateInfo {
            configured: true,
            available: true,
            current_version: current,
            version: update.version.clone(),
            notes: update.body.clone().unwrap_or_default(),
            date: update.date.map(|date| date.to_string()).unwrap_or_default(),
        },
        None => UpdateInfo {
            configured: true,
            available: false,
            current_version: current.clone(),
            version: current,
            notes: String::new(),
            date: String::new(),
        },
    })
}

#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    if !updates_configured(&app) {
        return Err("Updates are not set up in this build.".into());
    }
    let update = app
        .updater()
        .map_err(readable)?
        .check()
        .await
        .map_err(readable)?
        .ok_or_else(|| "EchoSpeak is already up to date.".to_string())?;

    let progress = app.clone();
    let mut downloaded: u64 = 0;
    let bytes = update
        .download(
            move |chunk, total| {
                downloaded += chunk as u64;
                let _ = progress.emit(
                    "desktop-update-progress",
                    UpdateProgress { phase: "downloading", downloaded, total },
                );
            },
            || {},
        )
        .await
        .map_err(readable)?;

    let size = bytes.len() as u64;
    let _ = app.emit(
        "desktop-update-progress",
        UpdateProgress { phase: "installing", downloaded: size, total: Some(size) },
    );
    // The installer replaces the Python service's files, so stop it first.
    let state = app.try_state::<DesktopState>().map(|state| state.inner().clone());
    if let Some(state) = &state {
        backend::shutdown_backend(state);
    }
    // On Windows this starts the installer and exits; it only returns on failure.
    if let Err(error) = update.install(bytes) {
        if let Some(state) = state {
            backend::restart_backend(app.clone(), state);
        }
        return Err(readable(error));
    }
    app.restart();
}
