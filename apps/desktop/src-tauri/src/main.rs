#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if std::env::args_os().nth(1).as_deref() == Some(std::ffi::OsStr::new("--version")) {
        println!("EchoSpeak {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    echospeak_desktop_lib::run();
}
