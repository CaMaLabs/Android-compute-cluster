use anyhow::{anyhow, bail, Context, Result};
use reqwest::blocking::{Body, Client};
use reqwest::header::{AUTHORIZATION, CONTENT_LENGTH, CONTENT_TYPE};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::env;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{atomic::{AtomicBool, Ordering}, Arc};
use std::thread;
use std::time::{Duration, Instant};

const AGENT_VERSION: &str = "0.3.0-rust";

#[derive(Debug, Serialize, Deserialize, Clone)]
struct Identity {
    worker_id: String,
    device_token: String,
}

#[derive(Debug, Deserialize)]
struct EnrollResponse {
    worker_id: String,
    device_token: String,
}

#[derive(Debug, Deserialize)]
struct RegisterResponse {
    lease_seconds: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct LeaseResponse {
    work: Option<Work>,
}

#[derive(Debug, Deserialize, Clone)]
struct Work {
    lease_id: String,
    job_id: String,
    unit_id: String,
    sequence: i64,
    kind: String,
    payload: Value,
}

#[derive(Debug, Deserialize, Clone)]
struct LocalPlugin {
    command: String,
    #[serde(default)]
    args: Vec<String>,
}

type PluginMap = BTreeMap<String, LocalPlugin>;

fn controller_url() -> String {
    env::var("SWARM_CONTROLLER_URL")
        .or_else(|_| env::var("CLUSTER_URL"))
        .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string())
        .trim_end_matches('/')
        .to_string()
}

fn enrollment_token() -> String {
    env::var("SWARM_ENROLLMENT_TOKEN").unwrap_or_else(|_| "dev-enroll-token-change-me".to_string())
}

fn identity_file() -> PathBuf {
    if let Ok(path) = env::var("SWARM_IDENTITY_FILE") {
        return PathBuf::from(path);
    }
    let home = env::var("HOME")
        .or_else(|_| env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join(".compute-swarm-rust-identity.json")
}

fn plugin_file() -> Option<PathBuf> {
    env::var("SWARM_RUST_PLUGIN_CONFIG").ok().map(PathBuf::from)
}

fn allow_insecure_remote() -> bool {
    env::var("SWARM_ALLOW_INSECURE_REMOTE").ok().as_deref() == Some("1")
}

fn validate_controller(url: &str) -> Result<()> {
    if url.starts_with("https://") {
        return Ok(());
    }
    let local = url.starts_with("http://127.0.0.1")
        || url.starts_with("http://localhost")
        || url.starts_with("http://[::1]");
    if url.starts_with("http://") && (local || allow_insecure_remote()) {
        return Ok(());
    }
    bail!("refusing plaintext remote controller; use HTTPS or SWARM_ALLOW_INSECURE_REMOTE=1 on a trusted LAN")
}

fn auth_value(token: &str) -> String {
    format!("Bearer {token}")
}

fn client() -> Result<Client> {
    Ok(Client::builder()
        .connect_timeout(Duration::from_secs(20))
        .timeout(Duration::from_secs(600))
        .build()?)
}

fn load_plugins() -> Result<PluginMap> {
    let Some(path) = plugin_file() else {
        return Ok(BTreeMap::new());
    };
    let data = fs::read_to_string(&path).with_context(|| format!("reading plugin config {}", path.display()))?;
    Ok(serde_json::from_str(&data).context("parsing Rust plugin config")?)
}

fn read_identity() -> Option<Identity> {
    let path = identity_file();
    let data = fs::read_to_string(path).ok()?;
    serde_json::from_str(&data).ok()
}

fn save_identity(identity: &Identity) -> Result<()> {
    let path = identity_file();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_vec_pretty(identity)?)?;
    Ok(())
}

fn hostname_string() -> String {
    hostname::get()
        .ok()
        .and_then(|x| x.into_string().ok())
        .unwrap_or_else(|| "rust-worker".to_string())
}

fn enroll(http: &Client, controller: &str) -> Result<Identity> {
    if let Some(identity) = read_identity() {
        return Ok(identity);
    }
    let response: EnrollResponse = http
        .post(format!("{controller}/workers/enroll"))
        .header(AUTHORIZATION, auth_value(&enrollment_token()))
        .json(&json!({"name": hostname_string()}))
        .send()?
        .error_for_status()?
        .json()?;
    let identity = Identity {
        worker_id: response.worker_id,
        device_token: response.device_token,
    };
    save_identity(&identity)?;
    Ok(identity)
}

fn benchmark() -> f64 {
    let iterations = 100_000u64;
    let mut data = b"universal-compute-swarm".to_vec();
    let start = Instant::now();
    for _ in 0..iterations {
        let mut h = Sha256::new();
        h.update(&data);
        data = h.finalize().to_vec();
    }
    iterations as f64 / start.elapsed().as_secs_f64().max(1e-9)
}

fn builtin_tasks() -> Vec<&'static str> {
    vec!["prime_count", "monte_carlo_pi", "sha256_artifact", "text_artifact"]
}

fn capabilities(plugins: &PluginMap) -> Vec<String> {
    let mut caps = vec![
        "cpu".to_string(),
        "rust".to_string(),
        format!("os:{}", env::consts::OS),
        format!("arch:{}", env::consts::ARCH),
    ];
    for task in builtin_tasks() {
        caps.push(format!("task:{task}"));
    }
    for task in plugins.keys() {
        caps.push(format!("task:{task}"));
    }
    if let Ok(extra) = env::var("SWARM_CAPABILITIES") {
        caps.extend(extra.split(',').map(str::trim).filter(|x| !x.is_empty()).map(str::to_string));
    }
    caps.sort();
    caps.dedup();
    caps
}

fn labels() -> Map<String, Value> {
    let mut out = Map::new();
    if let Ok(raw) = env::var("SWARM_LABELS") {
        for item in raw.split(',') {
            if let Some((k, v)) = item.trim().split_once('=') {
                out.insert(k.to_string(), Value::String(v.to_string()));
            }
        }
    }
    out
}

fn register(http: &Client, controller: &str, identity: &Identity, plugins: &PluginMap, score: f64) -> Result<u64> {
    let cores = thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    let response: RegisterResponse = http
        .post(format!("{controller}/workers/{}/register", identity.worker_id))
        .header(AUTHORIZATION, auth_value(&identity.device_token))
        .json(&json!({
            "name": hostname_string(),
            "os_name": env::consts::OS,
            "platform": format!("{}-{}", env::consts::OS, env::consts::ARCH),
            "arch": env::consts::ARCH,
            "cores": cores,
            "memory_mb": null,
            "benchmark": score,
            "capabilities": capabilities(plugins),
            "labels": labels(),
            "agent_version": AGENT_VERSION
        }))
        .send()?
        .error_for_status()?
        .json()?;
    Ok(response.lease_seconds.unwrap_or(120))
}

fn heartbeat(http: &Client, controller: &str, identity: &Identity, plugins: &PluginMap) -> Result<()> {
    http.post(format!("{controller}/workers/{}/heartbeat", identity.worker_id))
        .header(AUTHORIZATION, auth_value(&identity.device_token))
        .json(&json!({"capabilities": capabilities(plugins)}))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn lease(http: &Client, controller: &str, identity: &Identity) -> Result<Option<Work>> {
    let response: LeaseResponse = http
        .post(format!("{controller}/workers/{}/lease?wait_seconds=15", identity.worker_id))
        .header(AUTHORIZATION, auth_value(&identity.device_token))
        .json(&json!({}))
        .send()?
        .error_for_status()?
        .json()?;
    Ok(response.work)
}

fn renew(http: &Client, controller: &str, identity: &Identity, lease_id: &str) -> Result<()> {
    http.post(format!("{controller}/workers/{}/leases/{lease_id}/renew", identity.worker_id))
        .header(AUTHORIZATION, auth_value(&identity.device_token))
        .json(&json!({}))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn work_root(work: &Work) -> PathBuf {
    env::var("SWARM_WORK_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|_| env::temp_dir().join("compute-swarm-rust"))
        .join(&work.job_id)
        .join(&work.unit_id)
}

fn sanitize_name(name: &str, fallback: &str) -> String {
    Path::new(name)
        .file_name()
        .and_then(|x| x.to_str())
        .filter(|x| !x.is_empty())
        .unwrap_or(fallback)
        .to_string()
}

fn download_artifacts(http: &Client, controller: &str, identity: &Identity, work: &Work, dir: &Path) -> Result<Map<String, Value>> {
    let mut paths = Map::new();
    let Some(inputs) = work.payload.get("artifact_inputs").and_then(Value::as_array) else {
        return Ok(paths);
    };
    for (index, item) in inputs.iter().enumerate() {
        let artifact_id = item.get("artifact_id").and_then(Value::as_str).ok_or_else(|| anyhow!("artifact input missing artifact_id"))?;
        let alias = item.get("alias").and_then(Value::as_str).map(str::to_string).unwrap_or_else(|| format!("artifact_{index}"));
        let name = sanitize_name(item.get("name").and_then(Value::as_str).unwrap_or(artifact_id), &format!("artifact_{index}"));
        let destination = dir.join(name);
        let mut response = http
            .get(format!("{controller}/artifacts/{artifact_id}"))
            .header(AUTHORIZATION, auth_value(&identity.device_token))
            .header("X-Worker-ID", &identity.worker_id)
            .send()?
            .error_for_status()?;
        let expected = response.headers().get("X-Artifact-Sha256").and_then(|v| v.to_str().ok()).map(str::to_string);
        let mut out = File::create(&destination)?;
        let mut hasher = Sha256::new();
        let mut buffer = [0u8; 1024 * 1024];
        loop {
            let n = response.read(&mut buffer)?;
            if n == 0 { break; }
            hasher.update(&buffer[..n]);
            out.write_all(&buffer[..n])?;
        }
        let digest = format!("{:x}", hasher.finalize());
        if let Some(expected) = expected {
            if !digest.eq_ignore_ascii_case(&expected) {
                bail!("artifact checksum mismatch for {artifact_id}");
            }
        }
        paths.insert(alias, Value::String(destination.to_string_lossy().to_string()));
    }
    Ok(paths)
}

fn hash_file(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let n = file.read(&mut buffer)?;
        if n == 0 { break; }
        hasher.update(&buffer[..n]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn upload_artifact(http: &Client, controller: &str, identity: &Identity, path: &Path, name: &str, content_type: &str) -> Result<Value> {
    let digest = hash_file(path)?;
    let size = fs::metadata(path)?.len();
    let file = File::open(path)?;
    let response = http
        .post(format!(
            "{controller}/workers/{}/artifacts?name={}",
            identity.worker_id,
            urlencoding::encode(name)
        ))
        .header(AUTHORIZATION, auth_value(&identity.device_token))
        .header(CONTENT_TYPE, content_type)
        .header(CONTENT_LENGTH, size.to_string())
        .header("X-Artifact-Sha256", digest)
        .body(Body::new(file))
        .send()?
        .error_for_status()?
        .json()?;
    Ok(response)
}

fn prime_count(payload: &Value) -> Result<Value> {
    let start = payload.get("start").and_then(Value::as_i64).ok_or_else(|| anyhow!("start missing"))?;
    let end = payload.get("end").and_then(Value::as_i64).ok_or_else(|| anyhow!("end missing"))?;
    fn is_prime(n: i64) -> bool {
        if n < 2 { return false; }
        if n % 2 == 0 { return n == 2; }
        let mut d = 3i64;
        while d * d <= n {
            if n % d == 0 { return false; }
            d += 2;
        }
        true
    }
    let count = (start.max(2)..end).filter(|n| is_prime(*n)).count();
    Ok(json!({"count": count}))
}

fn pseudo_point(seed: u64) -> (f64, f64) {
    fn mix(mut x: u64) -> u64 {
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        x.wrapping_mul(0x2545F4914F6CDD1D)
    }
    let a = mix(seed.wrapping_add(0x9E3779B97F4A7C15));
    let b = mix(a ^ 0xD1B54A32D192ED03);
    let scale = 1.0 / (u64::MAX as f64);
    (a as f64 * scale, b as f64 * scale)
}

fn monte_carlo_pi(payload: &Value) -> Result<Value> {
    let start = payload.get("start").and_then(Value::as_u64).ok_or_else(|| anyhow!("start missing"))?;
    let end = payload.get("end").and_then(Value::as_u64).ok_or_else(|| anyhow!("end missing"))?;
    let mut inside = 0u64;
    for i in start..end {
        let (x, y) = pseudo_point(i);
        if x * x + y * y <= 1.0 { inside += 1; }
    }
    Ok(json!({"inside": inside, "samples": end - start}))
}

fn sha256_artifact(payload: &Value) -> Result<Value> {
    let alias = payload.get("alias").and_then(Value::as_str).unwrap_or("input");
    let path = payload
        .get("_artifact_paths")
        .and_then(|v| v.get(alias))
        .and_then(Value::as_str)
        .ok_or_else(|| anyhow!("artifact alias not found: {alias}"))?;
    let p = Path::new(path);
    Ok(json!({"sha256": hash_file(p)?, "size_bytes": fs::metadata(p)?.len()}))
}

fn text_artifact(payload: &Value, work_dir: &Path) -> Result<Value> {
    let name = sanitize_name(payload.get("name").and_then(Value::as_str).unwrap_or("output.txt"), "output.txt");
    let text = payload.get("text").and_then(Value::as_str).unwrap_or("");
    let path = work_dir.join(&name);
    fs::write(&path, text.as_bytes())?;
    Ok(json!({
        "bytes": text.as_bytes().len(),
        "_artifact_outputs": [{"path": name, "name": name, "content_type": "text/plain; charset=utf-8"}]
    }))
}

fn execute_local_plugin(plugin: &LocalPlugin, payload: &Value, work_dir: &Path) -> Result<Value> {
    let mut child = Command::new(&plugin.command)
        .args(&plugin.args)
        .current_dir(work_dir)
        .env("SWARM_WORK_DIR", work_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .with_context(|| format!("starting local plugin {}", plugin.command))?;
    child.stdin.as_mut().context("plugin stdin unavailable")?.write_all(serde_json::to_string(payload)?.as_bytes())?;
    let output = child.wait_with_output()?;
    if !output.status.success() {
        bail!("local plugin failed: {}", String::from_utf8_lossy(&output.stderr));
    }
    Ok(serde_json::from_slice(&output.stdout).context("local plugin did not return JSON")?)
}

fn execute_task(kind: &str, payload: &Value, work_dir: &Path, plugins: &PluginMap) -> Result<Value> {
    match kind {
        "prime_count" => prime_count(payload),
        "monte_carlo_pi" => monte_carlo_pi(payload),
        "sha256_artifact" => sha256_artifact(payload),
        "text_artifact" => text_artifact(payload, work_dir),
        other => {
            let plugin = plugins.get(other).ok_or_else(|| anyhow!("unsupported task kind: {other}"))?;
            execute_local_plugin(plugin, payload, work_dir)
        }
    }
}

fn normalize_outputs(http: &Client, controller: &str, identity: &Identity, work_dir: &Path, mut result: Value) -> Result<Value> {
    let Some(obj) = result.as_object_mut() else { return Ok(result); };
    let Some(outputs) = obj.remove("_artifact_outputs") else { return Ok(result); };
    let outputs = outputs.as_array().ok_or_else(|| anyhow!("_artifact_outputs must be an array"))?;
    let root = fs::canonicalize(work_dir)?;
    let mut uploaded = Vec::new();
    for (index, item) in outputs.iter().enumerate() {
        let rel = item.get("path").and_then(Value::as_str).ok_or_else(|| anyhow!("artifact output missing path"))?;
        let candidate = fs::canonicalize(work_dir.join(rel))?;
        if !candidate.starts_with(&root) {
            bail!("artifact output escaped work directory");
        }
        let name = sanitize_name(item.get("name").and_then(Value::as_str).unwrap_or(rel), &format!("output_{index}"));
        let content_type = item.get("content_type").and_then(Value::as_str).unwrap_or("application/octet-stream");
        uploaded.push(upload_artifact(http, controller, identity, &candidate, &name, content_type)?);
    }
    obj.insert("artifacts".to_string(), Value::Array(uploaded));
    Ok(result)
}

fn execute_work(http: &Client, controller: &str, identity: &Identity, work: &Work, plugins: &PluginMap, lease_seconds: u64) -> Result<(Value, f64)> {
    let dir = work_root(work);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir)?;

    let stop = Arc::new(AtomicBool::new(false));
    let stop_clone = stop.clone();
    let http_clone = http.clone();
    let controller_clone = controller.to_string();
    let identity_clone = identity.clone();
    let lease_id = work.lease_id.clone();
    let interval = Duration::from_secs((lease_seconds / 3).max(5));
    let keeper = thread::spawn(move || {
        while !stop_clone.load(Ordering::Relaxed) {
            thread::sleep(interval);
            if stop_clone.load(Ordering::Relaxed) { break; }
            if let Err(err) = renew(&http_clone, &controller_clone, &identity_clone, &lease_id) {
                eprintln!("lease renewal failed: {err:#}");
                break;
            }
        }
    });

    let started = Instant::now();
    let outcome = (|| -> Result<Value> {
        let mut payload = work.payload.clone();
        let object = payload.as_object_mut().ok_or_else(|| anyhow!("work payload must be an object"))?;
        object.insert("_work_dir".to_string(), Value::String(dir.to_string_lossy().to_string()));
        object.insert(
            "_artifact_paths".to_string(),
            Value::Object(download_artifacts(http, controller, identity, work, &dir)?),
        );
        let result = execute_task(&work.kind, &payload, &dir, plugins)?;
        normalize_outputs(http, controller, identity, &dir, result)
    })();

    stop.store(true, Ordering::Relaxed);
    let _ = keeper.join();
    let elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
    let _ = fs::remove_dir_all(&dir);
    Ok((outcome?, elapsed_ms))
}

fn submit_result(http: &Client, controller: &str, identity: &Identity, work: &Work, result: Value, elapsed_ms: f64) -> Result<()> {
    http.post(format!("{controller}/workers/{}/units/{}/result", identity.worker_id, work.unit_id))
        .header(AUTHORIZATION, auth_value(&identity.device_token))
        .json(&json!({"lease_id": work.lease_id, "result": result, "elapsed_ms": elapsed_ms}))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn submit_failure(http: &Client, controller: &str, identity: &Identity, work: &Work, error: &str) -> Result<()> {
    http.post(format!("{controller}/workers/{}/units/{}/failure", identity.worker_id, work.unit_id))
        .header(AUTHORIZATION, auth_value(&identity.device_token))
        .json(&json!({"lease_id": work.lease_id, "error": error, "retry": false}))
        .send()?
        .error_for_status()?;
    Ok(())
}

fn main() -> Result<()> {
    let controller = controller_url();
    validate_controller(&controller)?;
    let http = client()?;
    let plugins = load_plugins()?;
    let identity = enroll(&http, &controller)?;
    let score = benchmark();
    let lease_seconds = register(&http, &controller, &identity, &plugins, score)?;
    println!(
        "joined swarm as {} | {} {} | benchmark={:.0} iter/s",
        identity.worker_id,
        env::consts::OS,
        env::consts::ARCH,
        score
    );

    let mut last_heartbeat = Instant::now() - Duration::from_secs(60);
    loop {
        if last_heartbeat.elapsed() >= Duration::from_secs(10) {
            if let Err(err) = heartbeat(&http, &controller, &identity, &plugins) {
                eprintln!("heartbeat error: {err:#}");
            }
            last_heartbeat = Instant::now();
        }

        match lease(&http, &controller, &identity) {
            Ok(Some(work)) => {
                match execute_work(&http, &controller, &identity, &work, &plugins, lease_seconds) {
                    Ok((result, elapsed_ms)) => {
                        if let Err(err) = submit_result(&http, &controller, &identity, &work, result, elapsed_ms) {
                            eprintln!("result submission failed: {err:#}");
                        } else {
                            println!("done {} unit={} in {:.0} ms", work.kind, work.sequence, elapsed_ms);
                        }
                    }
                    Err(err) => {
                        eprintln!("task failed: {err:#}");
                        let _ = submit_failure(&http, &controller, &identity, &work, &format!("{err:#}"));
                    }
                }
            }
            Ok(None) => thread::sleep(Duration::from_millis(500)),
            Err(err) => {
                eprintln!("controller error: {err:#}");
                thread::sleep(Duration::from_secs(3));
            }
        }
    }
}
