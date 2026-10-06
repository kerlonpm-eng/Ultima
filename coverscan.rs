// coverscan.rs
//
// Helper standalone (sem dependências externas, só std) que
// procura uma capa (cover art) já presente nos arquivos de um
// jogo — muitos jogos vêm com a própria imagem de capa/boxart
// solta na pasta de instalação, e isso evita ter que caçar isso
// manualmente toda vez que um jogo é adicionado.
//
// Não decodifica nenhuma imagem (sem libs de imagem no meio) — só
// olha nome de arquivo, extensão e tamanho em bytes pra pontuar os
// candidatos. Quem decide se a imagem escolhida é boa mesmo é o
// launcher.py, que já sabe abrir/mostrar imagens de verdade (e o
// usuário sempre pode trocar manualmente se o palpite for ruim).
//
// COMPILAR:
//   rustc -O coverscan.rs -o coverscan
//
// USO:
//   ./coverscan <caminho_do_executavel_do_jogo>
//
// SAÍDA (stdout): um array JSON, do candidato mais provável pro
// menos provável:
//   [{"path":"/home/x/Jogo/cover.jpg","score":100,"size":204800}]
//
// Onde procura, a partir da pasta do executável:
//   - a própria pasta do exe
//   - subpastas comuns de arte/mídia dentro dela (art, media,
//     images, resources — com e sem maiúscula)
//   - a pasta um nível acima (alguns jogos guardam o .exe dentro
//     de bin/ ou similar, com a arte na raiz da instalação)
//
// Pontuação (score):
//   100 — nome do arquivo bate exatamente com um nome comum de
//         capa (cover, boxart, box, folder, header, poster,
//         banner, keyart, titleart, capa)
//    60 — nome do arquivo CONTÉM um desses termos
//    20 — qualquer outra imagem achada nessas pastas (candidato
//         fraco; desempate pelo arquivo maior, já que ícones e
//         logos pequenos tendem a ser arquivos bem menores que
//         capas/artes de verdade)
//
// Imagens menores que 4 KB são ignoradas de propósito — nesse
// tamanho é bem mais provável ser um ícone pequeno do que uma capa.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

struct Candidate {
    path: PathBuf,
    score: i32,
    size: u64,
}

const IMAGE_EXTENSIONS: &[&str] = &["jpg", "jpeg", "png", "webp", "bmp"];

const STRONG_NAMES: &[&str] = &[
    "cover", "boxart", "box", "folder", "header", "poster",
    "banner", "keyart", "titleart", "capa",
];

const ART_SUBDIRS: &[&str] = &[
    "art", "Art", "media", "Media", "images", "Images",
    "resources", "Resources",
];

const MIN_SIZE_BYTES: u64 = 4096;
const MAX_CANDIDATES: usize = 10;

fn has_image_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| IMAGE_EXTENSIONS.contains(&ext.to_lowercase().as_str()))
        .unwrap_or(false)
}

fn score_for_stem(stem_lower: &str) -> i32 {
    if STRONG_NAMES.iter().any(|name| stem_lower == *name) {
        return 100;
    }

    if STRONG_NAMES.iter().any(|name| stem_lower.contains(name)) {
        return 60;
    }

    20
}

fn scan_dir(dir: &Path, candidates: &mut Vec<Candidate>) {

    let entries = match fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };

    for entry in entries.flatten() {

        let path = entry.path();

        if !path.is_file() || !has_image_extension(&path) {
            continue;
        }

        let size = fs::metadata(&path).map(|meta| meta.len()).unwrap_or(0);

        if size < MIN_SIZE_BYTES {
            continue;
        }

        let stem_lower = path
            .file_stem()
            .and_then(|stem| stem.to_str())
            .unwrap_or("")
            .to_lowercase();

        candidates.push(Candidate {
            path,
            score: score_for_stem(&stem_lower),
            size,
        });
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
        eprintln!("uso: {} <caminho_do_executavel_do_jogo>", args[0]);
        std::process::exit(1);
    }

    let exe_path = PathBuf::from(&args[1]);

    let exe_dir = match exe_path.parent() {
        Some(dir) if dir.as_os_str().len() > 0 => dir.to_path_buf(),
        _ => PathBuf::from("."),
    };

    let mut candidates: Vec<Candidate> = Vec::new();

    scan_dir(&exe_dir, &mut candidates);

    for sub in ART_SUBDIRS {
        scan_dir(&exe_dir.join(sub), &mut candidates);
    }

    if let Some(parent) = exe_dir.parent() {
        scan_dir(parent, &mut candidates);
    }

    candidates.sort_by(|a, b| {
        b.score.cmp(&a.score).then(b.size.cmp(&a.size))
    });

    candidates.truncate(MAX_CANDIDATES);

    let body: Vec<String> = candidates
        .iter()
        .map(|candidate| {
            format!(
                "{{\"path\":\"{}\",\"score\":{},\"size\":{}}}",
                escape_json(&candidate.path.to_string_lossy()),
                candidate.score,
                candidate.size,
            )
        })
        .collect();

    println!("[{}]", body.join(","));
}
