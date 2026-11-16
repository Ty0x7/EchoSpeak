use serde::Serialize;
use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};
#[cfg(windows)]
use std::process::Command as StdCommand;
#[cfg(unix)]
use std::time::Instant;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};
use uuid::Uuid;

const MAX_AUTOMATIC_RESTARTS: u32 = 3;
const HEALTH_ATTEMPTS: usize = 360;
const HEALTH_INTERVAL_MS: u64 = 250;
const PROCESS_MONITOR_INTERVAL_MS: u64 = 500;
/// POSIX: how long a SIGTERM'd backend may run its graceful shutdown before SIGKILL.
#[cfg(unix)]
const GRACEFUL_STOP_MS: u64 = 5_000;
/// POSIX: how often an attached (not spawned) backend is checked.
#[cfg(unix)]
const ATTACHED_MONITOR_INTERVAL_MS: u64 = 1_000;

#[derive(Debug)]
struct Supervisor {
    phase: String,
    detail: String,
    api_base: String,
    api_session_key: String,
    data_dir: PathBuf,
    log_dir: PathBuf,
    child: Option<CommandChild>,
    child_pid: Option<u32>,
    desired_running: bool,
    generation: u64,
    instance_id: String,
    restart_count: u32,
    consecutive_failures: u32,
    startup_failure: Option<String>,
    /// POSIX: pid of an already-running backend for the same data folder that this
    /// host attached to instead of spawning one. This host never stops it.
    attached_pid: Option<u32>,
}

#[derive(Clone)]
pub struct DesktopState {
    inner: Arc<Mutex<Supervisor>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DesktopRuntime {
    pub environment: &'static str,
    pub api_base: String,
    pub api_session_key: String,
    pub backend_phase: String,
    pub backend_detail: String,
    pub backend_pid: Option<u32>,
    pub connection_generation: u64,
    pub instance_id: String,
    pub restart_count: u32,
    pub max_automatic_restarts: u32,
    pub data_dir: String,
    pub log_dir: String,
    /// True when the host attached to a backend it did not start (POSIX shared backend).
    pub backend_attached: bool,
}

impl DesktopState {
    pub fn new(data_dir: PathBuf, log_dir: PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(&data_dir)
            .map_err(|error| format!("Could not create desktop data directory: {error}"))?;
        std::fs::create_dir_all(&log_dir)
            .map_err(|error| format!("Could not create desktop log directory: {error}"))?;
        let data_dir = data_dir
            .canonicalize()
            .map_err(|error| format!("Could not resolve desktop data directory: {error}"))?;
        let log_dir = log_dir
            .canonicalize()
            .map_err(|error| format!("Could not resolve desktop log directory: {error}"))?;
        let port = reserve_loopback_port()?;
        let api_session_key = new_session_key();
        Ok(Self {
            inner: Arc::new(Mutex::new(Supervisor {
                phase: "starting".into(),
                detail: "Preparing the local EchoSpeak service".into(),
                api_base: format!("http://127.0.0.1:{port}"),
                api_session_key,
                data_dir,
                log_dir,
                child: None,
                child_pid: None,
                desired_running: true,
                generation: 0,
                instance_id: String::new(),
                restart_count: 0,
                consecutive_failures: 0,
                startup_failure: None,
                attached_pid: None,
            })),
        })
    }

    pub fn snapshot(&self) -> DesktopRuntime {
        let supervisor = self
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        DesktopRuntime {
            environment: "desktop",
            api_base: supervisor.api_base.clone(),
            api_session_key: supervisor.api_session_key.clone(),
            backend_phase: supervisor.phase.clone(),
            backend_detail: supervisor.detail.clone(),
            backend_pid: supervisor.child_pid.or(supervisor.attached_pid),
            connection_generation: supervisor.generation,
            instance_id: supervisor.instance_id.clone(),
            restart_count: supervisor.restart_count,
            max_automatic_restarts: MAX_AUTOMATIC_RESTARTS,
            data_dir: supervisor.data_dir.to_string_lossy().into_owned(),
            log_dir: supervisor.log_dir.to_string_lossy().into_owned(),
            backend_attached: supervisor.attached_pid.is_some(),
        }
    }

    pub fn log_dir(&self) -> PathBuf {
        self.inner
            .lock()
            .expect("desktop supervisor mutex poisoned")
            .log_dir
            .clone()
    }
}

fn new_session_key() -> String {
    format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
}

/// POSIX: give the host its own fresh port and key again (an attached backend is gone).
#[cfg(unix)]
fn reset_own_endpoint(supervisor: &mut Supervisor) {
    match reserve_loopback_port() {
        Ok(port) => supervisor.api_base = format!("http://127.0.0.1:{port}"),
        Err(error) => log::error!("{error}"),
    }
    supervisor.api_session_key = new_session_key();
}

fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("Could not reserve a loopback API port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("Could not inspect the reserved API port: {error}"))
}

fn port_from_api_base(api_base: &str) -> Result<u16, String> {
    api_base
        .rsplit_once(':')
        .and_then(|(_, port)| port.parse::<u16>().ok())
        .ok_or_else(|| "Desktop API base does not contain a valid port".to_string())
}

fn health_ready(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(350)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok()
        && response.starts_with("HTTP/1.1 200")
        && response.contains("\"status\":\"healthy\"")
}

fn product_ready(port: u16, session_key: &str, instance_id: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(750)));
    let request = format!(
        "GET /startup/readiness HTTP/1.1\r\nHost: 127.0.0.1\r\nX-EchoSpeak-Key: {session_key}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() || !response.starts_with("HTTP/1.1 200") {
        return false;
    }
    let Some((_, body)) = response.split_once("\r\n\r\n") else {
        return false;
    };
    let Ok(payload) = serde_json::from_str::<serde_json::Value>(body) else {
        return false;
    };
    payload.get("core_ready").and_then(|value| value.as_bool()) == Some(true)
        && payload
            .get("protocol_version")
            .and_then(|value| value.as_str())
            == Some("1")
        && payload.get("instance_id").and_then(|value| value.as_str()) == Some(instance_id)
}

/// POSIX attach: the authenticated readiness payload's instance id, if the key is
/// accepted and the protocol is the one the renderer speaks.
#[cfg(unix)]
fn readiness_instance(port: u16, session_key: &str) -> Option<String> {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(500)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(3000)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(750)));
    let request = format!(
        "GET /startup/readiness HTTP/1.1\r\nHost: 127.0.0.1\r\nX-EchoSpeak-Key: {session_key}\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;
    if !response.starts_with("HTTP/1.1 200") {
        return None;
    }
    let (_, body) = response.split_once("\r\n\r\n")?;
    let payload = serde_json::from_str::<serde_json::Value>(body).ok()?;
    if payload.get("protocol_version").and_then(|value| value.as_str()) != Some("1") {
        return None;
    }
    Some(
        payload
            .get("instance_id")
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string(),
    )
}

fn request_backend_exit(port: u16, session_key: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(350)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(750)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(750)));
    let body = "{\"delay_seconds\":0}";
    let request = format!(
        "POST /admin/restart HTTP/1.1\r\nHost: 127.0.0.1\r\nX-EchoSpeak-Key: {session_key}\r\nX-Admin-Key: {session_key}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200")
}

#[cfg(windows)]
fn process_is_alive(pid: u32) -> bool {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, WAIT_TIMEOUT},
        System::Threading::{OpenProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE},
    };

    unsafe {
        let handle = OpenProcess(PROCESS_SYNCHRONIZE, 0, pid);
        if handle.is_null() {
            return false;
        }
        let result = WaitForSingleObject(handle, 0);
        let _ = CloseHandle(handle);
        result == WAIT_TIMEOUT
    }
}

#[cfg(unix)]
fn process_is_alive(pid: u32) -> bool {
    let Ok(raw_pid) = libc::pid_t::try_from(pid) else {
        return false;
    };
    if raw_pid <= 0 {
        return false;
    }
    // A child that exited but is not yet reaped is a zombie: dead for our purposes.
    #[cfg(target_os = "linux")]
    if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
        let state = stat
            .rsplit_once(')')
            .and_then(|(_, rest)| rest.trim_start().chars().next());
        return !matches!(state, Some('Z') | Some('X'));
    }
    // SAFETY: signal 0 only probes for existence and permission.
    let result = unsafe { libc::kill(raw_pid, 0) };
    result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

#[cfg(not(any(windows, unix)))]
fn process_is_alive(_pid: u32) -> bool {
    // Other targets retain the shell termination-event path until they gain an
    // equivalent native probe.
    true
}

/// POSIX: attach to a backend that is already running for this data folder (for
/// example one the terminal client started) instead of spawning a second one, which
/// would fail on the backend's single-writer lock. Returns false to spawn as usual.
#[cfg(unix)]
fn try_attach(app: &AppHandle, state: &DesktopState) -> bool {
    let data_dir = {
        let Ok(supervisor) = state.inner.lock() else {
            return false;
        };
        if supervisor.child.is_some()
            || supervisor.attached_pid.is_some()
            || !supervisor.desired_running
        {
            return false;
        }
        supervisor.data_dir.clone()
    };
    let path = crate::endpoint::runtime_dir(&data_dir).join(crate::endpoint::FILENAME);
    let endpoint = match crate::endpoint::load(&path) {
        Ok(Some(endpoint)) => endpoint,
        Ok(None) => return false,
        Err(reason) => {
            log::warn!("Not attaching to the backend in {}: {reason}", path.display());
            return false;
        }
    };
    let refuse = |reason: String| {
        log::info!("Not attaching to the backend in {}: {reason}", path.display());
        false
    };
    if !process_is_alive(endpoint.pid) {
        return refuse(format!("its process {} is not running (stale file)", endpoint.pid));
    }
    if let Err(reason) = crate::endpoint::matches_desktop(&endpoint, &data_dir) {
        return refuse(reason);
    }
    if !health_ready(endpoint.port) {
        return refuse(format!("port {} does not answer /health", endpoint.port));
    }
    let Some(instance_id) = readiness_instance(endpoint.port, &endpoint.auth_key) else {
        return refuse("/startup/readiness refused its key or protocol".into());
    };
    let generation = {
        let Ok(mut supervisor) = state.inner.lock() else {
            return false;
        };
        if supervisor.child.is_some()
            || supervisor.attached_pid.is_some()
            || !supervisor.desired_running
        {
            return false;
        }
        supervisor.generation += 1;
        supervisor.instance_id = instance_id;
        supervisor.api_base = format!("http://127.0.0.1:{}", endpoint.port);
        supervisor.api_session_key = endpoint.auth_key.clone();
        supervisor.attached_pid = Some(endpoint.pid);
        supervisor.child_pid = None;
        supervisor.phase = "ready".into();
        supervisor.detail = format!(
            "Using the EchoSpeak service already running for this data folder (pid {})",
            endpoint.pid
        );
        supervisor.consecutive_failures = 0;
        supervisor.startup_failure = None;
        supervisor.generation
    };
    log::info!(
        "Attached to the running EchoSpeak backend {} (pid {}, port {}, data {}); the app will not stop it",
        endpoint.version,
        endpoint.pid,
        endpoint.port,
        data_dir.display()
    );
    monitor_attached(app.clone(), state.clone(), generation, endpoint.pid, endpoint.port);
    true
}

/// POSIX: watch an attached backend. When it goes away, start the app's own backend
/// (which first looks again for one to attach to).
#[cfg(unix)]
fn monitor_attached(app: AppHandle, state: DesktopState, generation: u64, pid: u32, port: u16) {
    tauri::async_runtime::spawn(async move {
        let mut misses = 0_u32;
        loop {
            tokio::time::sleep(Duration::from_millis(ATTACHED_MONITOR_INTERVAL_MS)).await;
            let still_current = {
                let supervisor = state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                supervisor.generation == generation
                    && supervisor.desired_running
                    && supervisor.attached_pid == Some(pid)
            };
            if !still_current {
                return;
            }
            let alive = process_is_alive(pid);
            if alive && health_ready(port) {
                misses = 0;
                continue;
            }
            misses += 1;
            // A busy backend can miss a probe; a dead one cannot come back.
            if alive && misses < 5 {
                continue;
            }
            let reason = format!("The shared EchoSpeak service (pid {pid}) stopped");
            {
                let mut supervisor = state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                if supervisor.generation != generation
                    || supervisor.attached_pid != Some(pid)
                    || !supervisor.desired_running
                {
                    return;
                }
                supervisor.attached_pid = None;
                supervisor.generation += 1;
                supervisor.phase = "recovering".into();
                supervisor.detail = format!("{reason}. Starting the app's own service.");
                reset_own_endpoint(&mut supervisor);
            }
            log::warn!("{reason}; starting the app's own backend");
            if let Err(error) = launch_backend(app.clone(), state.clone()) {
                schedule_restart(app, state, error);
            }
            return;
        }
    });
}

pub fn launch_backend(app: AppHandle, state: DesktopState) -> Result<(), String> {
    #[cfg(unix)]
    if try_attach(&app, &state) {
        return Ok(());
    }
    let (generation, instance_id, api_base, api_session_key, data_dir, log_dir) = {
        let mut supervisor = state
            .inner
            .lock()
            .map_err(|_| "Desktop supervisor lock failed")?;
        if supervisor.child.is_some() || supervisor.attached_pid.is_some() {
            return Ok(());
        }
        if !supervisor.desired_running {
            return Err("Backend launch was cancelled".into());
        }
        supervisor.generation += 1;
        supervisor.instance_id = Uuid::new_v4().simple().to_string();
        supervisor.phase = "starting".into();
        supervisor.detail = "Starting the local EchoSpeak service".into();
        (
            supervisor.generation,
            supervisor.instance_id.clone(),
            supervisor.api_base.clone(),
            supervisor.api_session_key.clone(),
            supervisor.data_dir.clone(),
            supervisor.log_dir.clone(),
        )
    };

    let port = port_from_api_base(&api_base)?;
    let parent_pid = std::process::id().to_string();
    // One-folder backend bundled as a resource (see build-sidecar.ps1). The
    // shell plugin spawns it with CREATE_NO_WINDOW, so no console appears.
    let backend_exe = app
        .path()
        .resource_dir()
        .map_err(|error| format!("Could not resolve the app resource folder: {error}"))?
        .join("backend")
        .join(if cfg!(windows) {
            "echospeak-backend.exe"
        } else {
            "echospeak-backend"
        });
    if !backend_exe.is_file() {
        return Err(format!(
            "The packaged EchoSpeak backend is missing at {}",
            backend_exe.display()
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&backend_exe)
            .map(|metadata| metadata.permissions().mode())
            .unwrap_or(0);
        if mode & 0o111 == 0 {
            return Err(format!(
                "The packaged EchoSpeak backend at {} is not executable (mode {:o}). Reinstall EchoSpeak.",
                backend_exe.display(),
                mode & 0o7777
            ));
        }
    }
    let command = app
        .shell()
        .command(backend_exe)
        .args([
            "--host".to_string(),
            "127.0.0.1".to_string(),
            "--port".to_string(),
            port.to_string(),
            "--parent-pid".to_string(),
            parent_pid,
        ])
        .current_dir(&data_dir)
        .env("ECHOSPEAK_RUNTIME_KIND", "desktop")
        .env("ECHOSPEAK_DESKTOP_INSTANCE_ID", &instance_id)
        .env("ECHOSPEAK_DATA_DIR", &data_dir)
        .env("ECHOSPEAK_LOGS_DIR", &log_dir)
        .env("SOUL_PATH", data_dir.join("SOUL.md"))
        .env("API_HOST", "127.0.0.1")
        .env("API_PORT", port.to_string())
        .env("API_AUTH_ENABLED", "true")
        .env("API_AUTH_LOCALHOST_BYPASS", "false")
        .env("API_AUTH_KEY", &api_session_key)
        .env("ADMIN_API_KEY", &api_session_key)
        .env("API_TRUST_PROXY_HEADERS", "false")
        .env("PYTHONUNBUFFERED", "1");

    let (mut events, child) = command
        .spawn()
        .map_err(|error| format!("Could not start the packaged EchoSpeak backend: {error}"))?;
    let child_pid = child.pid();
    {
        let mut supervisor = state
            .inner
            .lock()
            .map_err(|_| "Desktop supervisor lock failed")?;
        if supervisor.generation != generation || !supervisor.desired_running {
            let _ = child.kill();
            return Err("Backend launch was superseded".into());
        }
        supervisor.child_pid = Some(child_pid);
        supervisor.child = Some(child);
        supervisor.detail = "Waiting for the local EchoSpeak service".into();
        supervisor.startup_failure = None;
    }

    let event_app = app.clone();
    let event_state = state.clone();
    let event_session_key = api_session_key.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    log::info!(target: "echospeak_backend", "{}", String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Stderr(bytes) => {
                    forward_backend_stderr(&bytes);
                    if let Some(cause) = permanent_startup_failure(&String::from_utf8_lossy(&bytes))
                    {
                        if let Ok(mut supervisor) = event_state.inner.lock() {
                            if supervisor.generation == generation && supervisor.phase != "ready" {
                                supervisor.startup_failure = Some(cause);
                            }
                        }
                    }
                }
                CommandEvent::Error(error) => {
                    log::error!(target: "echospeak_backend", "{error}");
                }
                CommandEvent::Terminated(payload) => {
                    let reason = format!(
                        "Local service exited{}{}",
                        payload
                            .code
                            .map(|code| format!(" with code {code}"))
                            .unwrap_or_default(),
                        payload
                            .signal
                            .map(|signal| format!(" after signal {signal}"))
                            .unwrap_or_default()
                    );
                    let current = {
                        let mut supervisor = event_state
                            .inner
                            .lock()
                            .expect("desktop supervisor mutex poisoned");
                        if supervisor.generation != generation {
                            false
                        } else {
                            supervisor.child = None;
                            supervisor.child_pid = None;
                            supervisor.phase = "recovering".into();
                            supervisor.detail = reason.clone();
                            supervisor.desired_running
                        }
                    };
                    if current {
                        // PyInstaller one-file mode has a bootloader parent and
                        // a worker child. If only the tracked bootloader exits,
                        // ask the still-healthy authenticated worker to exit so
                        // recovery cannot create a competing orphan service.
                        if health_ready(port) {
                            let _ = request_backend_exit(port, &event_session_key);
                        }
                        schedule_restart(event_app.clone(), event_state.clone(), reason);
                    }
                    return;
                }
                _ => {}
            }
        }
    });

    // PyInstaller one-file mode keeps stdout/stderr owned by its worker. If the
    // tracked bootloader alone crashes, the shell event channel therefore stays
    // open. Monitor the process handle independently so the host can retire the
    // authenticated worker before it starts a replacement generation.
    let monitor_app = app.clone();
    let monitor_state = state.clone();
    let monitor_session_key = api_session_key.clone();
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_millis(PROCESS_MONITOR_INTERVAL_MS)).await;
            let still_current = {
                let supervisor = monitor_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                supervisor.generation == generation
                    && supervisor.desired_running
                    && supervisor.child_pid == Some(child_pid)
            };
            if !still_current {
                return;
            }
            if process_is_alive(child_pid) {
                continue;
            }

            let reason = format!("Tracked local service process {child_pid} exited unexpectedly");
            let Some(child) = take_failed_generation(&monitor_state, generation, &reason) else {
                return;
            };

            // Invalidating the generation first makes a later shell event inert,
            // so one crash consumes one recovery attempt. The worker also watches
            // this bootloader, while the authenticated route is a healthy-worker
            // fallback. Allow either path to release the port before replacement.
            if product_ready(port, &api_session_key, &instance_id) {
                let _ = request_backend_exit(port, &monitor_session_key);
            }
            tokio::time::sleep(Duration::from_millis(750)).await;
            if health_ready(port) {
                let _ = request_backend_exit(port, &monitor_session_key);
                tokio::time::sleep(Duration::from_millis(750)).await;
            }
            if let Some(child) = child {
                terminate_process_tree(child);
            }
            if health_ready(port) {
                let mut supervisor = monitor_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                supervisor.phase = "failed".into();
                supervisor.detail = format!(
                    "{reason}. The prior authenticated worker could not be retired; replacement was blocked."
                );
                return;
            }
            schedule_restart(monitor_app, monitor_state, reason);
            return;
        }
    });

    let health_app = app;
    let health_state = state;
    tauri::async_runtime::spawn(async move {
        for _ in 0..HEALTH_ATTEMPTS {
            let still_current = {
                let supervisor = health_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                supervisor.generation == generation && supervisor.desired_running
            };
            if !still_current {
                return;
            }
            if health_ready(port) {
                let mut supervisor = health_state
                    .inner
                    .lock()
                    .expect("desktop supervisor mutex poisoned");
                if supervisor.generation == generation {
                    supervisor.phase = "ready".into();
                    supervisor.detail = "Local service is ready".into();
                    supervisor.consecutive_failures = 0;
                    supervisor.startup_failure = None;
                }
                return;
            }
            tokio::time::sleep(Duration::from_millis(HEALTH_INTERVAL_MS)).await;
        }
        fail_generation(
            health_app,
            health_state,
            generation,
            "Local service did not become healthy within 90 seconds".into(),
        );
    });

    Ok(())
}

fn backend_stderr_level(line: &str) -> log::Level {
    let upper = line.trim_start().to_ascii_uppercase();
    if upper.starts_with("CRITICAL")
        || upper.starts_with("ERROR")
        || upper.contains("| CRITICAL ")
        || upper.contains("| ERROR ")
    {
        log::Level::Error
    } else if upper.starts_with("WARNING")
        || upper.starts_with("WARN")
        || upper.contains("| WARNING ")
        || upper.contains("| WARN ")
    {
        log::Level::Warn
    } else if upper.starts_with("DEBUG") || upper.contains("| DEBUG ") {
        log::Level::Debug
    } else if upper.starts_with("INFO") || upper.contains("| INFO ") {
        log::Level::Info
    } else {
        // Unknown stderr remains warning-level; only recognized Python/uvicorn
        // severity formats are safely downgraded.
        log::Level::Warn
    }
}

fn forward_backend_stderr(bytes: &[u8]) {
    for line in String::from_utf8_lossy(bytes).lines() {
        if line.trim().is_empty() {
            continue;
        }
        match backend_stderr_level(line) {
            log::Level::Error => log::error!(target: "echospeak_backend", "{line}"),
            log::Level::Warn => log::warn!(target: "echospeak_backend", "{line}"),
            log::Level::Debug => log::debug!(target: "echospeak_backend", "{line}"),
            _ => log::info!(target: "echospeak_backend", "{line}"),
        }
    }
}

fn fail_generation(app: AppHandle, state: DesktopState, generation: u64, reason: String) {
    let Some(child) = take_failed_generation(&state, generation, &reason) else {
        return;
    };
    if let Some(child) = child {
        terminate_process_tree(child);
    }
    schedule_restart(app, state, reason);
}

fn take_failed_generation(
    state: &DesktopState,
    generation: u64,
    reason: &str,
) -> Option<Option<CommandChild>> {
    let mut supervisor = state
        .inner
        .lock()
        .expect("desktop supervisor mutex poisoned");
    if supervisor.generation != generation || !supervisor.desired_running {
        return None;
    }
    supervisor.generation += 1;
    supervisor.phase = "recovering".into();
    supervisor.detail = reason.into();
    supervisor.child_pid = None;
    Some(supervisor.child.take())
}

/// The backend's single-writer lock (agent/state.py) is held by another process.
const DATA_FOLDER_LOCKED: &str = "durable state is already locked by another process";

fn permanent_startup_failure(line: &str) -> Option<String> {
    if line.contains("ImportError:")
        || line.contains("ModuleNotFoundError:")
        || line.contains(DATA_FOLDER_LOCKED)
    {
        Some(line.trim().chars().take(500).collect())
    } else {
        None
    }
}

fn schedule_restart(app: AppHandle, state: DesktopState, reason: String) {
    let delay_ms = {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        if !supervisor.desired_running {
            return;
        }
        if let Some(cause) = &supervisor.startup_failure {
            supervisor.detail = if cause.contains(DATA_FOLDER_LOCKED) {
                format!("Another EchoSpeak service is already using this data folder ({}) and the app could not share it: {cause}. Stop that service, or run it with an API key so the app can attach to it, then restart.", supervisor.data_dir.display())
            } else {
                format!("The installed backend could not load: {cause}. Install the latest complete EchoSpeak installer. Your data is preserved; restarting this bundle cannot repair missing dependencies.")
            };
            supervisor.phase = "failed".into();
            return;
        }
        if supervisor.consecutive_failures >= MAX_AUTOMATIC_RESTARTS {
            supervisor.phase = "failed".into();
            supervisor.detail = format!("{reason}. Automatic recovery is exhausted.");
            return;
        }
        supervisor.consecutive_failures += 1;
        supervisor.restart_count += 1;
        supervisor.phase = "recovering".into();
        supervisor.detail = format!(
            "{reason}. Recovery attempt {}/{} is queued.",
            supervisor.consecutive_failures, MAX_AUTOMATIC_RESTARTS
        );
        500_u64 * (1_u64 << (supervisor.consecutive_failures - 1))
    };

    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(Duration::from_millis(delay_ms)).await;
        if let Err(error) = launch_backend(app.clone(), state.clone()) {
            log::error!("Desktop backend recovery failed: {error}");
            schedule_restart(app, state, error);
        }
    });
}

pub fn recover_from_launch_failure(app: AppHandle, state: DesktopState, reason: String) {
    {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        supervisor.phase = "recovering".into();
        supervisor.detail = reason.clone();
        supervisor.child = None;
        supervisor.child_pid = None;
    }
    schedule_restart(app, state, reason);
}

pub fn restart_backend(app: AppHandle, state: DesktopState) -> DesktopRuntime {
    // An attached backend belongs to someone else: reconnect (re-attach while it is
    // still there, otherwise start the app's own) instead of stopping it.
    #[cfg(unix)]
    {
        let was_attached = {
            let mut supervisor = state
                .inner
                .lock()
                .expect("desktop supervisor mutex poisoned");
            if supervisor.attached_pid.take().is_some() {
                supervisor.desired_running = true;
                supervisor.generation += 1;
                supervisor.consecutive_failures = 0;
                supervisor.startup_failure = None;
                supervisor.phase = "recovering".into();
                supervisor.detail = "Reconnecting to the local EchoSpeak service".into();
                reset_own_endpoint(&mut supervisor);
                true
            } else {
                false
            }
        };
        if was_attached {
            if let Err(error) = launch_backend(app.clone(), state.clone()) {
                schedule_restart(app, state.clone(), error);
            }
            return state.snapshot();
        }
    }
    let (child, orphan_endpoint) = {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        supervisor.desired_running = true;
        supervisor.generation += 1;
        supervisor.consecutive_failures = 0;
        supervisor.startup_failure = None;
        supervisor.phase = "recovering".into();
        supervisor.detail = "Restarting the local EchoSpeak service".into();
        supervisor.child_pid = None;
        (
            supervisor.child.take(),
            port_from_api_base(&supervisor.api_base)
                .ok()
                .map(|port| (port, supervisor.api_session_key.clone())),
        )
    };
    if let Some(child) = child {
        terminate_process_tree(child);
    } else if let Some((port, session_key)) = orphan_endpoint {
        if health_ready(port) && request_backend_exit(port, &session_key) {
            let delayed_app = app.clone();
            let delayed_state = state.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(Duration::from_millis(750)).await;
                if let Err(error) = launch_backend(delayed_app.clone(), delayed_state.clone()) {
                    schedule_restart(delayed_app, delayed_state, error);
                }
            });
            return state.snapshot();
        }
    }
    if let Err(error) = launch_backend(app.clone(), state.clone()) {
        schedule_restart(app, state.clone(), error);
    }
    state.snapshot()
}

pub fn shutdown_backend(state: &DesktopState) {
    let (child, orphan_endpoint) = {
        let mut supervisor = state
            .inner
            .lock()
            .expect("desktop supervisor mutex poisoned");
        supervisor.desired_running = false;
        supervisor.generation += 1;
        supervisor.phase = "stopped".into();
        supervisor.detail = "Desktop host is closing".into();
        supervisor.child_pid = None;
        // A shared backend this host attached to keeps running for its other clients.
        // Not cleared here: shutdown runs again on RunEvent::Exit and must still see it.
        let attached = supervisor.attached_pid.is_some();
        (
            supervisor.child.take(),
            port_from_api_base(&supervisor.api_base)
                .ok()
                .filter(|_| !attached)
                .map(|port| (port, supervisor.api_session_key.clone())),
        )
    };
    if let Some(child) = child {
        terminate_process_tree(child);
    } else if let Some((port, session_key)) = orphan_endpoint {
        if health_ready(port) {
            let _ = request_backend_exit(port, &session_key);
        }
    }
}

fn terminate_process_tree(child: CommandChild) {
    let pid = child.pid();
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let _ = StdCommand::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
    // POSIX: SIGTERM first so uvicorn runs its shutdown (code sessions stop, the
    // endpoint file is removed), then SIGKILL whatever is left after the grace period.
    #[cfg(unix)]
    if let Ok(raw_pid) = libc::pid_t::try_from(pid) {
        // SAFETY: plain signal delivery to the child this host spawned and still owns.
        unsafe { libc::kill(raw_pid, libc::SIGTERM) };
        let deadline = Instant::now() + Duration::from_millis(GRACEFUL_STOP_MS);
        while Instant::now() < deadline && process_is_alive(pid) {
            std::thread::sleep(Duration::from_millis(100));
        }
    }
    let _ = child.kill();
}

#[cfg(test)]
mod tests {
    use super::{backend_stderr_level, permanent_startup_failure};

    #[cfg(unix)]
    #[test]
    fn quitting_never_contacts_an_attached_backend_even_when_exit_runs_twice() {
        use super::{shutdown_backend, DesktopState};
        use std::net::TcpListener;
        let dir = std::env::temp_dir().join(format!(
            "echospeak-shutdown-{}",
            uuid::Uuid::new_v4().simple()
        ));
        let state = DesktopState::new(dir.join("data"), dir.join("logs")).unwrap();
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let port = listener.local_addr().unwrap().port();
        {
            let mut supervisor = state.inner.lock().unwrap();
            supervisor.api_base = format!("http://127.0.0.1:{port}");
            supervisor.attached_pid = Some(std::process::id());
        }
        // RunEvent::ExitRequested and RunEvent::Exit both call it.
        shutdown_backend(&state);
        shutdown_backend(&state);
        assert!(
            listener.accept().is_err(),
            "no request may reach a backend the app did not start"
        );
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn missing_packaged_import_is_not_a_transient_restart() {
        assert!(permanent_startup_failure("ImportError: cannot import name 'Runnable'").is_some());
        assert!(
            permanent_startup_failure("ModuleNotFoundError: no module named langchain_core")
                .is_some()
        );
        assert!(permanent_startup_failure("WARNING: provider quota exceeded").is_none());
        assert!(permanent_startup_failure(
            "RuntimeError: EchoSpeak durable state is already locked by another process (x)"
        )
        .is_some());
    }

    #[test]
    fn backend_stderr_uses_embedded_python_severity() {
        assert_eq!(
            backend_stderr_level("2026-07-17 | INFO     | agent.core - ready"),
            log::Level::Info
        );
        assert_eq!(
            backend_stderr_level("DEBUG:    waiting for application startup"),
            log::Level::Debug
        );
        assert_eq!(
            backend_stderr_level("WARNING: provider retry scheduled"),
            log::Level::Warn
        );
        assert_eq!(
            backend_stderr_level("ERROR: application startup failed"),
            log::Level::Error
        );
        assert_eq!(
            backend_stderr_level("unclassified stderr"),
            log::Level::Warn
        );
    }
}
