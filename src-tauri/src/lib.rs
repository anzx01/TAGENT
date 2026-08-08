use std::net::TcpStream;
use std::path::Path;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::Manager;

/// 后端服务端口（与 python/server.py 保持一致）
const BACKEND_PORT: u16 = 18899;

struct BackendState(Mutex<Option<Child>>);

fn backend_running() -> bool {
    TcpStream::connect(("127.0.0.1", BACKEND_PORT)).is_ok()
}

/// 启动 Python 后端 sidecar；已在运行则跳过
fn spawn_backend() -> Option<Child> {
    if backend_running() {
        return None;
    }
    // 优先已打包的 sidecar exe（阶段5.4），其次源码模式
    let sidecar = Path::new("dist/tagent-server.exe");
    if sidecar.exists() {
        return Command::new(sidecar).spawn().ok();
    }
    let venv_py = Path::new(".venv/Scripts/python.exe");
    let base = if venv_py.exists() { venv_py } else { Path::new("python") };
    let mut cmd = Command::new(base);
    cmd.arg("python/server.py");
    cmd.spawn().ok()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            app.manage(BackendState(Mutex::new(spawn_backend())));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let tauri::RunEvent::Exit = event {
            if let Some(state) = app_handle.try_state::<BackendState>() {
                if let Some(mut child) = state.0.lock().unwrap().take() {
                    let _ = child.kill();
                }
            }
            // 兜底：主动通知后端退出（独立启动的后端也能被清理）
            let _ = TcpStream::connect(("127.0.0.1", BACKEND_PORT));
        }
    });
}
