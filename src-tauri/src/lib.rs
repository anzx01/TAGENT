use std::net::TcpStream;
use std::path::PathBuf;
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
fn project_root(resource_dir: Option<PathBuf>) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(path) = resource_dir {
        candidates.push(path);
    }
    if let Ok(path) = std::env::current_dir() {
        candidates.push(path);
    }
    if let Ok(path) = std::env::current_exe() {
        if let Some(parent) = path.parent() {
            candidates.push(parent.to_path_buf());
            if let Some(grandparent) = parent.parent() {
                candidates.push(grandparent.to_path_buf());
            }
        }
    }
    candidates.into_iter().find(|root| {
        root.join("python/server.py").exists()
            || root.join("dist/tagent-server.exe").exists()
    })
}

fn spawn_backend(resource_dir: Option<PathBuf>) -> Option<Child> {
    if backend_running() {
        return None;
    }
    let root = project_root(resource_dir).unwrap_or_else(|| {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
    });
    // Release 包使用随包发布的 sidecar；开发模式必须使用当前源码，
    // 否则工作区里残留的旧 dist/tagent-server.exe 会掩盖最新修复。
    let sidecar = root.join("dist/tagent-server.exe");
    if !cfg!(debug_assertions) && sidecar.exists() {
        return Command::new(sidecar)
            .current_dir(&root)
            .spawn()
            .map_err(|err| eprintln!("TAGENT sidecar start failed: {err}"))
            .ok();
    }
    let venv_py = root.join(".venv/Scripts/python.exe");
    let server = root.join("python/server.py");
    let venv_ok = venv_py.exists()
        && Command::new(&venv_py).arg("--version").output()
            .map(|output| output.status.success())
            .unwrap_or(false);
    let mut cmd = if venv_ok {
        Command::new(venv_py)
    } else {
        Command::new("python")
    };
    cmd.current_dir(&root);
    if server.exists() {
        cmd.arg(server);
    } else {
        cmd.arg("python/server.py");
    }
    cmd.spawn()
        .map_err(|err| eprintln!("TAGENT Python backend start failed: {err}"))
        .ok()
}

#[tauri::command]
fn set_panel_height(window: tauri::Window, open: bool) -> Result<(), String> {
    let size = if open {
        tauri::LogicalSize::new(500.0, 500.0)
    } else {
        tauri::LogicalSize::new(500.0, 60.0)
    };
    window.set_size(size).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![set_panel_height])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            let resource_dir = app.path().resource_dir().ok();
            app.manage(BackendState(Mutex::new(spawn_backend(resource_dir))));
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
