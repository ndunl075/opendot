//! OpenDot desktop shell (ARCHITECTURE.md section 11, M3 task 3.6).
//!
//! - Bundles the built UI (`ui/dist`) and the daemon as a PyInstaller sidecar.
//! - On launch it attaches to a daemon already answering on 127.0.0.1:8765, or starts the sidecar.
//! - It asks the sidecar for the access token (the same user's OS keychain) and hands it to the
//!   bundled UI through an initialization script, never through a URL or a log.
//! - Tray icon with Open, Pause, Resume and Quit. Closing the window hides it; the companion
//!   keeps running. Quit stops the daemon only if this app started it.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::json;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_opener::OpenerExt;
use tauri_plugin_shell::ShellExt;

const PORT: u16 = 8765;
const SIDECAR: &str = "opendot-daemon";
const START_TIMEOUT: Duration = Duration::from_secs(60);

struct Daemon {
    token: String,
    child: Mutex<Option<CommandChild>>,
}

/// The origins the bundled UI is served from (Windows/Android use http(s)://tauri.localhost, the
/// others tauri://localhost). Only these ever receive the token.
const APP_ORIGINS: [&str; 3] = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"];

fn is_app_url(url: &tauri::Url) -> bool {
    let origin = url.origin().ascii_serialization();
    APP_ORIGINS.contains(&origin.as_str()) || (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
}

/// Open an http(s) link in the user's browser (never inside the app's webview).
fn open_external(app: &AppHandle, url: &tauri::Url) {
    if matches!(url.scheme(), "http" | "https") && url.username().is_empty() && url.password().is_none() {
        if let Err(error) = app.opener().open_url(url.as_str(), None::<&str>) {
            eprintln!("could not open {url}: {error}");
        }
    }
}

fn base_url() -> String {
    format!("http://127.0.0.1:{PORT}")
}

/// True when an OpenDot daemon answers on the port. Health needs the token, so our API's
/// JSON 401 (`"code": "unauthorized"`) is as good a sign as a 200.
fn daemon_running() -> bool {
    let agent = ureq::AgentBuilder::new().timeout(Duration::from_millis(800)).build();
    match agent.get(&format!("{}/v1/health", base_url())).call() {
        Ok(_) => true,
        Err(ureq::Error::Status(401, response)) => response
            .into_string()
            .map(|body| body.contains("unauthorized"))
            .unwrap_or(false),
        Err(_) => false,
    }
}

/// Ask whatever answers on the port to prove it knows our token (HMAC of a fresh nonce), so the
/// token is never handed to another program that grabbed the port first (security review S1).
fn verify_identity(token: &str) -> bool {
    use hmac::{Hmac, Mac};
    let mut raw = [0u8; 32];
    if token.is_empty() || getrandom::getrandom(&mut raw).is_err() {
        return false;
    }
    let nonce: String = raw.iter().map(|b| format!("{b:02x}")).collect();
    let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(3)).build();
    let Ok(response) = agent.get(&format!("{}/v1/identity?nonce={nonce}", base_url())).call() else {
        return false;
    };
    let Ok(body) = response.into_json::<serde_json::Value>() else {
        return false;
    };
    let Some(proof) = body.get("proof").and_then(|value| value.as_str()) else {
        return false;
    };
    let Ok(mut mac) = Hmac::<sha2::Sha256>::new_from_slice(token.as_bytes()) else {
        return false;
    };
    mac.update(b"opendot-identity:");
    mac.update(nonce.as_bytes());
    let expected: String = mac.finalize().into_bytes().iter().map(|b| format!("{b:02x}")).collect();
    // Constant-time enough for a one-shot local check; both are fixed-length hex strings.
    expected.len() == proof.len() && expected.bytes().zip(proof.bytes()).fold(0u8, |acc, (a, b)| acc | (a ^ b)) == 0
}

fn sidecar_command(app: &AppHandle) -> Result<tauri_plugin_shell::process::Command, String> {
    let data_dir = app.path().app_data_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&data_dir).map_err(|e| e.to_string())?;
    let db = data_dir.join("opendot.db");
    Ok(app
        .shell()
        .sidecar(SIDECAR)
        .map_err(|e| e.to_string())?
        .current_dir(data_dir)
        .args(["--db", &db.to_string_lossy()]))
}

/// The access token, read by the sidecar from the OS keychain (created on first use).
fn read_token(app: &AppHandle) -> Result<String, String> {
    let command = sidecar_command(app)?.args(["api-token", "show"]);
    let output = tauri::async_runtime::block_on(command.output()).map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(format!("could not read the access token (exit {:?})", output.status.code()));
    }
    let token = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if token.is_empty() {
        return Err("the access token is empty".into());
    }
    Ok(token)
}

fn start_daemon(app: &AppHandle) -> Result<CommandChild, String> {
    let (mut events, child) = sidecar_command(app)?
        .args(["serve", "--port", &PORT.to_string(), "--print-ready"])
        .spawn()
        .map_err(|e| e.to_string())?;
    // Drain the sidecar's output so it never blocks on a full pipe.
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if let CommandEvent::Terminated(status) = event {
                eprintln!("opendot daemon exited: {:?}", status.code);
                break;
            }
        }
    });
    let deadline = Instant::now() + START_TIMEOUT;
    while Instant::now() < deadline {
        if daemon_running() {
            return Ok(child);
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
    Err("the OpenDot daemon did not start in time".into())
}

fn post(app: &AppHandle, path: &str, body: serde_json::Value) {
    let daemon = app.state::<Daemon>();
    let result = ureq::post(&format!("{}{}", base_url(), path))
        .timeout(Duration::from_secs(5))
        .set("Authorization", &format!("Bearer {}", daemon.token))
        .send_json(body);
    if let Err(error) = result {
        eprintln!("{path} failed: {error}");
    }
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn stop_started_daemon(app: &AppHandle) {
    let daemon = app.state::<Daemon>();
    let child = daemon.child.lock().ok().and_then(|mut guard| guard.take());
    if let Some(child) = child {
        let _ = child.kill();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let handle = app.handle().clone();
            let child = if daemon_running() {
                None
            } else {
                match start_daemon(&handle) {
                    Ok(child) => Some(child),
                    Err(error) => {
                        eprintln!("{error}");
                        None
                    }
                }
            };
            let token = read_token(&handle).unwrap_or_else(|error| {
                eprintln!("{error}");
                String::new()
            });
            let token = if verify_identity(&token) {
                token
            } else {
                eprintln!("the program on port {PORT} could not prove it is OpenDot; not sending it the token");
                String::new()
            };
            // The token goes only to the app's own origin: initialization scripts run on every
            // top-level navigation, so the script checks where it is before writing anything.
            let init = format!(
                "if ({origins}.includes(window.location.origin)) {{ window.__OPENDOT__ = Object.freeze({config}); }}",
                origins = json!(APP_ORIGINS),
                config = json!({ "token": token, "baseUrl": base_url() })
            );
            app.manage(Daemon { token, child: Mutex::new(child) });

            let nav_handle = handle.clone();
            let popup_handle = handle.clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("OpenDot")
                // The main window never leaves the app: external links open in the user's browser
                // (Google sign-in refuses embedded webviews anyway).
                .on_navigation(move |url| {
                    if is_app_url(url) {
                        return true;
                    }
                    open_external(&nav_handle, url);
                    false
                })
                .on_new_window(move |url, _features| {
                    if !is_app_url(&url) {
                        open_external(&popup_handle, &url);
                    }
                    tauri::webview::NewWindowResponse::Deny
                })
                .inner_size(1200.0, 800.0)
                .min_inner_size(720.0, 520.0)
                .initialization_script(&init)
                .build()?;

            let open = MenuItem::with_id(app, "open", "Open OpenDot", true, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "Pause", true, None::<&str>)?;
            let resume = MenuItem::with_id(app, "resume", "Resume", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit OpenDot", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &pause, &resume, &quit])?;
            let mut tray = TrayIconBuilder::with_id("opendot").tooltip("OpenDot").menu(&menu);
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.on_menu_event(|app, event| match event.id.as_ref() {
                "open" => show_main(app),
                "pause" => post(app, "/v1/companion/pause", json!({ "reason": "user" })),
                "resume" => post(app, "/v1/companion/resume", json!({})),
                "quit" => {
                    stop_started_daemon(app);
                    app.exit(0);
                }
                _ => {}
            })
            .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                // Closing hides the window; the companion keeps running. Quit is in the tray.
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running OpenDot");
}
