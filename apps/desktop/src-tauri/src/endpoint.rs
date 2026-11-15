//! POSIX: read the endpoint file a running backend publishes (`agent/endpoint_file.py`).
//!
//! The desktop app and the terminal client must share one backend per data folder,
//! because the backend holds a single-writer lock on it. Before spawning its own
//! backend the app reads this file and, when it describes a live, authenticated,
//! loopback backend for the same data folder, attaches to it instead.
//!
//! The rules mirror `endpoint_file.read_endpoint`: the file lives in
//! `$ECHOSPEAK_RUNTIME_DIR`, else `$XDG_RUNTIME_DIR/echospeak`, else `<data dir>/runtime`;
//! it must be a regular file (not a symlink) owned by this user with no group/other
//! permission bits, schema 1, and name a pid that is still alive. The caller adds the
//! network checks (`/health`, authenticated readiness) and the data-folder match.

use std::{
    ffi::OsString,
    fs::OpenOptions,
    io::Read,
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::{Path, PathBuf},
};

pub const FILENAME: &str = "endpoint.json";
const SCHEMA: i64 = 1;
const MAX_BYTES: u64 = 65_536;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Endpoint {
    pub pid: u32,
    pub host: String,
    pub port: u16,
    pub auth_key: String,
    pub auth_required: bool,
    pub data_dir: String,
    pub version: String,
}

fn non_empty(value: Option<OsString>) -> Option<String> {
    value
        .map(|value| value.to_string_lossy().trim().to_string())
        .filter(|value| !value.is_empty())
}

fn expand_user(value: &str, home: Option<&str>) -> PathBuf {
    match (value, home) {
        ("~", Some(home)) => PathBuf::from(home),
        (path, Some(home)) if path.starts_with("~/") => Path::new(home).join(&path[2..]),
        (path, _) => PathBuf::from(path),
    }
}

fn runtime_dir_from(
    explicit: Option<OsString>,
    xdg: Option<OsString>,
    home: Option<OsString>,
    data_dir: &Path,
) -> PathBuf {
    if let Some(explicit) = non_empty(explicit) {
        return expand_user(&explicit, non_empty(home).as_deref());
    }
    if let Some(xdg) = non_empty(xdg) {
        return Path::new(&xdg).join("echospeak");
    }
    data_dir.join("runtime")
}

/// Where the backend for `data_dir` publishes `endpoint.json`.
pub fn runtime_dir(data_dir: &Path) -> PathBuf {
    runtime_dir_from(
        std::env::var_os("ECHOSPEAK_RUNTIME_DIR"),
        std::env::var_os("XDG_RUNTIME_DIR"),
        std::env::var_os("HOME"),
        data_dir,
    )
}

/// Load and validate the file itself. `Ok(None)` means there is no file.
pub fn load(path: &Path) -> Result<Option<Endpoint>, String> {
    let file = match OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
    {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("it could not be opened ({error})")),
    };
    let info = file
        .metadata()
        .map_err(|error| format!("it could not be inspected ({error})"))?;
    // SAFETY: getuid has no preconditions and cannot fail.
    let uid = unsafe { libc::getuid() };
    if !info.file_type().is_file() || info.uid() != uid || info.mode() & 0o077 != 0 {
        return Err("wrong owner, type or permissions (must be a 0600 file owned by this user)".into());
    }
    let mut text = String::new();
    file.take(MAX_BYTES)
        .read_to_string(&mut text)
        .map_err(|error| format!("it could not be read ({error})"))?;
    parse(&text).map(Some)
}

pub fn parse(text: &str) -> Result<Endpoint, String> {
    let data: serde_json::Value =
        serde_json::from_str(text).map_err(|_| "it is not valid JSON".to_string())?;
    if data.get("schema").and_then(|value| value.as_i64()) != Some(SCHEMA) {
        return Err(format!("unsupported schema (expected {SCHEMA})"));
    }
    let text_field = |name: &str| {
        data.get(name)
            .and_then(|value| value.as_str())
            .unwrap_or_default()
            .to_string()
    };
    let pid = data
        .get("pid")
        .and_then(|value| value.as_u64())
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| *pid > 0)
        .ok_or_else(|| "it has no valid pid".to_string())?;
    let port = data
        .get("port")
        .and_then(|value| value.as_u64())
        .and_then(|port| u16::try_from(port).ok())
        .filter(|port| *port > 0)
        .ok_or_else(|| "it has no valid port".to_string())?;
    Ok(Endpoint {
        pid,
        host: text_field("host"),
        port,
        auth_key: text_field("auth_key"),
        auth_required: data
            .get("auth_required")
            .and_then(|value| value.as_bool())
            .unwrap_or(false),
        data_dir: text_field("data_dir"),
        version: text_field("version"),
    })
}

/// Checks that need no network: loopback, authenticated, same data folder.
/// `data_dir` must already be canonical (DesktopState canonicalizes it).
pub fn matches_desktop(endpoint: &Endpoint, data_dir: &Path) -> Result<(), String> {
    if endpoint.host != "127.0.0.1" {
        return Err(format!("it listens on {}, not 127.0.0.1", endpoint.host));
    }
    if !endpoint.auth_required || endpoint.auth_key.trim().is_empty() {
        return Err("it does not require an API key (the app only shares authenticated backends)".into());
    }
    let theirs = Path::new(&endpoint.data_dir)
        .canonicalize()
        .map_err(|_| format!("its data folder {} does not exist", endpoint.data_dir))?;
    if theirs != data_dir {
        return Err(format!(
            "it uses the data folder {}, not {}",
            theirs.display(),
            data_dir.display()
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "echospeak-endpoint-{name}-{}-{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir.canonicalize().unwrap()
    }

    fn sample(data_dir: &Path) -> String {
        serde_json::json!({
            "schema": 1, "pid": std::process::id(), "host": "127.0.0.1", "port": 8765,
            "auth_key": "k", "auth_required": true, "version": "11.13.1",
            "data_dir": data_dir.to_string_lossy(), "started_at": 1.0
        })
        .to_string()
    }

    #[test]
    fn runtime_dir_follows_the_backend_precedence() {
        let data = Path::new("/data");
        assert_eq!(
            runtime_dir_from(Some("~/rt".into()), Some("/run/user/1".into()), Some("/home/u".into()), data),
            PathBuf::from("/home/u/rt")
        );
        assert_eq!(
            runtime_dir_from(Some("  ".into()), Some("/run/user/1".into()), None, data),
            PathBuf::from("/run/user/1/echospeak")
        );
        assert_eq!(runtime_dir_from(None, None, None, data), PathBuf::from("/data/runtime"));
    }

    #[test]
    fn a_private_file_for_the_same_data_folder_is_accepted() {
        let dir = temp_dir("ok");
        let path = dir.join(FILENAME);
        std::fs::write(&path, sample(&dir)).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        let endpoint = load(&path).unwrap().expect("an endpoint");
        assert_eq!(endpoint.port, 8765);
        assert_eq!(endpoint.pid, std::process::id());
        assert!(matches_desktop(&endpoint, &dir).is_ok());
        let other = temp_dir("other");
        assert!(matches_desktop(&endpoint, &other).unwrap_err().contains("data folder"));
        std::fs::remove_dir_all(&dir).unwrap();
        std::fs::remove_dir_all(&other).unwrap();
    }

    #[test]
    fn loose_permissions_symlinks_and_missing_files_are_refused() {
        let dir = temp_dir("refuse");
        let path = dir.join(FILENAME);
        assert_eq!(load(&path).unwrap(), None);
        std::fs::write(&path, sample(&dir)).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(load(&path).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        let link = dir.join("link.json");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(load(&link).is_err(), "a symlinked endpoint file must be refused");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn unauthenticated_remote_or_malformed_endpoints_are_refused() {
        let dir = temp_dir("bad");
        let mut endpoint = parse(&sample(&dir)).unwrap();
        endpoint.auth_required = false;
        assert!(matches_desktop(&endpoint, &dir).is_err());
        let mut endpoint = parse(&sample(&dir)).unwrap();
        endpoint.host = "0.0.0.0".into();
        assert!(matches_desktop(&endpoint, &dir).is_err());
        assert!(parse("{\"schema\":2,\"pid\":1,\"port\":1}").is_err());
        assert!(parse("{\"schema\":1,\"pid\":0,\"port\":1}").is_err());
        assert!(parse("{\"schema\":1,\"pid\":1,\"port\":70000}").is_err());
        assert!(parse("not json").is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
