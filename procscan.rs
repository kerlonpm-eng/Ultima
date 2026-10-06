// procscan.rs
//
// Helper standalone (sem dependências externas, só std) que faz o
// rastreio de processos de uma sessão de jogo do UltimaLauncher:
// acha todo processo que pertence a ela tanto pelo grupo de
// processos (pgid) quanto pelo WINEPREFIX — pra pegar wineserver
// e outros processos do Wine que se desgarram do grupo original
// (chamam setsid() por conta própria pra sobreviver independente
// de quem os iniciou).
//
// Isso é um PORTE FIEL da mesma lógica que já existe em Python
// (list_pids_in_group + list_pids_by_wineprefix + read_process_info
// no launcher.py) — ou seja, se algum processo ainda escapava da
// detecção em Python, é bem provável que ainda escape aqui, já
// que os dois leem o mesmo /proc com as mesmas permissões. Trocar
// de linguagem não amplia por si só o que dá pra enxergar; ajuda é
// se a versão em Rust também ganhar uma estratégia de detecção
// diferente/mais ampla (ex: casar por nome do processo, por
// caminho do executável dentro do prefixo etc.) — o que ainda não
// fiz aqui de propósito, até sabermos exatamente quais processos
// continuam invisíveis.
//
// COMPILAR:
//   rustc -O procscan.rs -o procscan
//
// USO:
//   ./procscan <pgid> [wineprefix]
//
//   - pgid: o pgid da sessão (0 pra não filtrar por grupo)
//   - wineprefix: caminho do WINEPREFIX (opcional; omita ou passe
//     "" pra não filtrar por prefixo)
//
// SAÍDA (stdout): um array JSON, um objeto por processo achado:
//   [{"pid":1234,"name":"wineserver","cpu_ticks":567,"rss_mb":12.3}]
//
// Pensado pra ser chamado via subprocess a partir do launcher.py e
// o resultado lido com json.loads(). JÁ ESTÁ plugado no launcher:
// se esse arquivo estiver do lado de launcher.py (mesma pasta) e
// tiver um `rustc` instalado, o launcher tenta compilar sozinho na
// primeira vez que o monitor de processos ("🧬 Processos") é
// aberto, e passa a usar o binário compilado no lugar do rastreio
// em Python puro. Se não compilar ou o binário não existir, o
// launcher cai pro caminho em Python sem travar nem avisar nada —
// esse .rs é só um acelerador opcional, nunca uma exigência.

use std::env;
use std::fs;

struct ProcInfo {
    pid: i32,
    name: String,
    cpu_ticks: u64,
    rss_mb: f64,
}

/// Lê nome (comm), pgrp e tempo de CPU (utime+stime, em jiffies)
/// de /proc/[pid]/stat. Os índices batem exatamente com o parsing
/// que o launcher.py já faz (comm entre parênteses; depois dele,
/// campo 0 = state, 1 = ppid, 2 = pgrp, ..., 11 = utime, 12 = stime).
fn read_stat_fields(pid: i32) -> Option<(String, i32, u64)> {
    let raw = fs::read_to_string(format!("/proc/{}/stat", pid)).ok()?;

    let open = raw.find('(')?;
    let close = raw.rfind(')')?;

    if close < open {
        return None;
    }

    let name = raw[open + 1..close].to_string();
    let rest = raw[close + 1..].trim();
    let fields: Vec<&str> = rest.split_whitespace().collect();

    let pgrp: i32 = fields.get(2)?.parse().ok()?;
    let utime: u64 = fields.get(11)?.parse().ok()?;
    let stime: u64 = fields.get(12)?.parse().ok()?;

    Some((name, pgrp, utime + stime))
}

/// Lê VmRSS de /proc/[pid]/status e devolve em MB (mesma conta que
/// o launcher.py: kB / 1024).
fn read_rss_mb(pid: i32) -> f64 {
    let raw = match fs::read_to_string(format!("/proc/{}/status", pid)) {
        Ok(text) => text,
        Err(_) => return 0.0,
    };

    for line in raw.lines() {
        if let Some(rest) = line.strip_prefix("VmRSS:") {
            let kb: f64 = rest
                .trim()
                .split_whitespace()
                .next()
                .and_then(|value| value.parse().ok())
                .unwrap_or(0.0);
            return kb / 1024.0;
        }
    }

    0.0
}

/// Lê o WINEPREFIX do ambiente do processo, direto de
/// /proc/[pid]/environ (pares "CHAVE=valor" separados por \0).
fn read_wineprefix(pid: i32) -> Option<String> {
    let raw = fs::read(format!("/proc/{}/environ", pid)).ok()?;

    for chunk in raw.split(|byte| *byte == 0) {
        if let Some(rest) = chunk.strip_prefix(b"WINEPREFIX=") {
            return Some(String::from_utf8_lossy(rest).into_owned());
        }
    }

    None
}

/// Normalização simples de caminho (só tira uma "/" final) — não
/// exige que o caminho exista, ao contrário de fs::canonicalize,
/// já que o processo pode estar rodando de dentro de um prefixo
/// cujo caminho real não seja visível/resolvível daqui.
fn normalize(path: &str) -> String {
    let trimmed = path.trim_end_matches('/');
    if trimmed.is_empty() {
        "/".to_string()
    } else {
        trimmed.to_string()
    }
}

fn escape_json(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

fn main() {
    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        eprintln!("uso: {} <pgid> [wineprefix]", args[0]);
        std::process::exit(1);
    }

    let target_pgid: i32 = args[1].parse().unwrap_or(0);

    let target_prefix = args
        .get(2)
        .filter(|value| !value.is_empty())
        .map(|value| normalize(value));

    let mut results: Vec<ProcInfo> = Vec::new();

    let entries = match fs::read_dir("/proc") {
        Ok(entries) => entries,
        Err(_) => {
            println!("[]");
            return;
        }
    };

    for entry in entries.flatten() {
        let file_name = entry.file_name();
        let name_str = match file_name.to_str() {
            Some(value) => value,
            None => continue,
        };

        let pid: i32 = match name_str.parse() {
            Ok(value) => value,
            Err(_) => continue,
        };

        let (comm, pgrp, cpu_ticks) = match read_stat_fields(pid) {
            Some(value) => value,
            None => continue,
        };

        let mut belongs = target_pgid != 0 && pgrp == target_pgid;

        if !belongs {
            if let Some(prefix) = &target_prefix {
                if let Some(env_prefix) = read_wineprefix(pid) {
                    if &normalize(&env_prefix) == prefix {
                        belongs = true;
                    }
                }
            }
        }

        if !belongs {
            continue;
        }

        results.push(ProcInfo {
            pid,
            name: comm,
            cpu_ticks,
            rss_mb: read_rss_mb(pid),
        });
    }

    let body: Vec<String> = results
        .iter()
        .map(|info| {
            format!(
                "{{\"pid\":{},\"name\":\"{}\",\"cpu_ticks\":{},\"rss_mb\":{:.4}}}",
                info.pid,
                escape_json(&info.name),
                info.cpu_ticks,
                info.rss_mb,
            )
        })
        .collect();

    println!("[{}]", body.join(","));
}
