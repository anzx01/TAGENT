use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

use tauri::Manager;

/// 后端服务端口（与 python/server.py 保持一致）
const BACKEND_PORT: u16 = 18899;

struct BackendState(Mutex<Option<Child>>);

fn backend_running() -> bool {
    TcpStream::connect(("127.0.0.1", BACKEND_PORT)).is_ok()
}

fn backend_should_stay_running() -> Option<bool> {
    let mut stream = TcpStream::connect(("127.0.0.1", BACKEND_PORT)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let request = format!(
        "GET /lifecycle HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nConnection: close\r\n\r\n",
        BACKEND_PORT
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return None;
    }
    let mut response = Vec::new();
    if stream.read_to_end(&mut response).is_err() {
        return None;
    }
    let Some(body_start) = response.windows(4).position(|window| window == b"\r\n\r\n") else {
        return None;
    };
    should_stay_running_in_status_value(&response[body_start + 4..])
}

fn should_stay_running_in_status_value(body: &[u8]) -> Option<bool> {
    serde_json::from_slice::<serde_json::Value>(body)
        .ok()
        .and_then(|value| {
            let schedules = value["schedules"].as_object()?;
            Some(!schedules.is_empty()
                || value["startup_enabled"].as_bool() == Some(true)
                || value["recording"].as_bool() == Some(true)
                || value["replaying"].as_bool() == Some(true))
        })
}

fn request_backend_lifecycle(action: &str) {
    if let Ok(mut stream) = TcpStream::connect(("127.0.0.1", BACKEND_PORT)) {
        let request = format!(
            "POST /lifecycle/{action} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
            BACKEND_PORT,
        );
        let _ = stream.write_all(request.as_bytes());
    }
}

/// 启动 Python 后端 sidecar；已在运行则跳过
fn project_root(resource_dir: Option<PathBuf>) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if cfg!(debug_assertions) {
        if let Ok(path) = std::env::current_dir() {
            candidates.push(path);
        }
        if let Some(path) = resource_dir {
            candidates.push(path);
        }
    } else {
        if let Some(path) = resource_dir {
            candidates.push(path);
        }
        if let Ok(path) = std::env::current_dir() {
            candidates.push(path);
        }
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
            || root.join("tagent-server.exe").exists()
    })
}

fn spawn_backend(resource_dir: Option<PathBuf>) -> Option<Child> {
    if backend_running() {
        request_backend_lifecycle("attach");
        return None;
    }
    let root = project_root(resource_dir)
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let data_dir = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .map(|p| p.join("TAGENT"));
    // Release 包使用随包发布的 sidecar；开发模式必须使用当前源码，
    // 否则工作区里残留的旧 dist/tagent-server.exe 会掩盖最新修复。
    let packaged_sidecar = root.join("tagent-server.exe");
    let development_sidecar = root.join("dist/tagent-server.exe");
    let sidecar = if packaged_sidecar.exists() {
        packaged_sidecar
    } else {
        development_sidecar
    };
    if !cfg!(debug_assertions) && sidecar.exists() {
        let mut command = Command::new(sidecar);
        command.current_dir(&root);
        if let Some(path) = data_dir {
            command.env("TAGENT_DATA_DIR", path);
        }
        return command
            .spawn()
            .map_err(|err| eprintln!("TAGENT sidecar start failed: {err}"))
            .ok();
    }
    let venv_py = root.join(".venv/Scripts/python.exe");
    let server = root.join("python/server.py");
    let venv_ok = venv_py.exists()
        && Command::new(&venv_py)
            .arg("--version")
            .output()
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
            match backend_should_stay_running() {
                Some(false) => {
                    request_backend_lifecycle("detach");
                }
                Some(true) => {
                    request_backend_lifecycle("detach");
                    log::info!("Persistent or active background work remains after UI exit");
                }
                None => log::warn!(
                    "Could not read backend schedules; leaving it running to avoid interrupting work"
                ),
            }
            if let Some(state) = app_handle.try_state::<BackendState>() {
                state.0.lock().unwrap().take();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    #[test]
    fn startup_schedules_and_active_work_keep_backend_alive() {
        assert_eq!(
            super::should_stay_running_in_status_value(br#"{"schedules":{"task-1":{"kind":"daily"}},"startup_enabled":false}"#),
            Some(true)
        );
        assert_eq!(
            super::should_stay_running_in_status_value(br#"{"schedules":{},"startup_enabled":true}"#),
            Some(true)
        );
        assert_eq!(
            super::should_stay_running_in_status_value(br#"{"schedules":{},"startup_enabled":false}"#),
            Some(false)
        );
        assert_eq!(
            super::should_stay_running_in_status_value(br#"{"schedules":{},"startup_enabled":false,"replaying":true}"#),
            Some(true)
        );
        assert_eq!(super::should_stay_running_in_status_value(b"invalid"), None);
    }
}
