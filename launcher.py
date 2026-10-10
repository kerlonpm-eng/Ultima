import copy
import hashlib
import json
import re
import os
import sys
import subprocess
import shlex
import shutil
import signal
import time
import base64
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (
    Qt, QSize, QTimer, QRect, QRectF, QEvent, QCoreApplication,
    QPropertyAnimation, QEasingCurve, QUrl, QMimeData,
    QVariantAnimation, QPointF, QObject, Signal, QLocale,
)

# Suporte a sons é opcional: se o pacote QtMultimedia não estiver
# instalado junto com o PySide6, o launcher continua funcionando
# normalmente, só sem tocar sons.
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    SOUND_BACKEND_AVAILABLE = True
except ImportError:
    SOUND_BACKEND_AVAILABLE = False
from PySide6.QtGui import (
    QIcon,
    QPixmap,
    QColor,
    QPainter,
    QPainterPath,
    QLinearGradient,
    QImageReader,
    QFontMetrics,
    QDrag,
    QShortcut,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QTextBrowser,
    QApplication,
    QMainWindow,
    QWidget,
    QDialog,
    QFileDialog,
    QMessageBox,
    QLineEdit,
    QPushButton,
    QLabel,
    QComboBox,
    QCheckBox,
    QTabWidget,
    QButtonGroup,
    QFormLayout,
    QHBoxLayout,
    QVBoxLayout,
    QGridLayout,
    QScrollArea,
    QSlider,
    QColorDialog,
    QFrame,
    QSpinBox,
    QProgressBar,
    QStackedWidget,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QGraphicsOpacityEffect,
    QGraphicsView,
    QGraphicsScene,
    QListWidget,
    QListWidgetItem,
)


# ---- configuração ----

APP_NAME = "UltimaLauncher"
APP_SLUG = "ultimalauncher"

LAUNCHER_VERSION = "2.9.1"

DISCORD_LARGE_IMAGE_KEY = "ultimalauncher"
DISCORD_CLIENT_ID = "1549559299263692860"

BASE_DIR = Path(__file__).resolve().parent

APP_DIR = Path.home() / "UltimaLauncher"

DATA_DIR = APP_DIR / "dados"
IMAGE_DIR = APP_DIR / "imagens"
ICON_DIR = APP_DIR / "icones"
# Cópias das capas escolhidas pros jogos. O launcher copia a imagem
# pra cá ao salvar o jogo e passa a usar essa cópia, então mover ou
# apagar o arquivo original não faz o jogo perder a capa.
COVER_DIR = APP_DIR / "capas"
LOG_DIR = APP_DIR / "logs"
SOUND_DIR = APP_DIR / "sons"
# Código-fonte de outras linguagens (os .rs) e os binários que o
# launcher compila a partir deles ficam juntos nessa pasta, em vez
# de soltos na raiz do launcher.
RUST_DIR = APP_DIR / "rust"

DATA_FILE = DATA_DIR / "games.json"
SESSIONS_FILE = DATA_DIR / "sessions.json"

# Integração com o SteamGridDB (imagem do jogo no Discord).
STEAMGRIDDB_API = "https://www.steamgriddb.com/api/v2"
STEAMGRIDDB_KEY_URL = (
    "https://www.steamgriddb.com/profile/preferences/api"
)
# Imagens já encontradas (ou "não achei nada") por nome de jogo,
# pra não consultar a API toda vez que o jogo abre.
STEAMGRID_CACHE_FILE = DATA_DIR / "steamgrid_cache.json"
# Quando não achou imagem, só tenta de novo depois desse tempo.
STEAMGRID_RETRY_SECONDS = 7 * 24 * 3600
STEAMGRID_MIMES = "image/png,image/jpeg"
# Cada execução que dá erro gera o próprio arquivo de log, com data
# e hora no nome (ultimalauncher_2025-01-31_14-05-09.log), em vez de
# reescrever sempre o mesmo arquivo.
LOG_PREFIX = APP_SLUG
LOG_KEEP_MAX = 30  # quantos logs antigos manter na pasta
SCRIPT_PATH = APP_DIR / "launcher.py"
WRAPPER_PATH = APP_DIR / f"{APP_SLUG}.sh"

WRAPPER_MARKER = "# UltimaLauncher wrapper (não edite este arquivo à mão)"

BACKGROUND_CACHE_MAX = QSize(1920, 1080)

for folder in (
    APP_DIR, DATA_DIR, IMAGE_DIR, ICON_DIR, COVER_DIR, LOG_DIR,
    SOUND_DIR, RUST_DIR,
):
    folder.mkdir(parents=True, exist_ok=True)

# ---- ícone do próprio launcher ----
# O ícone do app pode estar em lugares diferentes dependendo de como
# o launcher foi instalado (rodando da pasta de desenvolvimento, de
# um pacote do AUR em /usr/share, de um AppImage etc.), então em vez
# de depender de UM caminho fixo, procura em vários e usa o primeiro
# que abrir de verdade. Se nenhum servir, quem chama cai pro texto.
APP_ICON_NAMES = (APP_SLUG, "icon", "logo")
APP_ICON_EXTENSIONS = (".svg", ".png", ".webp", ".ico", ".jpg")
APP_ICON_SIZE_DIRS = (
    "scalable", "512x512", "256x256", "128x128", "64x64", "48x48",
)

_app_icon_cache = {"loaded": False, "icon": None}


def app_icon_search_dirs():
    """Pastas onde o ícone do launcher pode estar, da mais
    específica pra mais genérica. ULTIMALAUNCHER_ASSETS (variável
    de ambiente) tem prioridade — útil pra um PKGBUILD que instala
    os assets num lugar próprio."""

    dirs = []

    env_dir = os.environ.get("ULTIMALAUNCHER_ASSETS")
    if env_dir:
        dirs.append(Path(env_dir))

    for base in (BASE_DIR, BASE_DIR.parent, APP_DIR):
        dirs.append(base / "assets")
        dirs.append(base)

    data_home = os.environ.get("XDG_DATA_HOME") or str(
        Path.home() / ".local" / "share"
    )
    data_dirs = os.environ.get("XDG_DATA_DIRS") or (
        "/usr/local/share:/usr/share"
    )

    for share in [data_home] + data_dirs.split(":"):

        if not share:
            continue

        share_path = Path(share)

        dirs.append(share_path / APP_SLUG / "assets")
        dirs.append(share_path / APP_SLUG)
        dirs.append(share_path / "pixmaps")

        for size_dir in APP_ICON_SIZE_DIRS:
            dirs.append(share_path / "icons" / "hicolor" / size_dir / "apps")

    dirs.append(Path("/opt") / APP_SLUG / "assets")
    dirs.append(Path("/opt") / APP_SLUG)

    unique = []
    seen = set()

    for directory in dirs:
        key = str(directory)
        if key not in seen:
            seen.add(key)
            unique.append(directory)

    return unique


def app_icon_candidates():
    """Gera, em ordem de preferência, os arquivos que podem ser o
    ícone do launcher."""

    for directory in app_icon_search_dirs():

        try:
            if not directory.is_dir():
                continue
        except OSError:
            continue

        for name in APP_ICON_NAMES:
            for extension in APP_ICON_EXTENSIONS:
                candidate = directory / f"{name}{extension}"
                try:
                    if candidate.is_file():
                        yield candidate
                except OSError:
                    continue


def load_app_icon():
    """Devolve um QIcon utilizável do launcher ou None. Testa se a
    imagem realmente renderiza (um .svg sem o plugin do Qt, por
    exemplo, abre "sem erro" mas desenha vazio) antes de aceitar, e
    por fim tenta o tema de ícones do sistema. O resultado fica em
    cache — precisa ser chamado depois do QApplication existir."""

    if _app_icon_cache["loaded"]:
        return _app_icon_cache["icon"]

    found = None

    for candidate in app_icon_candidates():

        icon = QIcon(str(candidate))

        if not icon.isNull() and not icon.pixmap(32, 32).isNull():
            found = icon
            break

    if found is None:

        themed = QIcon.fromTheme(APP_SLUG)

        if not themed.isNull():
            found = themed

    _app_icon_cache["loaded"] = True
    _app_icon_cache["icon"] = found

    return found

# ---- idiomas (i18n) ----
# O texto-fonte do launcher é o português: tr("texto em pt") devolve o
# próprio texto, ou a tradução quando o idioma ativo é outro. Texto sem
# tradução cai de volta no português (nunca quebra). Pra adicionar um
# idioma novo é só criar outro dicionário como o _EN e registrar em
# _TRANSLATIONS. Textos com variáveis usam .format(): veja os que têm
# {nome} no dicionário.

_LANG = "pt"

_EN = {
    "Clique em botões": "Click on buttons",
    "Iniciar jogo": "Start game",
    "Excluir jogo": "Delete game",
    "Confirmar / Aplicar": "Confirm / Apply",
    "Preencher (corta as bordas, sem distorcer)": "Fill (crops the edges, no distortion)",
    "Esticar (imagem inteira, pode distorcer)": "Stretch (whole image, may distort)",
    "Nunca jogado": "Never played",
    "Nunca": "Never",
    "jogo não encontrado no SteamGridDB": "game not found on SteamGridDB",
    "o jogo existe lá, mas não tem imagem utilizável": "the game exists there, but has no usable image",
    "chave recusada pelo SteamGridDB": "key rejected by SteamGridDB",
    (
        "Nenhum terminal compatível foi encontrado. Instale xterm, "
        "gnome-terminal, konsole, xfce4-terminal ou mate-terminal."
    ): (
        "No compatible terminal was found. Install xterm, "
        "gnome-terminal, konsole, xfce4-terminal or mate-terminal."
    ),
    "Jogo": "Game",
    "Processo": "Process",
    "Nenhum processo do launcher rodando agora. 🎉": "No launcher processes running right now. 🎉",
    "⏹ Encerrar sessão selecionada": "⏹ Stop selected session",
    "✖ Encerrar só este processo": "✖ Stop only this process",
    "🔄 Atualizar lista": "🔄 Refresh list",
    (
        "Atualiza a lista de processos agora (ela já se atualiza "
        "sozinha a cada 2 segundos)."
    ): (
        "Refreshes the process list now (it already refreshes itself "
        "every 2 seconds)."
    ),
    "Fechar": "Close",
    "Selecione um processo na lista primeiro.": "Select a process in the list first.",
    "essa sessão": "this session",
    (
        "O Wine pode demorar alguns segundos para abrir o jogo. "
        "Aguarde — esta janela fecha sozinha."
    ): (
        "Wine may take a few seconds to open the game. Please wait — "
        "this window closes by itself."
    ),
    "Ícones": "Icons",
    "Quadradas": "Square",
    "Verticais (capas)": "Vertical (covers)",
    "Horizontais": "Horizontal",
    "Imagem do jogo no Discord (SteamGridDB)": "Game image on Discord (SteamGridDB)",
    (
        "Se o launcher estiver pegando a imagem de outro jogo, procure "
        "o nome certo e escolha o jogo na lista. Depois clique na "
        "imagem que quiser."
    ): (
        "If the launcher is picking the image of another game, search "
        "for the right name and choose the game from the list. Then "
        "click the image you want."
    ),
    "Nome do jogo": "Game name",
    "🔍 Buscar": "🔍 Search",
    "Jogo:": "Game:",
    "Tipo:": "Type:",
    "↩ Voltar ao automático": "↩ Back to automatic",
    "Esquece a escolha e deixa o launcher procurar sozinho.": "Forgets your choice and lets the launcher search on its own.",
    "Cancelar": "Cancel",
    "Usar esta imagem": "Use this image",
    "Digite o nome de um jogo.": "Type a game name.",
    "Buscando jogos...": "Searching games...",
    "O SteamGridDB recusou a chave da API.": "SteamGridDB rejected the API key.",
    "Nenhum jogo encontrado com esse nome.": "No game found with that name.",
    "Carregando imagens...": "Loading images...",
    (
        "Esse jogo não tem imagens desse tipo — tente outro tipo ou "
        "outro jogo da lista."
    ): (
        "This game has no images of this type — try another type or "
        "another game from the list."
    ),
    "Editar jogo": "Edit game",
    "Adicionar jogo": "Add game",
    "Ex: The Binding of Isaac": "e.g. The Binding of Isaac",
    "Nome:": "Name:",
    "Procurar...": "Browse...",
    "Executável:": "Executable:",
    "Runner:": "Runner:",
    "Opcional: /home/user/.wine": "Optional: /home/user/.wine",
    "Wine Prefix:": "Wine Prefix:",
    "Ex: ~/cxoffice/bin/wine \"%EXE%\"": "e.g. ~/cxoffice/bin/wine \"%EXE%\"",
    "Comando:": "Command:",
    "Capa do jogo (imagem grande, preenche o card)": "Game cover (large image, fills the card)",
    "Capa:": "Cover:",
    "Ícone do jogo (logo pequeno)": "Game icon (small logo)",
    "Ícone:": "Icon:",
    "🎨 Usar ícone do executável": "🎨 Use the executable's icon",
    "🖼 Procurar capa nos arquivos do jogo": "🖼 Look for a cover in the game files",
    (
        "Procura uma imagem de capa que o próprio jogo já tenha junto "
        "do executável (pasta do jogo, subpastas de arte/mídia comuns "
        "etc.)."
    ): (
        "Looks for a cover image that the game itself already ships "
        "next to the executable (game folder, common art/media "
        "subfolders, etc.)."
    ),
    (
        "Preencher: a capa cobre o card inteiro e as bordas que "
        "sobrarem são cortadas.\nEsticar: a capa aparece inteira, "
        "esticada pro tamanho do card (pode ficar distorcida)."
    ): (
        "Fill: the cover covers the whole card and any leftover edges "
        "are cropped.\nStretch: the whole cover is shown, stretched to "
        "the card size (it may look distorted)."
    ),
    "Modo da capa:": "Cover mode:",
    "Mostrar capa/ícone no card": "Show cover/icon on the card",
    (
        "Quando os dois existirem, mostrar o ícone como um selo sobre "
        "a capa"
    ): "When both exist, show the icon as a badge over the cover",
    "Escolher imagem...": "Choose image...",
    (
        "Escolhe qual imagem do SteamGridDB aparece ao lado do ícone "
        "do launcher no Discord."
    ): (
        "Chooses which SteamGridDB image appears next to the launcher "
        "icon on Discord."
    ),
    "Imagem no Discord:": "Image on Discord:",
    "Capa": "Cover",
    "Ícone": "Icon",
    "Salvar": "Save",
    "Adicionar": "Add",
    "Escolher executável": "Choose executable",
    "Executáveis (*.exe);;Todos os arquivos (*)": "Executables (*.exe);;All files (*)",
    "Escolha o executável do jogo primeiro.": "Choose the game's executable first.",
    (
        "Não encontrei nenhuma capa nos arquivos do jogo. Escolha uma "
        "manualmente em \"Capa\"."
    ): (
        "I couldn't find any cover in the game files. Choose one "
        "manually under \"Cover\"."
    ),
    "Escolher capa": "Choose cover",
    "Imagens (*.png *.jpg *.jpeg *.webp *.ico)": "Images (*.png *.jpg *.jpeg *.webp *.ico)",
    "Escolher ícone": "Choose icon",
    "Escolha um executável primeiro.": "Choose an executable first.",
    "O executável não foi encontrado.": "The executable was not found.",
    "Ícone extraído com sucesso!": "Icon extracted successfully!",
    (
        "Não consegui extrair o ícone desse executável.\n\nVocê pode "
        "escolher uma imagem manualmente."
    ): (
        "I couldn't extract the icon from this executable.\n\nYou can "
        "choose an image manually."
    ),
    "✅ Imagem escolhida por você": "✅ Image chosen by you",
    "Automática (o launcher procura sozinho)": "Automatic (the launcher searches on its own)",
    (
        "Cole a chave do SteamGridDB em Configurações → Integrações "
        "primeiro."
    ): "Paste your SteamGridDB key in Settings → Integrations first.",
    "Digite o nome do jogo.": "Type the game name.",
    "Escolha o executável.": "Choose the executable.",
    "O executável não existe.\n\nDeseja salvar mesmo assim?": "The executable does not exist.\n\nSave anyway?",
    "Digite o comando personalizado.": "Type the custom command.",
    "Configurações": "Settings",
    "▦ Grade": "▦ Grid",
    "🎠 Fileira (carrossel infinito)": "🎠 Row (infinite carousel)",
    "Modo de exibição dos jogos:": "Game display mode:",
    (
        "No modo grade os jogos ficam organizados em colunas. No modo "
        "fileira os jogos formam um carrossel infinito: o do meio fica "
        "em destaque e os vizinhos ficam menores. Navegue com o scroll "
        "do mouse, as setas do teclado ou clicando num card do lado. "
        "(Reordenar arrastando só funciona no modo grade.)"
    ): (
        "In grid mode the games are arranged in columns. In row mode "
        "the games form an infinite carousel: the middle one is "
        "highlighted and its neighbors are smaller. Navigate with the "
        "mouse wheel, the arrow keys, or by clicking a card on the "
        "side. (Drag-to-reorder only works in grid mode.)"
    ),
    "Tema": "Theme",
    (
        "Exporte as cores, opacidades e imagem de fundo atuais num "
        "arquivo pra reaproveitar em outro lugar, ou importe um tema "
        "salvo anteriormente."
    ): (
        "Export the current colors, opacities and background image to "
        "a file to reuse elsewhere, or import a previously saved theme."
    ),
    "⬆ Exportar tema...": "⬆ Export theme...",
    "⬇ Importar tema...": "⬇ Import theme...",
    "Atualização do launcher": "Launcher update",
    (
        "Selecione um novo arquivo launcher.py pra atualizar. Só é "
        "aplicado se a versão dele for maior que a instalada "
        "atualmente."
    ): (
        "Select a new launcher.py file to update. It is only applied "
        "if its version is higher than the one currently installed."
    ),
    "🔄 Atualizar launcher...": "🔄 Update launcher...",
    "🖥 Geral": "🖥 General",
    "Cor de destaque:": "Accent color:",
    "Imagem de fundo:": "Background image:",
    "Opacidade:": "Opacity:",
    "🎨 Aparência": "🎨 Appearance",
    "Cor dos cards:": "Card color:",
    "Opacidade dos cards:": "Card opacity:",
    "Cor da borda:": "Border color:",
    "Espessura da borda:": "Border width:",
    "Arredondamento da borda:": "Border radius:",
    "✥ Reordenar jogos arrastando os cards": "✥ Reorder games by dragging the cards",
    (
        "Enquanto estiver ligado, clicar e arrastar um card move ele "
        "pra outra posição na lista (só muda a ordem — os botões do "
        "card ficam desativados até desligar de novo)."
    ): (
        "While enabled, clicking and dragging a card moves it to "
        "another position in the list (it only changes the order — the "
        "card's buttons stay disabled until you turn it off again)."
    ),
    "🃏 Cards": "🃏 Cards",
    "Escolher cor dos botões": "Choose button color",
    "Cor dos botões:": "Button color:",
    "Opacidade dos botões:": "Button opacity:",
    "Escolher cor do texto dos botões": "Choose button text color",
    "Cor do texto:": "Text color:",
    "Escolher cor da borda dos botões": "Choose button border color",
    "Arredondamento:": "Radius:",
    "Usar a cor de destaque": "Use the accent color",
    "Escolher cor do hover": "Choose hover color",
    "Cor ao passar o mouse:": "Hover color:",
    "🔘 Botões": "🔘 Buttons",
    "Mostrar janela de 'Carregando...' ao iniciar um jogo": "Show a 'Loading...' window when starting a game",
    "Fechar automaticamente após:": "Close automatically after:",
    (
        "A janela não é modal: você continua podendo usar o launcher "
        "enquanto o jogo abre. Ela fecha sozinha no tempo configurado "
        "acima."
    ): (
        "The window is not modal: you can keep using the launcher "
        "while the game opens. It closes by itself after the time set "
        "above."
    ),
    "Abrir o Wine dentro de um terminal (modo debug)": "Open Wine inside a terminal (debug mode)",
    (
        "Com o modo debug ligado, jogos que usam Wine são abertos "
        "dentro de uma janela de terminal, onde dá pra acompanhar as "
        "mensagens do Wine em tempo real. Útil pra descobrir por que "
        "um jogo não abre. Requer xterm, gnome-terminal, konsole, "
        "xfce4-terminal ou mate-terminal instalado."
    ): (
        "With debug mode on, games that use Wine are opened inside a "
        "terminal window, where you can follow Wine's messages in real "
        "time. Useful to find out why a game won't open. Requires "
        "xterm, gnome-terminal, konsole, xfce4-terminal or "
        "mate-terminal to be installed."
    ),
    "🚀 Execução": "🚀 Execution",
    "Ativar Rich Presence do Discord": "Enable Discord Rich Presence",
    "Cole aqui a sua chave da API do SteamGridDB": "Paste your SteamGridDB API key here",
    "Mostrar": "Show",
    "Chave do SteamGridDB:": "SteamGridDB key:",
    "Nome de um jogo pra testar (vazio = Hades)": "Name of a game to test (empty = Hades)",
    "🔍 Testar": "🔍 Test",
    "Testar chave:": "Test key:",
    (
        "É necessário ter o Discord Desktop aberto (o site/navegador "
        "não conta) para o Rich Presence aparecer no seu perfil."
    ): (
        "Discord Desktop must be open (the website/browser doesn't "
        "count) for Rich Presence to show on your profile."
    ),
    "🔌 Integrações": "🔌 Integrations",
    "Ativar sons do launcher": "Enable launcher sounds",
    "Volume geral:": "Master volume:",
    (
        "⚠ O pacote QtMultimedia não foi encontrado nesta instalação "
        "do PySide6. Os sons ficam desativados até ele ser instalado, "
        "mas o resto do launcher funciona normalmente."
    ): (
        "⚠ The QtMultimedia package was not found in this PySide6 "
        "installation. Sounds stay disabled until it is installed, but "
        "the rest of the launcher works normally."
    ),
    (
        "Cada evento toca, por padrão, o arquivo com o mesmo nome "
        "dentro da pasta 'sons' (ex: sons/click.mp3). Escolha um "
        "arquivo personalizado se quiser trocar, ou clique em 'Padrão' "
        "pra voltar a usar o som original."
    ): (
        "Each event plays, by default, the file with the same name "
        "inside the 'sons' folder (e.g. sons/click.mp3). Choose a "
        "custom file if you want to change it, or click 'Default' to "
        "go back to the original sound."
    ),
    "▶ Testar": "▶ Test",
    "Escolher...": "Choose...",
    "Padrão": "Default",
    "Volume:": "Volume:",
    "🔊 Sons": "🔊 Sounds",
    "Aplicar": "Apply",
    "Escolher cor": "Choose color",
    "Escolher imagem de fundo": "Choose background image",
    "Imagens (*.png *.jpg *.jpeg *.webp)": "Images (*.png *.jpg *.jpeg *.webp)",
    "Escolher cor dos cards": "Choose card color",
    "Escolher cor da borda": "Choose border color",
    "Cole a chave da API primeiro.": "Paste the API key first.",
    "❌ O SteamGridDB recusou a chave. Confira se copiou inteira.": (
        "❌ SteamGridDB rejected the key. Check that you copied all of "
        "it."
    ),
    "Escolher som": "Choose sound",
    "Áudio (*.mp3 *.wav *.ogg)": "Audio (*.mp3 *.wav *.ogg)",
    (
        "O suporte a sons (QtMultimedia) não está instalado nesta "
        "máquina."
    ): "Sound support (QtMultimedia) is not installed on this machine.",
    "Exportar tema": "Export theme",
    "Tema UltimaLauncher (*.json)": "UltimaLauncher theme (*.json)",
    "Importar tema": "Import theme",
    "Esse arquivo não parece ser um tema válido.": "This file doesn't look like a valid theme.",
    "Nenhuma configuração de tema reconhecida nesse arquivo.": "No recognized theme settings in this file.",
    "Tema importado. Clique em Aplicar pra confirmar as mudanças.": "Theme imported. Click Apply to confirm the changes.",
    "Selecionar novo launcher.py": "Select new launcher.py",
    "Python (*.py)": "Python (*.py)",
    "Atualizar launcher": "Update launcher",
    (
        "Não encontrei o número de versão nesse arquivo. Confirme se é "
        "mesmo um launcher.py válido."
    ): (
        "I couldn't find the version number in this file. Make sure it "
        "really is a valid launcher.py."
    ),
    "Sem nome": "Unnamed",
    "▶  Jogar": "▶  Play",
    "✏ Editar": "✏ Edit",
    "Arraste pra outra posição pra reordenar": "Drag to another position to reorder",
    "Porcentagem do tempo total jogado": "Percentage of total play time",
    "📅  nunca jogado": "📅  never played",
    "Outros": "Others",
    "Nenhum dado de tempo ainda.": "No time data yet.",
    "Nenhum jogo encontrado.\n\nClique em + Adicionar para começar.": "No games found.\n\nClick + Add to get started.",
    "📚 Biblioteca": "📚 Library",
    "📊 Estatísticas": "📊 Statistics",
    "+ Adicionar": "+ Add",
    "🧬 Processos": "🧬 Processes",
    (
        "Ver e encerrar processos abertos pelo launcher — inclusive os "
        "que sobram do Wine depois que o jogo já fechou."
    ): (
        "View and stop processes opened by the launcher — including "
        "the ones Wine leaves behind after the game has already closed."
    ),
    "🖥 Criar atalho": "🖥 Create shortcut",
    "⚙ Configurações": "⚙ Settings",
    "🔍 Procurar jogo...": "🔍 Search game...",
    "tempo total": "total time",
    "mais jogado": "most played",
    "maior sessão": "longest session",
    "sessões totais": "total sessions",
    (
        "Nenhum jogo na biblioteca ainda.\n\nAdicione jogos para começar "
        "a acompanhar suas estatísticas."
    ): (
        "No games in the library yet.\n\nAdd games to start tracking "
        "your statistics."
    ),
    "Por tempo jogado": "By play time",
    "🍕 Ver gráfico": "🍕 View chart",
    (
        "Nenhum jogo foi jogado ainda.\n\nAs estatísticas aparecem "
        "depois que você jogar algo."
    ): (
        "No game has been played yet.\n\nStatistics appear after you "
        "play something."
    ),
    "este jogo": "this game",
    "No menu": "In the menu",
    (
        "O SteamGridDB recusou a chave da API.\n\nConfira a chave em "
        "Configurações → Integrações."
    ): (
        "SteamGridDB rejected the API key.\n\nCheck the key in Settings "
        "→ Integrations."
    ),
    "Deseja escolher um ícone personalizado para o atalho?": "Do you want to choose a custom icon for the shortcut?",
    "Escolher ícone do atalho": "Choose shortcut icon",
    "Imagens (*.png *.svg *.ico *.jpg *.jpeg *.bmp *.webp)": "Images (*.png *.svg *.ico *.jpg *.jpeg *.bmp *.webp)",
    (
        "Não foi possível converter o ícone .ico.\nSerá usado o ícone "
        "padrão do sistema."
    ): (
        "Couldn't convert the .ico icon.\nThe system's default icon "
        "will be used."
    ),
    "Atalho criado no menu de aplicativos.": "Shortcut created in the application menu.",
    (
        "Atalho criado na área de trabalho (pode pedir pra "
        "confiar/executar na primeira vez que clicar)."
    ): (
        "Shortcut created on the desktop (it may ask you to trust/run "
        "it the first time you click it)."
    ),
    "Processos do {app}": "{app} processes",
    (
        "Processos abertos pelo launcher pra rodar seus jogos — Wine, "
        "Nativo ou comando personalizado — inclusive os que continuam "
        "rodando depois que o jogo em si já fechou. O monitor de "
        "sistema mostra tudo isso como se fosse do {app}, então é aqui "
        "que dá pra ver e, se precisar, encerrar."
    ): (
        "Processes opened by the launcher to run your games — Wine, "
        "Native or custom command — including those that keep running "
        "after the game itself has closed. Your system monitor shows "
        "all of them as if they belonged to {app}, so this is where "
        "you can see them and, if needed, stop them."
    ),
    (
        "Encerrar TODOS os processos de \"{name}\"?\n\nIsso inclui "
        "qualquer processo do Wine que tenha sobrado rodando em "
        "segundo plano."
    ): (
        "Stop ALL processes of \"{name}\"?\n\nThis includes any Wine "
        "process that may still be running in the background."
    ),
    "Não foi possível encerrar o processo:\n\n{error}": "Couldn't stop the process:\n\n{error}",
    "Iniciando {name}...": "Starting {name}...",
    "Falha na busca: {exc}": "Search failed: {exc}",
    "Falha ao carregar: {exc}": "Failed to load: {exc}",
    "{n} imagens. Clique numa pra escolher.": "{n} images. Click one to choose it.",
    "Encontrei e usei:\n\n{path}": "Found and used:\n\n{path}",
    "Versão instalada: {version}": "Installed version: {version}",
    (
        "Com a chave preenchida, o Rich Presence mostra a imagem do "
        "jogo ao lado do ícone do launcher (small_image). Cada pessoa "
        "precisa da própria chave: crie a sua em <a "
        "href=\"{url}\">steamgriddb.com → Preferências → API</a>. Ela "
        "fica salva só neste computador, junto das suas configurações."
    ): (
        "With the key filled in, Rich Presence shows the game's image "
        "next to the launcher icon (small_image). Everyone needs their "
        "own key: create yours at <a href=\"{url}\">steamgriddb.com → "
        "Preferences → API</a>. It is saved only on this computer, "
        "along with your settings."
    ),
    "(padrão: sons/{id}.mp3)": "(default: sons/{id}.mp3)",
    "✅ Funcionou! Imagem de “{name}”:\n{url}": "✅ It worked! Image for “{name}”:\n{url}",
    "⚠ A chave é válida, mas para “{name}”: {detail}.": "⚠ The key is valid, but for “{name}”: {detail}.",
    "❌ Falha na consulta:\n{detail}": "❌ Query failed:\n{detail}",
    (
        "Nenhum arquivo de som encontrado pra esse evento ainda. "
        "Escolha um arquivo ou coloque um '{id}.mp3' na pasta sons/."
    ): (
        "No sound file found for this event yet. Choose a file or put "
        "a '{id}.mp3' in the sons/ folder."
    ),
    "Tema exportado com sucesso para:\n\n{path}": "Theme exported successfully to:\n\n{path}",
    "Não foi possível exportar o tema:\n\n{error}": "Couldn't export the theme:\n\n{error}",
    "Não foi possível ler o arquivo de tema:\n\n{error}": "Couldn't read the theme file:\n\n{error}",
    "Não foi possível ler o arquivo selecionado:\n\n{error}": "Couldn't read the selected file:\n\n{error}",
    (
        "O arquivo selecionado é a versão {new}, que não é mais nova "
        "que a versão instalada ({current}). Nada foi alterado."
    ): (
        "The selected file is version {new}, which is not newer than "
        "the installed version ({current}). Nothing was changed."
    ),
    (
        "Foi encontrada a versão {new} (atual: {current}).\n\nDeseja "
        "atualizar agora? O launcher precisa ser reaberto depois pra "
        "aplicar as mudanças."
    ): (
        "Version {new} was found (current: {current}).\n\nUpdate now? "
        "The launcher needs to be reopened afterwards to apply the "
        "changes."
    ),
    "Não foi possível atualizar o launcher:\n\n{error}": "Couldn't update the launcher:\n\n{error}",
    "Versão instalada: {version} (reabra o launcher)": "Installed version: {version} (reopen the launcher)",
    (
        "Atualizado para a versão {new} com sucesso!\n\nFeche e abra o "
        "{app} de novo pra usar a nova versão."
    ): (
        "Updated to version {new} successfully!\n\nClose and reopen "
        "{app} to use the new version."
    ),
    "Total": "Total",
    "{app} — Gráfico de horas": "{app} — Hours chart",
    "Remover \"{name}\" da biblioteca?": "Remove \"{name}\" from the library?",
    "Executável não encontrado:\n\n{exe}": "Executable not found:\n\n{exe}",
    "Erro ao iniciar {name}:\n\n{error}": "Error starting {name}:\n\n{error}",
    "1 jogo na biblioteca": "1 game in the library",
    "{n} jogos na biblioteca": "{n} games in the library",
    "Jogando {name}": "Playing {name}",
    "via {runner}": "via {runner}",
    "Não foi possível copiar o ícone:\n\n{error}": "Couldn't copy the icon:\n\n{error}",
    "Não foi possível criar o wrapper:\n\n{error}": "Couldn't create the wrapper:\n\n{error}",
    "Não foi possível criar o aplicativo:\n\n{error}": "Couldn't create the application:\n\n{error}",
    (
        "Se o atalho ainda assim não abrir, rode 'bash {wrapper}' num "
        "terminal, ou confira a pasta de logs ({logs}) pra ver o erro."
    ): (
        "If the shortcut still doesn't open, run 'bash {wrapper}' in a "
        "terminal, or check the logs folder ({logs}) to see the error."
    ),
    (
        "O launcher foi organizado numa pasta própria:\n\n{app_dir}\n\n"
        "Seus jogos, imagens e configurações foram movidos para lá.\n\nA "
        "partir de agora edite e execute o arquivo {script}, porque é "
        "esse que o atalho abre."
    ): (
        "The launcher was organized into its own folder:\n\n{app_dir}\n\n"
        "Your games, images and settings were moved there.\n\nFrom now "
        "on, edit and run the file {script}, because that is the one "
        "the shortcut opens."
    ),
    "Não foi possível salvar os dados.\n\n{error}": "Couldn't save the data:\n\n{error}",
    "%d/%m/%Y %H:%M": "%m/%d/%Y %H:%M",
    "Nativo": "Native",
    "Comando personalizado": "Custom command",
    "Idioma:": "Language:",
    "Automático (idioma do sistema)": "Automatic (system language)",
    "A mudança de idioma vale depois de reabrir o launcher.": "The language change takes effect after reopening the launcher.",
    "❓ Ajuda": "❓ Help",
    "Abre a ajuda do launcher (F1).": "Opens the launcher help (F1).",
    "Ajuda — {app}": "Help — {app}",
    "Buscar na ajuda...": "Search the help...",
    "Nada encontrado.": "Nothing found.",
    "--- O processo terminou. Pressione Enter para fechar. ---": (
        "--- The process has ended. Press Enter to close. ---"
    ),
}

_TRANSLATIONS = {"en": _EN}


def detect_system_language():
    """"pt" se o sistema estiver em português, "en" em qualquer outro
    caso."""

    try:
        name = QLocale.system().name()
    except Exception:
        name = os.environ.get("LANG", "")

    return "pt" if name.lower().startswith("pt") else "en"


def set_language(choice):
    """Define o idioma ativo: "pt", "en" ou "auto" (idioma do
    sistema)."""

    global _LANG

    if choice not in ("pt", "en"):
        choice = detect_system_language()

    _LANG = choice
    return _LANG


def tr(text):
    """Traduz um texto da interface (português -> idioma ativo)."""

    table = _TRANSLATIONS.get(_LANG)

    if table is None:
        return text

    return table.get(text, text)


# Eventos de som configuráveis. O id é usado como chave nas
# configurações e também como nome do arquivo padrão esperado
# dentro da pasta "sons" (ex: sons/click.mp3). Se o usuário não
# escolher um som personalizado pra um evento, o launcher procura
# por esse arquivo padrão; se ele também não existir, o evento
# simplesmente fica mudo.
SOUND_EVENTS = [
    ("click", "Clique em botões"),
    ("start_game", "Iniciar jogo"),
    ("delete", "Excluir jogo"),
    ("conf_and_apply", "Confirmar / Aplicar"),
]


def default_sound_path(event_id):
    return SOUND_DIR / f"{event_id}.mp3"


def new_log_path(label=""):
    """Caminho de um log novo: logs/ultimalauncher_AAAA-MM-DD_HH-MM-SS.log
    (com um sufixo opcional). Se já existir um com o mesmo nome no
    mesmo segundo, acrescenta -2, -3... pra nunca sobrescrever."""

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    suffix = f"_{label}" if label else ""
    path = LOG_DIR / f"{LOG_PREFIX}_{stamp}{suffix}.log"

    n = 2
    while path.exists():
        path = LOG_DIR / f"{LOG_PREFIX}_{stamp}{suffix}-{n}.log"
        n += 1

    return path


def prune_old_logs():
    """Apaga os logs mais antigos além do limite, pra pasta não
    crescer pra sempre."""

    try:
        logs = sorted(
            LOG_DIR.glob(f"{LOG_PREFIX}_*.log"),
            key=lambda f: f.stat().st_mtime,
        )
        for old in logs[:-LOG_KEEP_MAX]:
            old.unlink()
    except OSError:
        pass


_session_log_path = None


def install_crash_logger():
    """Erros não tratados dentro do launcher viram um arquivo de log
    novo (um por execução; se acontecer mais de um erro na mesma
    execução, eles vão pro mesmo arquivo). Erros que acontecem antes
    do Python subir (falha de plugin do Qt, import quebrado etc.) são
    pegos pelo wrapper .sh, que também grava um log com data/hora."""

    def handle(exc_type, exc_value, exc_tb):
        global _session_log_path

        sys.__excepthook__(exc_type, exc_value, exc_tb)

        if issubclass(exc_type, KeyboardInterrupt):
            return

        try:
            if _session_log_path is None:
                _session_log_path = new_log_path("erro")
                prune_old_logs()

            with open(_session_log_path, "a", encoding="utf-8") as file:
                file.write(
                    f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                    f"{APP_NAME} v{LAUNCHER_VERSION}\n"
                )
                file.write(
                    "".join(
                        traceback.format_exception(
                            exc_type, exc_value, exc_tb
                        )
                    )
                )
                file.write("\n")
        except OSError:
            pass

    sys.excepthook = handle


def migrate_rust_files():
    """Move os .rs e os binários compilados que ficavam soltos na
    raiz (ou do lado do launcher.py antes da instalação) pra
    pasta rust/. Roda sempre, mesmo quando o launcher já está
    instalado em APP_DIR."""

    try:
        for origin in {APP_DIR, BASE_DIR}:
            for name in ("procscan", "coverscan"):
                for old in (origin / f"{name}.rs", origin / name):
                    if not old.is_file():
                        continue
                    destination = RUST_DIR / old.name
                    if not destination.exists():
                        shutil.move(str(old), str(destination))
    except Exception as error:
        print(f"Não foi possível organizar os arquivos Rust: {error}")


def is_managed_cover(path):
    """True se o arquivo já está dentro da pasta capas/."""

    try:
        return Path(path).expanduser().resolve().parent == COVER_DIR.resolve()
    except OSError:
        return False


def import_cover(path):
    """Copia a capa escolhida pra pasta capas/ e devolve o caminho da
    cópia (é ele que fica salvo no jogo). Se a imagem já estiver lá,
    ou não existir, devolve o caminho como veio. O nome da cópia leva
    um hash do conteúdo, então a mesma imagem nunca é copiada duas
    vezes e duas capas com o mesmo nome de arquivo não se misturam."""

    if not path:
        return path

    try:
        source = Path(path).expanduser()

        if not source.is_file() or is_managed_cover(source):
            return path

        digest = hashlib.sha1(source.read_bytes()).hexdigest()[:10]
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", source.stem).strip("-")[:40]
        extension = source.suffix.lower() or ".png"
        destination = COVER_DIR / f"{stem or 'capa'}-{digest}{extension}"

        if not destination.exists():
            shutil.copy2(source, destination)

        return str(destination)

    except OSError as error:
        print(f"Não foi possível copiar a capa pra pasta capas: {error}")
        return path


def release_cover(path, games):
    """Apaga uma cópia em capas/ que não é mais usada por nenhum
    jogo (capa trocada ou jogo removido). Arquivos fora da pasta
    capas/ nunca são tocados."""

    if not path or not is_managed_cover(path):
        return

    try:
        target = Path(path).expanduser().resolve()

        for game in games:
            other = game.get("cover", "")
            if other and Path(other).expanduser().resolve() == target:
                return

        if target.is_file():
            target.unlink()

    except OSError:
        pass


def migrate_covers_to_folder(games):
    """Jogos que já existiam com a capa em outro lugar: copia pra
    capas/ e atualiza o caminho. Devolve True se algo mudou."""

    changed = False

    for game in games:
        cover = game.get("cover", "")

        if not cover:
            continue

        new_path = import_cover(cover)

        if new_path != cover:
            game["cover"] = new_path
            changed = True

    return changed


def migrate_old_files():

    if BASE_DIR == APP_DIR:
        return

    try:

        old_data = BASE_DIR / "games.json"

        if old_data.exists() and not DATA_FILE.exists():
            shutil.move(str(old_data), str(DATA_FILE))

        old_images = BASE_DIR / "launcher-images"

        if old_images.is_dir():
            for image in old_images.iterdir():
                destination = IMAGE_DIR / image.name
                if not destination.exists():
                    shutil.move(str(image), str(destination))

        for old_icon in BASE_DIR.glob(f"{APP_SLUG}-icon.*"):
            destination = ICON_DIR / old_icon.name
            if not destination.exists():
                shutil.move(str(old_icon), str(destination))

        old_log = BASE_DIR / f"{APP_SLUG}.log"

        if old_log.exists():
            shutil.move(str(old_log), str(new_log_path("antigo")))

        fix_saved_paths(old_images)

    except Exception as error:
        print(f"Não foi possível migrar os arquivos antigos: {error}")


def fix_saved_paths(old_images):

    if not DATA_FILE.exists():
        return

    try:

        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            data = {"games": data, "settings": {}}

        changed = False

        for game in data.get("games", []):

            # "image" é o campo antigo (antes de capa e ícone
            # serem separados); "cover" e "icon" são os novos.
            for key in ("image", "cover", "icon"):

                value = game.get(key, "")

                if not value:
                    continue

                try:
                    if Path(value).exists():
                        continue
                except OSError:
                    pass

                folders = [IMAGE_DIR]
                if key == "cover":
                    folders.insert(0, COVER_DIR)

                for folder in folders:
                    candidate = folder / Path(value).name

                    if candidate.exists():
                        game[key] = str(candidate)
                        changed = True
                        break

        settings = data.get("settings", {})

        sounds = settings.get("sounds", {})

        for config in sounds.values():

            if not isinstance(config, dict):
                continue

            value = config.get("path", "")

            if not value:
                continue

            try:
                if Path(value).exists():
                    continue
            except OSError:
                pass

            candidate = SOUND_DIR / Path(value).name

            if candidate.exists():
                config["path"] = str(candidate)
                changed = True

        icon = settings.get("app_icon", "")

        if icon:

            icon_exists = False

            try:
                icon_exists = Path(icon).exists()
            except OSError:
                pass

            if not icon_exists:

                candidate = ICON_DIR / Path(icon).name

                if candidate.exists():
                    settings["app_icon"] = str(candidate)
                    changed = True
                else:
                    for possible in ICON_DIR.glob(f"{APP_SLUG}-icon*"):
                        settings["app_icon"] = str(possible)
                        changed = True
                        break
                    else:
                        settings["app_icon"] = ""
                        changed = True

        if changed:
            with open(DATA_FILE, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4, ensure_ascii=False)

    except Exception as error:
        print(f"Não foi possível corrigir os caminhos salvos: {error}")


def build_wrapper_content():

    return f"""#!/bin/bash
{WRAPPER_MARKER}
cd "$HOME/UltimaLauncher"

# Ambientes gráficos como o Plasma costumam exportar QT_PLUGIN_PATH
# apontando pro Qt do sistema. Isso não existe quando você roda
# pelo terminal (a variável simplesmente não está setada ali), mas
# via atalho o launcher pode tentar carregar os plugins do Qt do
# sistema em vez dos que vêm junto com o PySide6 da venv — e falha
# ao iniciar, às vezes sim às vezes não, dependendo de como a
# sessão gráfica exportou as variáveis daquela vez. Removendo elas
# aqui, o PySide6 sempre usa os plugins que já vêm com ele mesmo.
unset QT_PLUGIN_PATH
unset QT_QPA_PLATFORM_PLUGIN_PATH

if [ -x "$HOME/UltimaLauncher/.venv/bin/python" ]; then
    PY="$HOME/UltimaLauncher/.venv/bin/python"
elif [ -x "$HOME/game-launcher/.venv/bin/python" ]; then
    PY="$HOME/game-launcher/.venv/bin/python"
else
    PY="$(command -v python3)"
fi

# A saída vai pra um arquivo temporário. Se o launcher fechar normal
# (código 0) ele é descartado; se der erro, vira um log novo em
# logs/ com data e hora no nome — nunca reescreve o anterior.
LOG_DIR="$HOME/UltimaLauncher/logs"
mkdir -p "$LOG_DIR"
TMP_LOG="$(mktemp "$LOG_DIR/.run.XXXXXX")"

"$PY" "$HOME/UltimaLauncher/launcher.py" "$@" > "$TMP_LOG" 2>&1
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
    FINAL="$LOG_DIR/ultimalauncher_$(date +%Y-%m-%d_%H-%M-%S)_erro.log"
    N=2
    while [ -e "$FINAL" ]; do
        FINAL="$LOG_DIR/ultimalauncher_$(date +%Y-%m-%d_%H-%M-%S)_erro-$N.log"
        N=$((N + 1))
    done
    mv "$TMP_LOG" "$FINAL"
    # mantém só os 30 logs mais recentes
    ls -1t "$LOG_DIR"/ultimalauncher_*.log 2>/dev/null | tail -n +31 | xargs -r rm -f --
else
    rm -f "$TMP_LOG"
fi

exit "$STATUS"
"""


def parse_version(version_string):
    """Converte uma versão tipo '2.3.1' numa tupla de inteiros
    (2, 3, 1) pra comparar direito. Comparar como texto puro erra
    em casos tipo '2.10.0' vs '2.9.0' (texto acha que '2.10.0' é
    menor, já que '1' < '9'); como tupla de números, compara
    posição por posição e acerta."""

    parts = []

    for piece in str(version_string).strip().split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)

    return tuple(parts) if parts else (0,)


def install_script():

    if BASE_DIR == APP_DIR:
        return False

    source = Path(__file__).resolve()

    if source == SCRIPT_PATH:
        return False

    first_install = not (
        SCRIPT_PATH.exists() and SCRIPT_PATH.stat().st_size > 0
    )

    try:
        source_bytes = source.read_bytes()
    except OSError as error:
        print(f"Não foi possível ler o launcher atual: {error}")
        return False

    installed_version = "0"

    if not first_install:
        try:
            for line in SCRIPT_PATH.read_text(
                encoding="utf-8"
            ).splitlines():
                stripped = line.strip()
                if stripped.startswith("LAUNCHER_VERSION"):
                    installed_version = (
                        stripped.split("=", 1)[1]
                        .split("#")[0]
                        .strip()
                        .strip("'\"")
                    )
                    break
        except Exception:
            installed_version = "0"

    running_version = LAUNCHER_VERSION

    if (
        not first_install
        and parse_version(installed_version)
        > parse_version(running_version)
    ):

        print(
            f"A cópia instalada em {SCRIPT_PATH} é mais nova "
            f"(v{installed_version}) do que este arquivo "
            f"(v{running_version}). Não vou sobrescrever."
        )

        try:
            source.unlink()
        except OSError as error:
            print(f"Não consegui remover {source}: {error}")

        return False

    needs_copy = True

    if not first_install:
        try:
            if SCRIPT_PATH.read_bytes() == source_bytes:
                needs_copy = False
        except OSError:
            needs_copy = True

    if needs_copy:
        try:
            SCRIPT_PATH.write_bytes(source_bytes)
            try:
                SCRIPT_PATH.chmod(0o755)
            except OSError:
                pass
        except Exception as error:
            print(f"Não foi possível instalar o launcher: {error}")
            return False

    try:
        source.unlink()
    except OSError as error:
        print(f"Não foi possível remover {source}: {error}")

    return first_install


migrate_old_files()
migrate_rust_files()
install_crash_logger()


# Modos de exibição da capa (escolhido por jogo, no diálogo de edição).
#   crop    -> preenche o card inteiro cortando o que sobrar nas bordas
#              (sem distorcer; é o modo original).
#   stretch -> mostra a imagem INTEIRA, esticada pro tamanho do card
#              (não corta nada, mas pode distorcer).
COVER_MODES = [
    ("crop", "Preencher (corta as bordas, sem distorcer)"),
    ("stretch", "Esticar (imagem inteira, pode distorcer)"),
]
DEFAULT_COVER_MODE = "crop"

# Runners disponíveis. O nome em português é o valor guardado no
# games.json (não mude, senão os jogos já salvos perdem o runner); o
# texto mostrado na tela passa por tr().
RUNNERS = ["Wine", "Nativo", "Comando personalizado"]


DEFAULT_GAME_STATS = {
    "times_played": 0,
    "total_playtime_seconds": 0,
    "longest_session_seconds": 0,
    "last_played": None,
}


DEFAULT_SETTINGS = {
    "accent": "#8b5cf6",
    "background": "",
    "background_opacity": 35,
    "card_color": "#20202c",
    "card_opacity": 90,
    "border_color": "#3a3a4a",
    "border_width": 1,
    "border_radius": 14,
    "app_icon": "",
    "discord_rpc_enabled": True,
    "steamgriddb_api_key": "",
    "button_color": "#2a2a3a",
    "button_opacity": 100,
    "button_text_color": "#ffffff",
    "button_border_color": "#3a3a4a",
    "button_border_width": 1,
    "button_border_radius": 8,
    "button_hover_color": "",
    "loading_window_enabled": True,
    "loading_window_seconds": 8,
    "wine_debug_enabled": False,
    "view_mode": "grade",
    "language": "auto",
    "reorder_mode_enabled": False,
    "sound_enabled": True,
    "sound_volume": 100,
    "sounds": {
        event_id: {"enabled": True, "path": "", "volume": 100}
        for event_id, _ in SOUND_EVENTS
    },
}


def merge_sound_settings(saved_sounds):
    """Combina os sons salvos com os padrões, sem perder eventos
    novos que ainda não existiam quando o usuário salvou pela
    última vez, e sem arriscar mutar o dicionário padrão."""

    result = copy.deepcopy(DEFAULT_SETTINGS["sounds"])

    if isinstance(saved_sounds, dict):
        for event_id, config in saved_sounds.items():
            if event_id in result and isinstance(config, dict):
                result[event_id].update(config)

    return result


# Chaves de configuração que fazem parte de um "tema" (usadas na
# exportação/importação de temas nas Configurações). Coisas como
# modo de exibição, Discord RPC etc. não fazem parte do tema.
THEME_KEYS = [
    "accent",
    "background",
    "background_opacity",
    "card_color",
    "card_opacity",
    "border_color",
    "border_width",
    "border_radius",
    "button_color",
    "button_opacity",
    "button_text_color",
    "button_border_color",
    "button_border_width",
    "button_border_radius",
    "button_hover_color",
]


def hex_to_rgba(hex_color, opacity_percent):

    hex_color = hex_color.lstrip("#")

    if len(hex_color) != 6:
        hex_color = "000000"

    red = int(hex_color[0:2], 16)
    green = int(hex_color[2:4], 16)
    blue = int(hex_color[4:6], 16)

    alpha = int(255 * opacity_percent / 100)

    return f"rgba({red}, {green}, {blue}, {alpha})"


def extract_launcher_version(source_text):

    for line in source_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("LAUNCHER_VERSION"):
            try:
                return (
                    stripped.split("=", 1)[1]
                    .split("#")[0]
                    .strip()
                    .strip("'\"")
                )
            except Exception:
                return None

    return None


def format_playtime(seconds):

    seconds = int(seconds or 0)

    if seconds <= 0:
        return tr("Nunca jogado")

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}min"

    return f"{minutes}min"


def format_last_played(timestamp):

    if not timestamp:
        return tr("Nunca")

    return time.strftime(
        tr("%d/%m/%Y %H:%M"),
        time.localtime(timestamp),
    )


def _looks_like_icon_file(path):
    """Reaproveita a lógica antiga de detectar se uma imagem tem
    cara de ícone (proporção quase quadrada). Usada só uma vez, na
    migração de jogos salvos antes de capa e ícone virarem campos
    separados."""

    try:
        reader = QImageReader(path)
        size = reader.size()

        if not size.isValid() or size.height() <= 0:
            return False

        aspect_ratio = size.width() / size.height()
        return 0.8 <= aspect_ratio <= 1.25

    except Exception:
        return False


def migrate_game_image_fields(game):
    """Antes, um jogo tinha só o campo 'image', usado tanto pra
    capa quanto pra ícone (adivinhando qual era qual pela
    proporção). Agora são dois campos: 'cover' e 'icon'. Essa
    função roda uma vez por jogo antigo pra separar os dois."""

    if "cover" in game or "icon" in game:
        game.setdefault("cover", "")
        game.setdefault("icon", "")
        return

    legacy_image = game.get("image", "")

    game["cover"] = ""
    game["icon"] = ""

    if not legacy_image:
        return

    expanded = os.path.expanduser(legacy_image)

    if os.path.exists(expanded) and _looks_like_icon_file(expanded):
        game["icon"] = legacy_image
    else:
        game["cover"] = legacy_image


def load_data():

    if not DATA_FILE.exists():
        settings = DEFAULT_SETTINGS.copy()
        settings["sounds"] = merge_sound_settings(None)
        return [], settings

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            for game in data:
                for key, default_value in DEFAULT_GAME_STATS.items():
                    game.setdefault(key, default_value)
                game.setdefault("show_image", True)
                game.setdefault("show_icon_badge", True)
                game.setdefault("cover_mode", DEFAULT_COVER_MODE)
                migrate_game_image_fields(game)

            settings = DEFAULT_SETTINGS.copy()
            settings["sounds"] = merge_sound_settings(None)
            return data, settings

        games = data.get("games", [])
        settings = data.get("settings", DEFAULT_SETTINGS.copy())

        merged_settings = DEFAULT_SETTINGS.copy()
        merged_settings.update(settings)
        merged_settings["sounds"] = merge_sound_settings(
            settings.get("sounds")
        )

        for game in games:
            for key, default_value in DEFAULT_GAME_STATS.items():
                game.setdefault(key, default_value)
            game.setdefault("show_image", True)
            game.setdefault("show_icon_badge", True)
            game.setdefault("cover_mode", DEFAULT_COVER_MODE)
            migrate_game_image_fields(game)

        return games, merged_settings

    except Exception as error:
        print(f"Erro ao carregar games.json: {error}")
        settings = DEFAULT_SETTINGS.copy()
        settings["sounds"] = merge_sound_settings(None)
        return [], settings


def save_data(games, settings):

    try:
        data = {"games": games, "settings": settings}

        with open(DATA_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)

    except Exception as error:
        QMessageBox.critical(
            None,
            APP_NAME,
            tr("Não foi possível salvar os dados.\n\n{error}").format(
                error=error
            ),
        )


def extract_exe_icon(exe_path, output_path):

    try:
        from PIL import Image
    except ImportError:
        print("Pillow não está instalado.")
        return False

    exe_path = Path(exe_path)
    output_path = Path(output_path)
    icoextract_bin = Path(sys.executable).parent / "icoextract"

    if not icoextract_bin.exists():
        print("Não foi encontrado o executável 'icoextract'.")
        return False

    if not exe_path.exists():
        print(f"EXE não encontrado: {exe_path}")
        return False

    temp_ico = output_path.with_name(output_path.stem + "_temp.ico")

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            [str(icoextract_bin), str(exe_path), str(temp_ico)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if not temp_ico.exists() or temp_ico.stat().st_size == 0:
            return False

        with Image.open(temp_ico) as ico:

            try:
                frame_count = ico.n_frames
            except AttributeError:
                frame_count = 1

            frames = []

            for frame in range(frame_count):
                try:
                    ico.seek(frame)
                    width, height = ico.size
                    if width > 0 and height > 0:
                        frames.append(
                            (width * height, width, height, ico.copy())
                        )
                except EOFError:
                    break

            if not frames:
                return False

            frames.sort(key=lambda item: item[0], reverse=True)
            _, width, height, image = frames[0]

            image = image.convert("RGBA")
            image.save(output_path, "PNG")
            image.close()

        return (
            output_path.exists() and output_path.stat().st_size > 0
        )

    except Exception as error:
        print(f"Não foi possível processar o ícone: {error}")
        return False

    finally:
        try:
            if temp_ico.exists():
                temp_ico.unlink()
        except OSError:
            pass


def convert_ico_to_png(ico_path, output_path):

    try:
        from PIL import Image
    except ImportError:
        print("Pillow não está instalado.")
        return False

    try:
        with Image.open(ico_path) as ico:

            try:
                frame_count = ico.n_frames
            except AttributeError:
                frame_count = 1

            best_image = None
            best_area = 0

            for frame in range(frame_count):
                try:
                    ico.seek(frame)
                except EOFError:
                    break
                width, height = ico.size
                area = width * height
                if area > best_area:
                    best_area = area
                    best_image = ico.copy()

            if best_image is None:
                return False

            best_image.convert("RGBA").save(output_path, "PNG")

        return True

    except Exception as error:
        print(f"Não foi possível converter o ícone: {error}")
        return False


# ---- sons ----

class SoundManager:
    """Toca os efeitos sonoros do launcher (click, start_game,
    delete, conf_and_apply). Se o QtMultimedia não estiver
    instalado (SOUND_BACKEND_AVAILABLE == False), todos os
    métodos viram no-ops silenciosos — o launcher continua
    funcionando normalmente, só sem som."""

    def __init__(self, settings):

        self.settings = settings
        self.players = {}

        if not SOUND_BACKEND_AVAILABLE:
            return

        for event_id, _ in SOUND_EVENTS:
            player = QMediaPlayer()
            audio_output = QAudioOutput()
            player.setAudioOutput(audio_output)
            self.players[event_id] = (player, audio_output)

    def update_settings(self, settings):
        self.settings = settings

    def resolve_path(self, event_id):

        config = self.settings.get("sounds", {}).get(event_id, {})
        custom_path = config.get("path", "")

        if custom_path:
            expanded = os.path.expanduser(custom_path)
            if os.path.isfile(expanded):
                return expanded

        default_path = default_sound_path(event_id)

        if default_path.is_file():
            return str(default_path)

        return None

    def play(self, event_id):

        if not SOUND_BACKEND_AVAILABLE:
            return

        if not self.settings.get("sound_enabled", True):
            return

        config = self.settings.get("sounds", {}).get(event_id, {})

        if not config.get("enabled", True):
            return

        path = self.resolve_path(event_id)

        if not path:
            return

        pair = self.players.get(event_id)

        if pair is None:
            return

        player, audio_output = pair

        global_volume = self.settings.get("sound_volume", 100)
        event_volume = config.get("volume", 100)

        final_volume = (
            max(0, min(100, global_volume))
            / 100
            * max(0, min(100, event_volume))
            / 100
        )

        audio_output.setVolume(final_volume)
        player.setSource(QUrl.fromLocalFile(path))
        player.play()


def wire_click_sounds(widget, sound_manager, exempt=()):
    """Conecta o som 'click' a todos os QPushButton dentro de
    `widget` (usando findChildren, então pega tanto os já
    existentes quanto qualquer outro criado antes desta chamada).
    Botões que já tocam um som próprio e específico (como o
    'Aplicar' das Configurações) devem ser passados em `exempt`
    pra não tocar os dois sons."""

    if sound_manager is None:
        return

    for button in widget.findChildren(QPushButton):
        if button not in exempt:
            button.clicked.connect(
                lambda _checked=False, sm=sound_manager:
                    sm.play("click")
            )


class SteamGridAuthError(Exception):
    """A API recusou a chave (inválida ou revogada)."""


def steamgrid_get(path, api_key, params=None):
    """GET na API v2 do SteamGridDB. Devolve a lista de "data" (vazia
    se não houve resultado). Levanta SteamGridAuthError se a chave
    foi recusada; outros erros de rede sobem como exceção normal."""

    url = STEAMGRIDDB_API + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": f"{APP_SLUG}/{LAUNCHER_VERSION}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise SteamGridAuthError(str(error))
        if error.code == 404:
            return []

        try:
            body = error.read().decode("utf-8", "replace")[:200]
        except Exception:
            body = ""

        raise RuntimeError(f"HTTP {error.code} em {path} {body}".strip())

    if not isinstance(payload, dict) or not payload.get("success"):
        return []

    data = payload.get("data")

    return data if isinstance(data, list) else []


def steamgrid_search_terms(game_name):
    """Nome original e uma versão "limpa" (sem parênteses/colchetes
    nem ™®©) — nomes digitados à mão costumam ter esses extras."""

    terms = []

    original = (game_name or "").strip()
    simplified = re.sub(r"[\(\[].*?[\)\]]", "", original)
    simplified = re.sub(r"[™®©]", "", simplified)
    simplified = re.sub(r"\s+", " ", simplified).strip()

    for term in (original, simplified):
        if term and term not in terms:
            terms.append(term)

    return terms


def steamgrid_pick_game_id(results, term):

    first_id = None

    for item in results:

        if not isinstance(item, dict) or not isinstance(
            item.get("id"), int
        ):
            continue

        if first_id is None:
            first_id = item["id"]

        if str(item.get("name", "")).strip().lower() == term.lower():
            return item["id"]

    return first_id


def steamgrid_pick_image(items):
    """Primeira imagem utilizável da lista (a API já ordena pela
    nota). Prefere a miniatura — o Discord mostra isso bem pequeno."""

    for item in items:

        if not isinstance(item, dict):
            continue

        for key in ("thumb", "url"):
            value = item.get(key)

            if (
                isinstance(value, str)
                and value.startswith("https://")
                and not value.lower().endswith(".ico")
            ):
                return value

    return ""


def steamgrid_find_image(api_key, game_name):
    """Procura a imagem do jogo. Devolve (url, status, detalhe):
    "ok" (achou), "none" (jogo/imagem não existe lá), "auth"
    (chave recusada) ou "error" (rede/API com problema — o detalhe
    diz o quê)."""

    last_error = ""

    try:
        game_id = None

        for term in steamgrid_search_terms(game_name):

            try:
                results = steamgrid_get(
                    "/search/autocomplete/"
                    + urllib.parse.quote(term, safe=""),
                    api_key,
                )
            except SteamGridAuthError:
                raise
            except Exception as error:
                last_error = str(error)
                continue

            game_id = steamgrid_pick_game_id(results, term)

            if game_id is not None:
                break

        if game_id is None:
            if last_error:
                return "", "error", last_error
            return "", "none", tr("jogo não encontrado no SteamGridDB")

        # Cada endpoint aceita formatos diferentes (os ícones só
        # aceitam PNG/ICO, por exemplo — pedir JPEG neles dá erro),
        # então cada tentativa leva os próprios parâmetros. Se uma
        # falhar, a próxima ainda roda.
        attempts = [
            (
                f"/icons/game/{game_id}",
                {"mimes": "image/png", "types": "static"},
            ),
            (
                f"/grids/game/{game_id}",
                {
                    "dimensions": "512x512,1024x1024",
                    "mimes": "image/png,image/jpeg",
                    "types": "static",
                },
            ),
            (
                f"/grids/game/{game_id}",
                {"mimes": "image/png,image/jpeg", "types": "static"},
            ),
            (f"/grids/game/{game_id}", {}),
        ]

        for path, params in attempts:

            try:
                items = steamgrid_get(path, api_key, params)
            except SteamGridAuthError:
                raise
            except Exception as error:
                last_error = str(error)
                continue

            url = steamgrid_pick_image(items)

            if url:
                return url, "ok", ""

        if last_error:
            return "", "error", last_error

        return "", "none", (
            tr("o jogo existe lá, mas não tem imagem utilizável")
        )

    except SteamGridAuthError:
        return "", "auth", tr("chave recusada pelo SteamGridDB")
    except Exception as error:
        return "", "error", str(error)


def load_steamgrid_cache():

    try:
        with open(STEAMGRID_CACHE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_steamgrid_cache(cache):

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(STEAMGRID_CACHE_FILE, "w", encoding="utf-8") as file:
            json.dump(cache, file, indent=2, ensure_ascii=False)
    except Exception as error:
        print(f"SteamGridDB: não foi possível salvar o cache ({error})")


class SteamGridSignals(QObject):
    """Ponte entre a thread que consulta a API e a janela: o sinal
    emitido de fora da thread principal chega na janela já na
    thread certa."""

    resolved = Signal(str, str, str)  # nome, url, status


class DiscordPresence:

    def __init__(self):
        self.client = None
        self.connected = False
        self.enabled = False
        self.client_id = ""
        self.pending_state = None
        self._rpc_class = None
        self._rpc_class_loaded = False

    def _get_rpc_class(self):

        if self._rpc_class_loaded:
            return self._rpc_class

        self._rpc_class_loaded = True

        try:
            from pypresence import Presence as RPCClass
            self._rpc_class = RPCClass
        except ImportError:
            print("pypresence não está instalado.")
            self._rpc_class = None

        return self._rpc_class

    def configure(self, enabled, client_id):

        client_id_changed = client_id != self.client_id

        self.enabled = enabled
        self.client_id = client_id

        if not self.enabled:
            self.disconnect()
            return

        if client_id_changed:
            self.disconnect()

        if not self.connected:
            self.try_connect()

    def try_connect(self):

        if not self.enabled or self.connected:
            return self.connected

        rpc_class = self._get_rpc_class()

        if rpc_class is None:
            return False

        if not self.client_id:
            return False

        try:
            self.client = rpc_class(self.client_id)
            self.client.connect()
            self.connected = True

            if self.pending_state:
                self.send(**self.pending_state)

            return True

        except Exception as error:
            print(
                f"Rich Presence: não foi possível conectar ao "
                f"Discord ({error})"
            )
            self.client = None
            self.connected = False
            return False

    def send(self, **kwargs):

        self.pending_state = kwargs

        if not self.enabled:
            return

        if not self.connected and not self.try_connect():
            return

        try:
            self.client.update(**kwargs)
        except Exception as error:
            print(f"Rich Presence: falha ao atualizar ({error})")
            self.client = None
            self.connected = False

    def clear(self):

        self.pending_state = None

        if self.connected and self.client:
            try:
                self.client.clear()
            except Exception:
                pass

    def disconnect(self):

        self.pending_state = None

        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

        self.client = None
        self.connected = False


def launch_in_terminal(shell_command, cwd):

    ended_message = tr(
        "--- O processo terminou. Pressione Enter para fechar. ---"
    )

    wrapped = (
        f"{shell_command}; "
        f"echo; "
        f"echo {shlex.quote(ended_message)}; "
        f"read"
    )

    candidates = [
        ("x-terminal-emulator", ["-e", "bash", "-c", wrapped]),
        ("gnome-terminal", ["--wait", "--", "bash", "-c", wrapped]),
        ("konsole", ["-e", "bash", "-c", wrapped]),
        ("xfce4-terminal", ["-x", "bash", "-c", wrapped]),
        ("mate-terminal", ["-e", "bash", "-c", wrapped]),
        ("xterm", ["-e", "bash", "-c", wrapped]),
    ]

    for terminal, args in candidates:
        if shutil.which(terminal):
            return subprocess.Popen(
                [terminal] + args, cwd=cwd, start_new_session=True,
            )

    raise RuntimeError(
        tr("Nenhum terminal compatível foi encontrado. "
        "Instale xterm, gnome-terminal, konsole, "
        "xfce4-terminal ou mate-terminal.")
    )


# ---- processos (rastreio de sessões de jogo) ----
# Todo jogo é iniciado com start_new_session=True, o que faz do
# processo direto (wine/exe/comando) o líder de um grupo de
# processos novo — cujo id (pgid) é igual ao próprio PID desse
# processo. Como praticamente tudo que ele criar por baixo dos
# panos (wineserver, o .exe traduzido, processos internos do Wine
# tipo services.exe/explorer.exe) herda esse mesmo grupo, dá pra
# encontrar e encerrar a árvore inteira mais tarde — mesmo que o
# processo original que rastreamos já tenha saído — só perguntando
# ao sistema "quem ainda está vivo nesse grupo?" via /proc.

def list_pids_in_group(pgid):
    """Retorna os PIDs de todos os processos vivos que pertencem ao
    grupo de processos `pgid` (Linux, lendo /proc)."""

    pids = []

    if not pgid:
        return pids

    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids

    for entry in entries:

        if not entry.isdigit():
            continue

        pid = int(entry)

        try:
            if os.getpgid(pid) == pgid:
                pids.append(pid)
        except (ProcessLookupError, PermissionError, OSError):
            continue

    return pids


def list_pids_by_wineprefix(prefix):
    """Retorna os PIDs de todo processo cujo WINEPREFIX (lido do
    ambiente dele em /proc/[pid]/environ) bate com `prefix`. Existe
    porque o wineserver e vários processos internos do Wine
    (explorer.exe, services.exe, plugplay.exe etc.) costumam se
    desgarrar do grupo/sessão de processos original chamando
    setsid() por conta própria — pra sobreviver independente de
    quem os iniciou — então rastrear só por pgid não é suficiente
    pra achar tudo que sobra rodando depois que o jogo fecha."""

    pids = []

    if not prefix:
        return pids

    prefix = os.path.normpath(prefix)

    try:
        entries = os.listdir("/proc")
    except OSError:
        return pids

    for entry in entries:

        if not entry.isdigit():
            continue

        pid = int(entry)

        try:
            with open(f"/proc/{pid}/environ", "rb") as file:
                raw = file.read()
        except OSError:
            continue

        for chunk in raw.split(b"\x00"):
            if chunk.startswith(b"WINEPREFIX="):
                value = chunk[len(b"WINEPREFIX="):].decode(
                    "utf-8", errors="replace"
                )
                if os.path.normpath(value) == prefix:
                    pids.append(pid)
                break

    return pids


def list_session_pids(session):
    """União dos PIDs achados pelo grupo de processos (pgid) e,
    pra sessões do Wine, também pelo WINEPREFIX — assim pega tanto
    o processo principal quanto qualquer coisa do Wine que tenha
    se desgarrado pra rodar em segundo plano (ex: wineserver e os
    processos internos que ele sobe junto)."""

    pids = set(list_pids_in_group(session.get("pgid")))

    prefix = session.get("prefix")

    if prefix:
        pids.update(list_pids_by_wineprefix(prefix))

    return sorted(pids)


def read_process_info(pid):
    """Lê nome, memória residente (em MB) e os contadores de tempo
    de CPU (em jiffies) de um processo direto do /proc. Retorna
    None se o processo não existir mais."""

    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as file:
            raw = file.read()
    except OSError:
        return None

    # O campo "comm" vem entre parênteses e pode ter espaços, então
    # cortamos pelo último ")" pra não bagunçar os campos numéricos
    # que vêm depois dele.
    name_start = raw.find("(")
    name_end = raw.rfind(")")

    if name_start == -1 or name_end == -1:
        return None

    name = raw[name_start + 1:name_end]
    rest = raw[name_end + 2:].split()

    try:
        utime = int(rest[11])
        stime = int(rest[12])
    except (IndexError, ValueError):
        utime = stime = 0

    rss_mb = 0.0

    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as file:
            for line in file:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        rss_mb = int(parts[1]) / 1024
                    break
    except OSError:
        pass

    return {
        "pid": pid,
        "name": name,
        "cpu_ticks": utime + stime,
        "rss_mb": rss_mb,
    }


# ---- procscan (Rust) — acelerador opcional do rastreio acima ----
# list_pids_in_group + list_pids_by_wineprefix + read_process_info
# (tudo acima) continuam existindo e são o fallback de sempre —
# funcionam em qualquer máquina, só com Python. O procscan é a
# MESMA lógica portada pra Rust (é um porte fiel, não enxerga nada
# a mais), usada só porque o monitor de processos varre /proc a
# cada 2 segundos enquanto está aberto, e em Rust isso é bem mais
# barato do que abrir uma porção de arquivos pequenos em Python a
# cada rodada. Se o binário não existir, não compilar, ou falhar
# por qualquer razão, cai pro caminho em Python sem avisar nada —
# na prática o usuário nem percebe a diferença, só o launcher fica
# mais leve enquanto o monitor está aberto.

PROCSCAN_SOURCE = RUST_DIR / "procscan.rs"
PROCSCAN_BINARY = RUST_DIR / "procscan"

_procscan_checked = False


def ensure_procscan_binary():
    """Se o binário ainda não existir mas o código-fonte estiver do
    lado do launcher.py e tiver um `rustc` instalado, tenta compilar
    uma vez (só isso — sem exigir Rust de quem não tiver). Guarda
    numa variável global pra não tentar de novo a cada vez que o
    monitor de processos é aberto na mesma sessão do launcher."""

    global _procscan_checked

    if _procscan_checked:
        return

    _procscan_checked = True

    if PROCSCAN_BINARY.is_file() and os.access(PROCSCAN_BINARY, os.X_OK):
        return

    if not PROCSCAN_SOURCE.is_file():
        return

    rustc = shutil.which("rustc")

    if not rustc:
        return

    try:
        subprocess.run(
            [rustc, "-O", str(PROCSCAN_SOURCE), "-o", str(PROCSCAN_BINARY)],
            capture_output=True,
            timeout=30,
        )
    except Exception as error:
        print(f"Não foi possível compilar o procscan: {error}")


def read_session_processes_native(pgid, prefix):
    """Chama o procscan pra achar, numa passada só, todo processo
    da sessão (por pgid e por WINEPREFIX) já com nome/RAM/CPU.
    Devolve None se o binário não existir/falhar — quem chamou
    deve cair pro caminho em Python (list_session_pids +
    read_process_info) nesse caso."""

    if not PROCSCAN_BINARY.is_file() or not os.access(
        PROCSCAN_BINARY, os.X_OK
    ):
        return None

    try:
        result = subprocess.run(
            [str(PROCSCAN_BINARY), str(pgid or 0), prefix or ""],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception as error:
        print(f"procscan falhou ao rodar: {error}")
        return None

    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, list):
        return None

    processes = []

    for item in data:
        try:
            processes.append({
                "pid": int(item["pid"]),
                "name": str(item["name"]),
                "cpu_ticks": int(item["cpu_ticks"]),
                "rss_mb": float(item["rss_mb"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return processes


def read_session_processes(session):
    """Lista os processos de uma sessão já com nome/RAM/CPU — via
    procscan se ele estiver disponível, senão via Python puro
    (list_session_pids + read_process_info, de sempre)."""

    native = read_session_processes_native(
        session.get("pgid"), session.get("prefix"),
    )

    if native is not None:
        return native

    processes = []

    for pid in list_session_pids(session):
        info = read_process_info(pid)
        if info is not None:
            processes.append(info)

    return processes


# ---- coverscan (Rust) — busca capa nos arquivos do próprio jogo ----
# Diferente do procscan (que acelera algo que já existia em
# Python), essa é uma tarefa nova — então, pra já começar deixando
# o Python mais leve, ela só existe em Rust mesmo: sem binário
# compilado, o launcher simplesmente não sugere nada sozinho e o
# usuário escolhe a capa manualmente como sempre (nada quebra).

COVERSCAN_SOURCE = RUST_DIR / "coverscan.rs"
COVERSCAN_BINARY = RUST_DIR / "coverscan"

_coverscan_checked = False


def ensure_coverscan_binary():
    """Mesmo esquema do ensure_procscan_binary: compila uma vez só,
    se tiver o .rs do lado e um `rustc` instalado. Sem aviso nenhum
    se não der — é só um bônus."""

    global _coverscan_checked

    if _coverscan_checked:
        return

    _coverscan_checked = True

    if COVERSCAN_BINARY.is_file() and os.access(
        COVERSCAN_BINARY, os.X_OK
    ):
        return

    if not COVERSCAN_SOURCE.is_file():
        return

    rustc = shutil.which("rustc")

    if not rustc:
        return

    try:
        subprocess.run(
            [rustc, "-O", str(COVERSCAN_SOURCE), "-o", str(COVERSCAN_BINARY)],
            capture_output=True,
            timeout=30,
        )
    except Exception as error:
        print(f"Não foi possível compilar o coverscan: {error}")


def detect_cover_candidates(exe_path):
    """Pergunta pro coverscan se ele acha alguma capa solta nos
    arquivos do jogo (pasta do exe, subpastas de arte/mídia comuns
    e a pasta de cima). Devolve a lista de candidatos (já ordenada
    do melhor pro pior) ou [] se o binário não existir/falhar."""

    if not COVERSCAN_BINARY.is_file() or not os.access(
        COVERSCAN_BINARY, os.X_OK
    ):
        return []

    try:
        result = subprocess.run(
            [str(COVERSCAN_BINARY), exe_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as error:
        print(f"coverscan falhou ao rodar: {error}")
        return []

    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(data, list):
        return []

    candidates = []

    for item in data:
        try:
            candidates.append({
                "path": str(item["path"]),
                "score": int(item["score"]),
                "size": int(item["size"]),
            })
        except (KeyError, TypeError, ValueError):
            continue

    return candidates


def kill_session(session, grace_seconds=3):
    """Encerra a sessão inteira: manda SIGTERM pro grupo de
    processos e, se for do Wine, também em qualquer processo
    achado pelo WINEPREFIX que não esteja nesse grupo. Depois do
    prazo de tolerância, manda SIGKILL no que ainda sobrar."""

    pgid = session.get("pgid")

    if pgid:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as error:
            print(f"Não foi possível encerrar o grupo {pgid}: {error}")

    for pid in list_session_pids(session):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            pass

    def force_kill():

        if pgid:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass

        for pid in list_session_pids(session):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

    QTimer.singleShot(int(grace_seconds * 1000), force_kill)


class ProcessMonitorDialog(QDialog):
    """Mostra os processos que o UltimaLauncher abriu — incluindo
    os que sobraram rodando depois que o jogo já fechou (tipo
    wineserver e outros processos internos do Wine) — e deixa
    encerrar tanto um processo específico quanto a sessão inteira."""

    def __init__(self, launcher, parent=None):

        super().__init__(parent)

        self.launcher = launcher
        self._prev_samples = {}

        # Tenta compilar o procscan (se tiver rustc e o .rs do
        # lado) só agora, na primeira vez que o monitor é aberto —
        # não no boot do launcher, que já tem coisa demais pra
        # fazer logo de cara.
        ensure_procscan_binary()

        self.setWindowTitle(tr("Processos do {app}").format(app=APP_NAME))
        self.setMinimumSize(700, 420)

        layout = QVBoxLayout(self)

        info = QLabel(
            tr(
                "Processos abertos pelo launcher pra rodar seus jogos "
                "— Wine, Nativo ou comando personalizado — inclusive "
                "os que continuam rodando depois que o jogo em si já "
                "fechou. O monitor de sistema mostra tudo isso como se "
                "fosse do {app}, então é aqui que dá pra ver e, "
                "se precisar, encerrar."
            ).format(app=APP_NAME)
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(info)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [tr("Jogo"), "PID", tr("Processo"), "RAM", "CPU"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        header.setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table)

        self.empty_label = QLabel(
            tr("Nenhum processo do launcher rodando agora. 🎉")
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(
            "color: #888; padding: 30px;"
        )
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

        buttons = QHBoxLayout()

        kill_session_button = QPushButton(
            tr("⏹ Encerrar sessão selecionada")
        )
        kill_session_button.clicked.connect(self.kill_selected_session)
        buttons.addWidget(kill_session_button)

        kill_process_button = QPushButton(
            tr("✖ Encerrar só este processo")
        )
        kill_process_button.clicked.connect(self.kill_selected_process)
        buttons.addWidget(kill_process_button)

        buttons.addStretch()

        refresh_button = QPushButton(tr("🔄 Atualizar lista"))
        refresh_button.setToolTip(
            tr("Atualiza a lista de processos agora (ela já se "
            "atualiza sozinha a cada 2 segundos).")
        )
        refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(refresh_button)

        close_button = QPushButton(tr("Fechar"))
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)

        layout.addLayout(buttons)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)

        self.refresh()

    def refresh(self):

        self.launcher.prune_dead_sessions()

        rows = []
        seen_pids = set()

        for session in self.launcher.game_sessions:
            for info in read_session_processes(session):
                pid = info["pid"]
                if pid in seen_pids:
                    continue
                rows.append((session, info))
                seen_pids.add(pid)

        self.empty_label.setVisible(not rows)
        self.table.setVisible(bool(rows))

        previously_selected = self.selected_pid()

        self.table.setRowCount(len(rows))

        now = time.time()
        clk_tck = os.sysconf("SC_CLK_TCK") or 100

        for row, (session, info) in enumerate(rows):

            pid = info["pid"]
            previous = self._prev_samples.get(pid)

            cpu_percent = 0.0

            if previous is not None:

                prev_ticks, prev_time = previous
                delta_time = now - prev_time

                if delta_time > 0:
                    cpu_percent = max(
                        0.0,
                        100.0 * (info["cpu_ticks"] - prev_ticks)
                        / clk_tck / delta_time,
                    )

            self._prev_samples[pid] = (info["cpu_ticks"], now)

            values = [
                session.get("name", "?"),
                str(pid),
                info["name"],
                f'{info["rss_mb"]:.0f} MB',
                f"{cpu_percent:.1f}%",
            ]

            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(
                    Qt.ItemDataRole.UserRole, (session, pid),
                )
                self.table.setItem(row, column, item)

            if pid == previously_selected:
                self.table.selectRow(row)

        live_pids = {info["pid"] for _, info in rows}

        for pid in list(self._prev_samples):
            if pid not in live_pids:
                self._prev_samples.pop(pid, None)

    def selected_session_and_pid(self):

        items = self.table.selectedItems()

        if not items:
            return None, None

        data = items[0].data(Qt.ItemDataRole.UserRole)

        if not data:
            return None, None

        return data

    def selected_pid(self):

        _session, pid = self.selected_session_and_pid()
        return pid

    def kill_selected_session(self):

        session, _pid = self.selected_session_and_pid()

        if session is None:
            QMessageBox.information(
                self, APP_NAME,
                tr("Selecione um processo na lista primeiro."),
            )
            return

        session_name = session.get("name", tr("essa sessão"))

        answer = QMessageBox.question(
            self, APP_NAME,
            tr(
                'Encerrar TODOS os processos de "{name}"?\n\n'
                "Isso inclui qualquer processo do Wine que tenha "
                "sobrado rodando em segundo plano."
            ).format(name=session_name),
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        kill_session(session)

        QTimer.singleShot(600, self.refresh)

    def kill_selected_process(self):

        _session, pid = self.selected_session_and_pid()

        if pid is None:
            QMessageBox.information(
                self, APP_NAME,
                tr("Selecione um processo na lista primeiro."),
            )
            return

        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as error:
            QMessageBox.warning(
                self, APP_NAME,
                tr("Não foi possível encerrar o processo:\n\n{error}").format(
                    error=error
                ),
            )
            return

        QTimer.singleShot(600, self.refresh)

    def closeEvent(self, event):

        self.timer.stop()
        super().closeEvent(event)


# ---- tela de boot (splash) ----

class BootSplash(QWidget):
    """
    Tela preta de abertura que digita o texto letra por letra e
    depois faz fade-out, fechando sozinha.
    """

    MESSAGE = "Ultima... Your Ultimate launcher"
    CHAR_DELAY = 42
    DOT_PAUSE = 500
    HOLD_AFTER_TYPING = 500
    FADE_DURATION = 600

    def __init__(self):
        super().__init__(None)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.SplashScreen
        )

        self.setAttribute(
            Qt.WidgetAttribute.WA_DeleteOnClose, True
        )

        self.setStyleSheet("background: #000000;")

        self.label = QLabel("", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(
            "color: #e5e5e5;"
            "background: transparent;"
            "font-family: 'JetBrains Mono', 'Fira Code', "
            "'Cascadia Mono', 'Consolas', monospace;"
            "font-size: 26px;"
            "letter-spacing: 2px;"
        )
        self.label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(60, 60, 60, 60)
        layout.addStretch()
        layout.addWidget(self.label)
        layout.addStretch()

        self._full_text = self.MESSAGE
        self._visible_length = 0
        self._fading = False

        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(self.CHAR_DELAY)
        self._typing_timer.timeout.connect(self._type_next_char)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(self.FADE_DURATION)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.finished.connect(self.close)

        self.showFullScreen()

        self._typing_timer.start()

    def _type_next_char(self):

        if self._visible_length >= len(self._full_text):

            self._typing_timer.stop()
            self.label.setText(self._full_text)

            QTimer.singleShot(
                self.HOLD_AFTER_TYPING,
                self._start_fade,
            )
            return

        next_char = self._full_text[self._visible_length]

        if next_char == ".":
            # Pausa um pouco ANTES de cada ponto aparecer, em vez de
            # depois — senão o primeiro ponto surgia junto/instantâneo
            # com a última letra de "Ultima", sem pausa nenhuma.
            self._typing_timer.stop()
            QTimer.singleShot(self.DOT_PAUSE, self._reveal_dot)
            return

        self._reveal_char()

    def _reveal_char(self):

        self._visible_length += 1
        shown = self._full_text[: self._visible_length]
        self.label.setText(shown + "▌")

    def _reveal_dot(self):

        if self._fading:
            return

        self._reveal_char()
        self._typing_timer.start()

    def _start_fade(self):

        if self._fading:
            return

        self._fading = True
        self._fade.start()

    def keyPressEvent(self, event):

        self._typing_timer.stop()
        self.label.setText(self._full_text)
        self._start_fade()

    def mousePressEvent(self, event):

        self._typing_timer.stop()
        self.label.setText(self._full_text)
        self._start_fade()


# ---- janela de carregamento de jogo ----

class LoadingDialog(QDialog):

    def __init__(self, game_name, seconds=8, parent=None):

        super().__init__(parent)

        self.setWindowTitle(APP_NAME)
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setFixedSize(400, 190)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel(tr("Iniciando {name}...").format(name=game_name))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title)

        message = QLabel(
            tr("O Wine pode demorar alguns segundos para abrir o "
            "jogo. Aguarde — esta janela fecha sozinha.")
        )
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message.setWordWrap(True)
        message.setStyleSheet("color: #aaa;")
        layout.addWidget(message)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        layout.addWidget(self.progress)

        close_button = QPushButton(tr("Fechar"))
        close_button.clicked.connect(self.accept)
        layout.addWidget(
            close_button,
            alignment=Qt.AlignmentFlag.AlignCenter,
        )

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.accept)
        self.timer.start(max(1, int(seconds)) * 1000)

        wire_click_sounds(self, getattr(parent, "sound_manager", None))


# ---- diálogo de jogo ----

class SteamGridPickerSignals(QObject):
    """Ponte das threads de rede pra janela (os sinais chegam na
    thread principal)."""

    games = Signal(object)    # (token, [jogos], erro)
    images = Signal(object)   # (token, [urls], erro)
    thumb = Signal(object)    # (token, linha, bytes)


class SteamGridPickerDialog(QDialog):
    """Mostra as imagens do SteamGridDB pra você escolher qual vai
    aparecer ao lado do ícone do launcher no Discord. Dá pra trocar
    de jogo (quando a busca acha o jogo errado) e de tipo de
    imagem (ícones, quadradas, capas...)."""

    KINDS = [
        (
            "Ícones", "/icons/game/{id}",
            {"mimes": "image/png", "types": "static"},
        ),
        (
            "Quadradas", "/grids/game/{id}",
            {
                "dimensions": "512x512,1024x1024",
                "mimes": "image/png,image/jpeg",
                "types": "static",
            },
        ),
        (
            "Verticais (capas)", "/grids/game/{id}",
            {
                "dimensions": "600x900,342x482,660x930",
                "mimes": "image/png,image/jpeg",
                "types": "static",
            },
        ),
        (
            "Horizontais", "/grids/game/{id}",
            {
                "dimensions": "460x215,920x430",
                "mimes": "image/png,image/jpeg",
                "types": "static",
            },
        ),
    ]

    THUMB_SIZE = 96

    def __init__(self, api_key, game_name, current_url="", parent=None):
        super().__init__(parent)

        self.api_key = api_key
        self.current_url = current_url or ""
        # None = nada mudou; "" = voltar pro automático; senão, a URL.
        self.result_url = None

        self._token = 0
        self._pool = ThreadPoolExecutor(max_workers=6)

        self.signals = SteamGridPickerSignals(self)
        self.signals.games.connect(self._on_games)
        self.signals.images.connect(self._on_images)
        self.signals.thumb.connect(self._on_thumb)

        self.setWindowTitle(tr("Imagem do jogo no Discord (SteamGridDB)"))
        self.resize(660, 560)

        layout = QVBoxLayout(self)

        hint = QLabel(
            tr("Se o launcher estiver pegando a imagem de outro jogo, "
            "procure o nome certo e escolha o jogo na lista. Depois "
            "clique na imagem que quiser.")
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)

        search_row = QHBoxLayout()

        self.search_input = QLineEdit(game_name or "")
        self.search_input.setPlaceholderText(tr("Nome do jogo"))
        self.search_input.returnPressed.connect(self._search)

        search_button = QPushButton(tr("🔍 Buscar"))
        search_button.clicked.connect(self._search)

        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)

        selectors = QFormLayout()

        self.game_combo = QComboBox()
        self.game_combo.currentIndexChanged.connect(self._load_images)
        selectors.addRow(tr("Jogo:"), self.game_combo)

        self.kind_combo = QComboBox()
        for label, _path, _params in self.KINDS:
            self.kind_combo.addItem(tr(label))
        self.kind_combo.currentIndexChanged.connect(self._load_images)
        selectors.addRow(tr("Tipo:"), self.kind_combo)

        layout.addLayout(selectors)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.image_list = QListWidget()
        self.image_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.image_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.image_list.setMovement(QListWidget.Movement.Static)
        self.image_list.setIconSize(
            QSize(self.THUMB_SIZE, self.THUMB_SIZE)
        )
        self.image_list.setGridSize(
            QSize(self.THUMB_SIZE + 14, self.THUMB_SIZE + 14)
        )
        self.image_list.setSpacing(4)
        self.image_list.setMinimumHeight(260)
        self.image_list.itemSelectionChanged.connect(
            self._update_buttons
        )
        self.image_list.itemDoubleClicked.connect(
            lambda _item: self._use_selected()
        )
        layout.addWidget(self.image_list, 1)

        buttons = QHBoxLayout()

        auto_button = QPushButton(tr("↩ Voltar ao automático"))
        auto_button.setToolTip(
            tr("Esquece a escolha e deixa o launcher procurar sozinho.")
        )
        auto_button.clicked.connect(self._use_auto)

        cancel = QPushButton(tr("Cancelar"))
        cancel.clicked.connect(self.reject)

        self.use_button = QPushButton(tr("Usar esta imagem"))
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._use_selected)

        buttons.addWidget(auto_button)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)

        placeholder = QPixmap(self.THUMB_SIZE, self.THUMB_SIZE)
        placeholder.fill(QColor("#2a2a2a"))
        self._placeholder_icon = QIcon(placeholder)

        QTimer.singleShot(0, self._search)

    # ------------------------------------------------ utilitários

    def _emit(self, signal, payload):
        try:
            signal.emit(payload)
        except RuntimeError:
            # Janela já foi fechada/destruída.
            pass

    def _update_buttons(self):
        self.use_button.setEnabled(bool(self.image_list.selectedItems()))

    # ------------------------------------------------ busca de jogos

    def _search(self):

        term = self.search_input.text().strip()

        self._token += 1
        token = self._token

        self.image_list.clear()
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        self.game_combo.blockSignals(False)

        if not term:
            self.status_label.setText(tr("Digite o nome de um jogo."))
            return

        self.status_label.setText(tr("Buscando jogos..."))

        self._pool.submit(self._search_worker, token, term)

    def _search_worker(self, token, term):

        found = []
        error = ""

        try:
            for candidate in steamgrid_search_terms(term):
                results = steamgrid_get(
                    "/search/autocomplete/"
                    + urllib.parse.quote(candidate, safe=""),
                    self.api_key,
                )

                found = [
                    item for item in results
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), int)
                ]

                if found:
                    break

        except SteamGridAuthError:
            error = tr("O SteamGridDB recusou a chave da API.")
        except Exception as exc:
            error = tr("Falha na busca: {exc}").format(exc=exc)

        # Nome idêntico ao digitado vem primeiro.
        wanted = term.strip().lower()
        found.sort(
            key=lambda item: str(item.get("name", "")).strip().lower()
            != wanted
        )

        self._emit(self.signals.games, (token, found, error))

    def _on_games(self, payload):

        token, games, error = payload

        if token != self._token:
            return

        if error:
            self.status_label.setText(f"❌ {error}")
            return

        if not games:
            self.status_label.setText(
                tr("Nenhum jogo encontrado com esse nome.")
            )
            return

        self.game_combo.blockSignals(True)

        for item in games:
            self.game_combo.addItem(
                str(item.get("name", "?")), item["id"],
            )

        self.game_combo.setCurrentIndex(0)
        self.game_combo.blockSignals(False)

        self._load_images()

    # ------------------------------------------------ imagens

    def _load_images(self, *_args):

        game_id = self.game_combo.currentData()

        if game_id is None:
            return

        self._token += 1
        token = self._token

        self.image_list.clear()
        self.status_label.setText(tr("Carregando imagens..."))

        _label, path, params = self.KINDS[self.kind_combo.currentIndex()]

        self._pool.submit(
            self._images_worker, token, path.format(id=game_id),
            dict(params),
        )

    def _images_worker(self, token, path, params):

        urls = []
        error = ""

        try:
            for item in steamgrid_get(path, self.api_key, params):
                url = steamgrid_pick_image([item])

                if url and url not in urls:
                    urls.append(url)

        except SteamGridAuthError:
            error = tr("O SteamGridDB recusou a chave da API.")
        except Exception as exc:
            error = tr("Falha ao carregar: {exc}").format(exc=exc)

        self._emit(self.signals.images, (token, urls, error))

    def _on_images(self, payload):

        token, urls, error = payload

        if token != self._token:
            return

        if error:
            self.status_label.setText(f"❌ {error}")
            return

        if not urls:
            self.status_label.setText(
                tr("Esse jogo não tem imagens desse tipo — tente outro "
                "tipo ou outro jogo da lista.")
            )
            return

        self.status_label.setText(
            tr("{n} imagens. Clique numa pra escolher.").format(n=len(urls))
        )

        for row, url in enumerate(urls):

            item = QListWidgetItem(self._placeholder_icon, "")
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.image_list.addItem(item)

            if url == self.current_url:
                item.setSelected(True)
                self.image_list.scrollToItem(item)

            self._pool.submit(self._thumb_worker, token, row, url)

    def _thumb_worker(self, token, row, url):

        if token != self._token:
            return

        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": f"{APP_SLUG}/{LAUNCHER_VERSION}"},
            )

            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read(4 * 1024 * 1024)

        except Exception:
            return

        self._emit(self.signals.thumb, (token, row, data))

    def _on_thumb(self, payload):

        token, row, data = payload

        if token != self._token or row >= self.image_list.count():
            return

        pixmap = QPixmap()

        if pixmap.loadFromData(data):
            self.image_list.item(row).setIcon(QIcon(pixmap))

    # ------------------------------------------------ resultado

    def _use_selected(self):

        selected = self.image_list.selectedItems()

        if not selected:
            return

        self.result_url = selected[0].data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _use_auto(self):

        self.result_url = ""
        self.accept()

    def done(self, result):

        # Cancela downloads pendentes e ignora respostas atrasadas.
        self._token += 1
        self._pool.shutdown(wait=False, cancel_futures=True)

        super().done(result)


class GameDialog(QDialog):

    def __init__(self, game=None, parent=None):
        super().__init__(parent)

        self.game = game
        self.selected_image = ""
        self.sound_manager = getattr(parent, "sound_manager", None)

        self.setWindowTitle(tr("Editar jogo") if game else tr("Adicionar jogo"))
        self.setMinimumWidth(650)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText(tr("Ex: The Binding of Isaac"))
        form.addRow(tr("Nome:"), self.name_input)

        self.exe_input = QLineEdit()
        self.exe_input.setPlaceholderText(
            "/home/user/Games/Jogo/jogo.exe"
        )

        browse_exe = QPushButton(tr("Procurar..."))
        browse_exe.clicked.connect(self.browse_exe)

        exe_layout = QHBoxLayout()
        exe_layout.addWidget(self.exe_input)
        exe_layout.addWidget(browse_exe)

        form.addRow(tr("Executável:"), exe_layout)

        self.runner_combo = QComboBox()
        for runner_id in RUNNERS:
            self.runner_combo.addItem(tr(runner_id), runner_id)
        self.runner_combo.currentIndexChanged.connect(self.update_runner)
        form.addRow(tr("Runner:"), self.runner_combo)

        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText(
            tr("Opcional: /home/user/.wine")
        )
        form.addRow(tr("Wine Prefix:"), self.prefix_input)

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText(
            tr('Ex: ~/cxoffice/bin/wine "%EXE%"')
        )
        form.addRow(tr("Comando:"), self.command_input)

        self.cover_input = QLineEdit()
        self.cover_input.setPlaceholderText(
            tr("Capa do jogo (imagem grande, preenche o card)")
        )

        browse_cover = QPushButton(tr("Procurar..."))
        browse_cover.clicked.connect(self.browse_cover)

        cover_layout = QHBoxLayout()
        cover_layout.addWidget(self.cover_input)
        cover_layout.addWidget(browse_cover)

        form.addRow(tr("Capa:"), cover_layout)

        self.icon_input = QLineEdit()
        self.icon_input.setPlaceholderText(
            tr("Ícone do jogo (logo pequeno)")
        )

        browse_icon = QPushButton(tr("Procurar..."))
        browse_icon.clicked.connect(self.browse_icon)

        icon_layout = QHBoxLayout()
        icon_layout.addWidget(self.icon_input)
        icon_layout.addWidget(browse_icon)

        form.addRow(tr("Ícone:"), icon_layout)

        extract_button = QPushButton(tr("🎨 Usar ícone do executável"))
        extract_button.clicked.connect(self.extract_icon)
        form.addRow("", extract_button)

        detect_cover_button = QPushButton(
            tr("🖼 Procurar capa nos arquivos do jogo")
        )
        detect_cover_button.setToolTip(
            tr("Procura uma imagem de capa que o próprio jogo já "
            "tenha junto do executável (pasta do jogo, subpastas "
            "de arte/mídia comuns etc.).")
        )
        detect_cover_button.clicked.connect(self.detect_cover)
        form.addRow("", detect_cover_button)

        self.cover_mode_combo = QComboBox()
        for mode_id, mode_label in COVER_MODES:
            self.cover_mode_combo.addItem(tr(mode_label), mode_id)
        self.cover_mode_combo.setToolTip(
            tr("Preencher: a capa cobre o card inteiro e as bordas que "
            "sobrarem são cortadas.\n"
            "Esticar: a capa aparece inteira, esticada pro tamanho "
            "do card (pode ficar distorcida).")
        )
        self.cover_mode_combo.currentIndexChanged.connect(
            self.update_preview
        )
        form.addRow(tr("Modo da capa:"), self.cover_mode_combo)

        self.show_image_checkbox = QCheckBox(
            tr("Mostrar capa/ícone no card")
        )
        self.show_image_checkbox.setChecked(True)
        self.show_image_checkbox.toggled.connect(self.update_preview)
        form.addRow("", self.show_image_checkbox)

        self.show_badge_checkbox = QCheckBox(
            tr("Quando os dois existirem, mostrar o ícone como um "
            "selo sobre a capa")
        )
        self.show_badge_checkbox.setChecked(True)
        self.show_badge_checkbox.toggled.connect(self.update_preview)
        form.addRow("", self.show_badge_checkbox)

        self.presence_image = ""

        self.presence_status = QLabel("")
        self.presence_status.setStyleSheet("color: #888;")

        presence_button = QPushButton(tr("Escolher imagem..."))
        presence_button.setToolTip(
            tr("Escolhe qual imagem do SteamGridDB aparece ao lado do "
            "ícone do launcher no Discord.")
        )
        presence_button.clicked.connect(self.choose_presence_image)

        presence_row = QHBoxLayout()
        presence_row.addWidget(self.presence_status, 1)
        presence_row.addWidget(presence_button)

        form.addRow(tr("Imagem no Discord:"), presence_row)
        self.update_presence_status()

        layout.addLayout(form)

        previews_layout = QHBoxLayout()

        cover_preview_box = QVBoxLayout()
        cover_preview_label = QLabel(tr("Capa"))
        cover_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_preview_label.setStyleSheet("color: #888; font-size: 11px;")
        self.cover_preview = QLabel()
        # Mesma proporção do card (270x360) pra o preview mostrar
        # fielmente como a capa vai ficar em cada modo.
        self.cover_preview.setFixedSize(120, 160)
        self.cover_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_preview.setStyleSheet("""
            QLabel {
                border: 1px solid #555;
                border-radius: 12px;
                background: #181818;
            }
        """)
        cover_preview_box.addWidget(cover_preview_label)
        cover_preview_box.addWidget(self.cover_preview)

        icon_preview_box = QVBoxLayout()
        icon_preview_label = QLabel(tr("Ícone"))
        icon_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_preview_label.setStyleSheet("color: #888; font-size: 11px;")
        self.icon_preview = QLabel()
        self.icon_preview.setFixedSize(90, 90)
        self.icon_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_preview.setStyleSheet("""
            QLabel {
                border: 1px solid #555;
                border-radius: 12px;
                background: #181818;
            }
        """)
        icon_preview_box.addWidget(icon_preview_label)
        icon_preview_box.addWidget(self.icon_preview)

        previews_layout.addStretch()
        previews_layout.addLayout(cover_preview_box)
        previews_layout.addLayout(icon_preview_box)
        previews_layout.addStretch()

        layout.addLayout(previews_layout)

        buttons = QHBoxLayout()

        cancel = QPushButton(tr("Cancelar"))
        cancel.clicked.connect(self.reject)

        save = QPushButton(tr("Salvar") if game else tr("Adicionar"))
        save.clicked.connect(self.validate)

        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

        if game:
            self.name_input.setText(game.get("name", ""))
            self.presence_image = game.get("presence_image", "") or ""
            self.update_presence_status()
            self.exe_input.setText(game.get("exe", ""))
            self.prefix_input.setText(game.get("prefix", ""))
            self.command_input.setText(game.get("command", ""))
            self.cover_input.setText(
                game.get("cover", game.get("image", ""))
            )
            self.icon_input.setText(game.get("icon", ""))
            self.show_image_checkbox.setChecked(
                game.get("show_image", True)
            )
            self.show_badge_checkbox.setChecked(
                game.get("show_icon_badge", True)
            )
            mode_index = self.cover_mode_combo.findData(
                game.get("cover_mode", DEFAULT_COVER_MODE)
            )
            if mode_index >= 0:
                self.cover_mode_combo.setCurrentIndex(mode_index)

            runner = game.get("runner", "Wine")
            index = self.runner_combo.findData(runner)
            if index >= 0:
                self.runner_combo.setCurrentIndex(index)

            self.update_preview()

        self.update_runner()

        wire_click_sounds(self, self.sound_manager)

    def browse_exe(self):

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Escolher executável"), str(Path.home()),
            tr("Executáveis (*.exe);;Todos os arquivos (*)"),
        )

        if path:
            self.exe_input.setText(path)
            if not self.icon_input.text():
                self.extract_icon(silent=True)
            if not self.cover_input.text():
                self.detect_cover(silent=True)

    def detect_cover(self, silent=False):
        """Procura (via coverscan, se compilado) uma capa que o
        próprio jogo já tenha solta nos arquivos dele. silent=True
        é usado ao escolher o exe (não incomoda com mensagens se
        não achar nada); o botão manual sempre avisa o resultado."""

        exe = os.path.expanduser(self.exe_input.text().strip())

        if not exe or not os.path.isfile(exe):
            if not silent:
                QMessageBox.warning(
                    self, APP_NAME,
                    tr("Escolha o executável do jogo primeiro."),
                )
            return

        ensure_coverscan_binary()

        candidates = detect_cover_candidates(exe)

        if not candidates:
            if not silent:
                QMessageBox.information(
                    self, APP_NAME,
                    tr("Não encontrei nenhuma capa nos arquivos do "
                    "jogo. Escolha uma manualmente em \"Capa\"."),
                )
            return

        best = candidates[0]
        self.cover_input.setText(best["path"])
        self.update_preview()

        if not silent:
            QMessageBox.information(
                self, APP_NAME,
                tr("Encontrei e usei:\n\n{path}").format(path=best["path"]),
            )

    def browse_cover(self):

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Escolher capa"), str(Path.home()),
            tr("Imagens (*.png *.jpg *.jpeg *.webp *.ico)"),
        )

        if path:
            self.cover_input.setText(path)
            self.update_preview()

    def browse_icon(self):

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Escolher ícone"), str(Path.home()),
            tr("Imagens (*.png *.jpg *.jpeg *.webp *.ico)"),
        )

        if path:
            self.icon_input.setText(path)
            self.update_preview()

    def extract_icon(self, silent=False):

        exe = os.path.expanduser(self.exe_input.text().strip())

        if not exe:
            if not silent:
                QMessageBox.warning(
                    self, APP_NAME, tr("Escolha um executável primeiro."),
                )
            return

        if not os.path.isfile(exe):
            if not silent:
                QMessageBox.warning(
                    self, APP_NAME,
                    tr("O executável não foi encontrado."),
                )
            return

        safe_name = str(abs(hash(exe)))
        output = IMAGE_DIR / f"{safe_name}.ico"

        if extract_exe_icon(exe, str(output)):
            self.icon_input.setText(str(output))
            self.update_preview()
            if not silent:
                QMessageBox.information(
                    self, APP_NAME, tr("Ícone extraído com sucesso!"),
                )
        elif not silent:
            QMessageBox.warning(
                self, APP_NAME,
                tr("Não consegui extrair o ícone desse executável.\n\n"
                "Você pode escolher uma imagem manualmente."),
            )

    def update_runner(self):

        runner = self.runner_combo.currentData()
        self.prefix_input.setEnabled(runner == "Wine")
        self.command_input.setEnabled(
            runner == "Comando personalizado"
        )

    def update_presence_status(self):

        if self.presence_image:
            self.presence_status.setText(tr("✅ Imagem escolhida por você"))
            self.presence_status.setToolTip(self.presence_image)
        else:
            self.presence_status.setText(
                tr("Automática (o launcher procura sozinho)")
            )
            self.presence_status.setToolTip("")

    def choose_presence_image(self):

        parent = self.parent()
        api_key = ""

        if parent is not None and hasattr(parent, "steamgrid_key"):
            api_key = parent.steamgrid_key()

        if not api_key:
            QMessageBox.information(
                self, APP_NAME,
                tr("Cole a chave do SteamGridDB em "
                "Configurações → Integrações primeiro."),
            )
            return

        dialog = SteamGridPickerDialog(
            api_key, self.name_input.text().strip(),
            self.presence_image, self,
        )

        if dialog.exec() and dialog.result_url is not None:
            self.presence_image = dialog.result_url
            self.update_presence_status()

    def update_preview(self):

        show = self.show_image_checkbox.isChecked()

        cover_path = os.path.expanduser(
            self.cover_input.text().strip()
        )
        icon_path = os.path.expanduser(
            self.icon_input.text().strip()
        )

        if not show or not cover_path or not os.path.exists(cover_path):
            self.cover_preview.clear()
        else:
            cover_pixmap = load_cover_pixmap(
                cover_path, self.cover_preview.size(),
                self.cover_mode_combo.currentData(),
            )
            if cover_pixmap is not None:
                self.cover_preview.setPixmap(cover_pixmap)
            else:
                self.cover_preview.clear()

        if not show or not icon_path or not os.path.exists(icon_path):
            self.icon_preview.clear()
        else:
            icon_pixmap = load_icon_pixmap(
                icon_path, self.icon_preview.size(),
            )
            if icon_pixmap is not None:
                self.icon_preview.setPixmap(icon_pixmap)
            else:
                self.icon_preview.clear()

    def validate(self):

        name = self.name_input.text().strip()
        exe = self.exe_input.text().strip()
        runner = self.runner_combo.currentData()
        command = self.command_input.text().strip()

        if not name:
            QMessageBox.warning(self, APP_NAME, tr("Digite o nome do jogo."))
            return

        if not exe:
            QMessageBox.warning(self, APP_NAME, tr("Escolha o executável."))
            return

        if not os.path.isfile(os.path.expanduser(exe)):
            answer = QMessageBox.question(
                self, APP_NAME,
                tr("O executável não existe.\n\n"
                "Deseja salvar mesmo assim?"),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if runner == "Comando personalizado" and not command:
            QMessageBox.warning(
                self, APP_NAME, tr("Digite o comando personalizado."),
            )
            return

        self.accept()

    def get_data(self):

        return {
            "name": self.name_input.text().strip(),
            "exe": os.path.expanduser(self.exe_input.text().strip()),
            "runner": self.runner_combo.currentData(),
            "prefix": os.path.expanduser(
                self.prefix_input.text().strip()
            ),
            "command": self.command_input.text().strip(),
            # Copia a capa pra pasta capas/ e salva o caminho da cópia.
            "cover": import_cover(
                os.path.expanduser(self.cover_input.text().strip())
            ),
            "icon": os.path.expanduser(
                self.icon_input.text().strip()
            ),
            "show_image": self.show_image_checkbox.isChecked(),
            "show_icon_badge": self.show_badge_checkbox.isChecked(),
            "cover_mode": (
                self.cover_mode_combo.currentData() or DEFAULT_COVER_MODE
            ),
            "presence_image": self.presence_image,
        }


# ---- configurações ----

class SettingsDialog(QDialog):

    def __init__(self, settings, parent=None):

        super().__init__(parent)

        self.settings = settings.copy()

        self.sound_manager = getattr(parent, "sound_manager", None)
        self._test_player = None
        self._test_audio_output = None

        self.setWindowTitle(tr("Configurações"))
        self.setMinimumWidth(560)
        self.setMinimumHeight(540)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---- aba 0 — geral ----

        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)
        general_form = QFormLayout()

        self.view_mode_combo = QComboBox()
        self.view_mode_combo.addItem(tr("▦ Grade"), "grade")
        self.view_mode_combo.addItem(tr("🎠 Fileira (carrossel infinito)"), "fileira")

        current_view_mode = self.settings.get("view_mode", "grade")
        index = self.view_mode_combo.findData(current_view_mode)
        self.view_mode_combo.setCurrentIndex(max(0, index))

        general_form.addRow(
            tr("Modo de exibição dos jogos:"), self.view_mode_combo
        )

        view_mode_help = QLabel(
            tr("No modo grade os jogos ficam organizados em colunas. "
            "No modo fileira os jogos formam um carrossel infinito: "
            "o do meio fica em destaque e os vizinhos ficam menores. "
            "Navegue com o scroll do mouse, as setas do teclado ou "
            "clicando num card do lado. (Reordenar arrastando só "
            "funciona no modo grade.)")
        )
        view_mode_help.setWordWrap(True)
        view_mode_help.setStyleSheet("color: #888; font-size: 11px;")
        general_form.addRow(view_mode_help)

        self.language_combo = QComboBox()
        self.language_combo.addItem(tr("Automático (idioma do sistema)"), "auto")
        self.language_combo.addItem("Português (Brasil)", "pt")
        self.language_combo.addItem("English", "en")
        language_index = self.language_combo.findData(
            self.settings.get("language", "auto")
        )
        self.language_combo.setCurrentIndex(max(0, language_index))
        general_form.addRow(tr("Idioma:"), self.language_combo)

        language_help = QLabel(
            tr("A mudança de idioma vale depois de reabrir o launcher.")
        )
        language_help.setWordWrap(True)
        language_help.setStyleSheet("color: #888; font-size: 11px;")
        general_form.addRow(language_help)

        general_layout.addLayout(general_form)

        theme_separator = QFrame()
        theme_separator.setFrameShape(QFrame.Shape.HLine)
        general_layout.addWidget(theme_separator)

        theme_label = QLabel(tr("Tema"))
        theme_label.setStyleSheet(
            "font-size: 14px; font-weight: bold;"
        )
        general_layout.addWidget(theme_label)

        theme_help = QLabel(
            tr("Exporte as cores, opacidades e imagem de fundo atuais "
            "num arquivo pra reaproveitar em outro lugar, ou "
            "importe um tema salvo anteriormente.")
        )
        theme_help.setWordWrap(True)
        theme_help.setStyleSheet("color: #888; font-size: 11px;")
        general_layout.addWidget(theme_help)

        theme_buttons = QHBoxLayout()

        export_theme_button = QPushButton(tr("⬆ Exportar tema..."))
        export_theme_button.clicked.connect(self.export_theme)
        theme_buttons.addWidget(export_theme_button)

        import_theme_button = QPushButton(tr("⬇ Importar tema..."))
        import_theme_button.clicked.connect(self.import_theme)
        theme_buttons.addWidget(import_theme_button)

        theme_buttons.addStretch()
        general_layout.addLayout(theme_buttons)

        update_separator = QFrame()
        update_separator.setFrameShape(QFrame.Shape.HLine)
        general_layout.addWidget(update_separator)

        update_label = QLabel(tr("Atualização do launcher"))
        update_label.setStyleSheet(
            "font-size: 14px; font-weight: bold;"
        )
        general_layout.addWidget(update_label)

        self.update_version_label = QLabel(
            tr("Versão instalada: {version}").format(
                version=LAUNCHER_VERSION
            )
        )
        self.update_version_label.setStyleSheet(
            "color: #888; font-size: 11px;"
        )
        general_layout.addWidget(self.update_version_label)

        update_help = QLabel(
            tr("Selecione um novo arquivo launcher.py pra atualizar. "
            "Só é aplicado se a versão dele for maior que a "
            "instalada atualmente.")
        )
        update_help.setWordWrap(True)
        update_help.setStyleSheet("color: #888; font-size: 11px;")
        general_layout.addWidget(update_help)

        update_button = QPushButton(tr("🔄 Atualizar launcher..."))
        update_button.clicked.connect(self.update_launcher_file)

        update_buttons = QHBoxLayout()
        update_buttons.addWidget(update_button)
        update_buttons.addStretch()
        general_layout.addLayout(update_buttons)

        general_layout.addStretch()

        tabs.addTab(general_tab, tr("🖥 Geral"))

        # ---- aba 1 — aparência ----

        appearance_tab = QWidget()
        appearance_layout = QVBoxLayout(appearance_tab)
        appearance_form = QFormLayout()

        self.accent_button = QPushButton()
        self.accent_button.clicked.connect(self.choose_color)
        self.update_color_button()
        appearance_form.addRow(tr("Cor de destaque:"), self.accent_button)

        self.background_input = QLineEdit()
        self.background_input.setText(
            self.settings.get("background", "")
        )

        browse_background = QPushButton(tr("Procurar..."))
        browse_background.clicked.connect(self.choose_background)

        bg_layout = QHBoxLayout()
        bg_layout.addWidget(self.background_input)
        bg_layout.addWidget(browse_background)

        appearance_form.addRow(tr("Imagem de fundo:"), bg_layout)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(
            int(self.settings.get("background_opacity", 35))
        )

        self.opacity_label = QLabel()
        self.update_opacity_label()
        self.opacity_slider.valueChanged.connect(
            self.update_opacity_label
        )

        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(self.opacity_slider)
        opacity_layout.addWidget(self.opacity_label)

        appearance_form.addRow(tr("Opacidade:"), opacity_layout)

        appearance_layout.addLayout(appearance_form)
        appearance_layout.addStretch()

        tabs.addTab(appearance_tab, tr("🎨 Aparência"))

        # ---- aba 2 — cards ----

        cards_tab = QWidget()
        cards_layout = QVBoxLayout(cards_tab)
        cards_form = QFormLayout()

        self.card_color_button = QPushButton()
        self.card_color_button.clicked.connect(self.choose_card_color)
        self.update_card_color_button()
        cards_form.addRow(tr("Cor dos cards:"), self.card_color_button)

        self.card_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.card_opacity_slider.setRange(0, 100)
        self.card_opacity_slider.setValue(
            int(
                self.settings.get(
                    "card_opacity",
                    DEFAULT_SETTINGS["card_opacity"],
                )
            )
        )

        self.card_opacity_label = QLabel()
        self.update_card_opacity_label()
        self.card_opacity_slider.valueChanged.connect(
            self.update_card_opacity_label
        )

        card_opacity_layout = QHBoxLayout()
        card_opacity_layout.addWidget(self.card_opacity_slider)
        card_opacity_layout.addWidget(self.card_opacity_label)

        cards_form.addRow(
            tr("Opacidade dos cards:"), card_opacity_layout
        )

        self.border_color_button = QPushButton()
        self.border_color_button.clicked.connect(
            self.choose_border_color
        )
        self.update_border_color_button()
        cards_form.addRow(tr("Cor da borda:"), self.border_color_button)

        self.border_width_spin = QSpinBox()
        self.border_width_spin.setRange(0, 30)
        self.border_width_spin.setValue(
            int(
                self.settings.get(
                    "border_width",
                    DEFAULT_SETTINGS["border_width"],
                )
            )
        )
        cards_form.addRow(
            tr("Espessura da borda:"), self.border_width_spin
        )

        self.border_radius_spin = QSpinBox()
        self.border_radius_spin.setRange(0, 40)
        self.border_radius_spin.setValue(
            int(
                self.settings.get(
                    "border_radius",
                    DEFAULT_SETTINGS["border_radius"],
                )
            )
        )
        cards_form.addRow(
            tr("Arredondamento da borda:"), self.border_radius_spin
        )

        cards_layout.addLayout(cards_form)

        reorder_separator = QFrame()
        reorder_separator.setFrameShape(QFrame.Shape.HLine)
        cards_layout.addWidget(reorder_separator)

        self.reorder_mode_checkbox = QCheckBox(
            tr("✥ Reordenar jogos arrastando os cards")
        )
        self.reorder_mode_checkbox.setToolTip(
            tr("Enquanto estiver ligado, clicar e arrastar um card "
            "move ele pra outra posição na lista (só muda a "
            "ordem — os botões do card ficam desativados até "
            "desligar de novo).")
        )
        self.reorder_mode_checkbox.setChecked(
            bool(
                self.settings.get(
                    "reorder_mode_enabled",
                    DEFAULT_SETTINGS["reorder_mode_enabled"],
                )
            )
        )
        cards_layout.addWidget(self.reorder_mode_checkbox)

        cards_layout.addStretch()

        tabs.addTab(cards_tab, tr("🃏 Cards"))

        # ---- aba 3 — botões ----

        buttons_tab = QWidget()
        buttons_layout = QVBoxLayout(buttons_tab)
        buttons_form = QFormLayout()

        self.button_color_button = QPushButton()
        self.button_color_button.clicked.connect(
            lambda: self.choose_setting_color(
                "button_color",
                tr("Escolher cor dos botões"),
                self.button_color_button,
            )
        )
        self.update_setting_color_button(
            "button_color", self.button_color_button
        )
        buttons_form.addRow(
            tr("Cor dos botões:"), self.button_color_button
        )

        self.button_opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.button_opacity_slider.setRange(0, 100)
        self.button_opacity_slider.setValue(
            int(
                self.settings.get(
                    "button_opacity",
                    DEFAULT_SETTINGS["button_opacity"],
                )
            )
        )

        self.button_opacity_label = QLabel()
        self.update_button_opacity_label()
        self.button_opacity_slider.valueChanged.connect(
            self.update_button_opacity_label
        )

        button_opacity_layout = QHBoxLayout()
        button_opacity_layout.addWidget(self.button_opacity_slider)
        button_opacity_layout.addWidget(self.button_opacity_label)

        buttons_form.addRow(
            tr("Opacidade dos botões:"), button_opacity_layout
        )

        self.button_text_color_button = QPushButton()
        self.button_text_color_button.clicked.connect(
            lambda: self.choose_setting_color(
                "button_text_color",
                tr("Escolher cor do texto dos botões"),
                self.button_text_color_button,
            )
        )
        self.update_setting_color_button(
            "button_text_color", self.button_text_color_button
        )
        buttons_form.addRow(
            tr("Cor do texto:"), self.button_text_color_button
        )

        self.button_border_color_button = QPushButton()
        self.button_border_color_button.clicked.connect(
            lambda: self.choose_setting_color(
                "button_border_color",
                tr("Escolher cor da borda dos botões"),
                self.button_border_color_button,
            )
        )
        self.update_setting_color_button(
            "button_border_color", self.button_border_color_button
        )
        buttons_form.addRow(
            tr("Cor da borda:"), self.button_border_color_button
        )

        self.button_border_width_spin = QSpinBox()
        self.button_border_width_spin.setRange(0, 20)
        self.button_border_width_spin.setValue(
            int(
                self.settings.get(
                    "button_border_width",
                    DEFAULT_SETTINGS["button_border_width"],
                )
            )
        )
        buttons_form.addRow(
            tr("Espessura da borda:"), self.button_border_width_spin
        )

        self.button_border_radius_spin = QSpinBox()
        self.button_border_radius_spin.setRange(0, 40)
        self.button_border_radius_spin.setValue(
            int(
                self.settings.get(
                    "button_border_radius",
                    DEFAULT_SETTINGS["button_border_radius"],
                )
            )
        )
        buttons_form.addRow(
            tr("Arredondamento:"), self.button_border_radius_spin
        )

        self.button_hover_checkbox = QCheckBox(
            tr("Usar a cor de destaque")
        )
        self.button_hover_checkbox.setChecked(
            not self.settings.get("button_hover_color", "")
        )
        self.button_hover_checkbox.toggled.connect(
            self.update_hover_controls
        )

        self.button_hover_color_button = QPushButton()
        self.button_hover_color_button.clicked.connect(
            lambda: self.choose_setting_color(
                "button_hover_color",
                tr("Escolher cor do hover"),
                self.button_hover_color_button,
            )
        )

        hover_layout = QHBoxLayout()
        hover_layout.addWidget(self.button_hover_checkbox)
        hover_layout.addWidget(self.button_hover_color_button)

        self.update_hover_controls()

        buttons_form.addRow(
            tr("Cor ao passar o mouse:"), hover_layout
        )

        buttons_layout.addLayout(buttons_form)
        buttons_layout.addStretch()

        tabs.addTab(buttons_tab, tr("🔘 Botões"))

        # ---- aba 4 — execução ----

        execution_tab = QWidget()
        execution_layout = QVBoxLayout(execution_tab)
        execution_form = QFormLayout()

        self.loading_enabled_checkbox = QCheckBox(
            tr("Mostrar janela de 'Carregando...' ao iniciar um jogo")
        )
        self.loading_enabled_checkbox.setChecked(
            bool(
                self.settings.get(
                    "loading_window_enabled",
                    DEFAULT_SETTINGS["loading_window_enabled"],
                )
            )
        )
        execution_form.addRow(self.loading_enabled_checkbox)

        self.loading_seconds_spin = QSpinBox()
        self.loading_seconds_spin.setRange(1, 60)
        self.loading_seconds_spin.setSuffix(" segundos")
        self.loading_seconds_spin.setValue(
            int(
                self.settings.get(
                    "loading_window_seconds",
                    DEFAULT_SETTINGS["loading_window_seconds"],
                )
            )
        )
        execution_form.addRow(
            tr("Fechar automaticamente após:"),
            self.loading_seconds_spin,
        )

        loading_help = QLabel(
            tr("A janela não é modal: você continua podendo usar o "
            "launcher enquanto o jogo abre. Ela fecha sozinha no "
            "tempo configurado acima.")
        )
        loading_help.setWordWrap(True)
        loading_help.setStyleSheet("color: #888; font-size: 11px;")
        execution_form.addRow(loading_help)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        execution_form.addRow(separator)

        self.wine_debug_checkbox = QCheckBox(
            tr("Abrir o Wine dentro de um terminal (modo debug)")
        )
        self.wine_debug_checkbox.setChecked(
            bool(
                self.settings.get(
                    "wine_debug_enabled",
                    DEFAULT_SETTINGS["wine_debug_enabled"],
                )
            )
        )
        execution_form.addRow(self.wine_debug_checkbox)

        wine_help = QLabel(
            tr("Com o modo debug ligado, jogos que usam Wine são "
            "abertos dentro de uma janela de terminal, onde dá pra "
            "acompanhar as mensagens do Wine em tempo real. Útil "
            "pra descobrir por que um jogo não abre. "
            "Requer xterm, gnome-terminal, konsole, xfce4-terminal "
            "ou mate-terminal instalado.")
        )
        wine_help.setWordWrap(True)
        wine_help.setStyleSheet("color: #888; font-size: 11px;")
        execution_form.addRow(wine_help)

        execution_layout.addLayout(execution_form)
        execution_layout.addStretch()

        tabs.addTab(execution_tab, tr("🚀 Execução"))

        # ---- aba 5 — integrações ----

        integrations_tab = QWidget()
        integrations_layout = QVBoxLayout(integrations_tab)
        integrations_form = QFormLayout()

        self.discord_enabled_checkbox = QCheckBox(
            tr("Ativar Rich Presence do Discord")
        )
        self.discord_enabled_checkbox.setChecked(
            bool(
                self.settings.get(
                    "discord_rpc_enabled",
                    DEFAULT_SETTINGS["discord_rpc_enabled"],
                )
            )
        )
        integrations_form.addRow(self.discord_enabled_checkbox)

        self.steamgrid_key_input = QLineEdit()
        self.steamgrid_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.steamgrid_key_input.setPlaceholderText(
            tr("Cole aqui a sua chave da API do SteamGridDB")
        )
        self.steamgrid_key_input.setText(
            str(self.settings.get("steamgriddb_api_key", "") or "")
        )

        show_key_checkbox = QCheckBox(tr("Mostrar"))
        show_key_checkbox.toggled.connect(
            lambda visible: self.steamgrid_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal
                if visible
                else QLineEdit.EchoMode.Password
            )
        )

        key_row = QHBoxLayout()
        key_row.addWidget(self.steamgrid_key_input, 1)
        key_row.addWidget(show_key_checkbox)

        integrations_form.addRow(tr("Chave do SteamGridDB:"), key_row)

        self.steamgrid_test_input = QLineEdit()
        self.steamgrid_test_input.setPlaceholderText(
            tr("Nome de um jogo pra testar (vazio = Hades)")
        )

        test_button = QPushButton(tr("🔍 Testar"))
        test_button.clicked.connect(self.test_steamgrid)

        test_row = QHBoxLayout()
        test_row.addWidget(self.steamgrid_test_input, 1)
        test_row.addWidget(test_button)

        integrations_form.addRow(tr("Testar chave:"), test_row)

        integrations_layout.addLayout(integrations_form)

        self.steamgrid_test_result = QLabel("")
        self.steamgrid_test_result.setWordWrap(True)
        self.steamgrid_test_result.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        integrations_layout.addWidget(self.steamgrid_test_result)

        steamgrid_help = QLabel(
            tr(
                "Com a chave preenchida, o Rich Presence mostra a imagem "
                "do jogo ao lado do ícone do launcher (small_image). "
                "Cada pessoa precisa da própria chave: crie a sua em "
                '<a href="{url}">steamgriddb.com → '
                "Preferências → API</a>. Ela fica salva só neste "
                "computador, junto das suas configurações."
            ).format(url=STEAMGRIDDB_KEY_URL)
        )
        steamgrid_help.setWordWrap(True)
        steamgrid_help.setTextFormat(Qt.TextFormat.RichText)
        steamgrid_help.setOpenExternalLinks(True)
        steamgrid_help.setStyleSheet("color: #888; font-size: 11px;")

        integrations_layout.addWidget(steamgrid_help)

        discord_help = QLabel(
            tr("É necessário ter o Discord Desktop aberto (o "
            "site/navegador não conta) para o Rich Presence "
            "aparecer no seu perfil.")
        )
        discord_help.setWordWrap(True)
        discord_help.setStyleSheet("color: #888; font-size: 11px;")

        integrations_layout.addWidget(discord_help)
        integrations_layout.addStretch()

        tabs.addTab(integrations_tab, tr("🔌 Integrações"))

        # ---- aba 6 — sons ----

        sounds_tab = QWidget()
        sounds_layout = QVBoxLayout(sounds_tab)

        self.sound_enabled_checkbox = QCheckBox(
            tr("Ativar sons do launcher")
        )
        self.sound_enabled_checkbox.setChecked(
            bool(
                self.settings.get(
                    "sound_enabled", DEFAULT_SETTINGS["sound_enabled"],
                )
            )
        )
        sounds_layout.addWidget(self.sound_enabled_checkbox)

        global_volume_layout = QHBoxLayout()
        global_volume_layout.addWidget(QLabel(tr("Volume geral:")))

        self.sound_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.sound_volume_slider.setRange(0, 100)
        self.sound_volume_slider.setValue(
            int(
                self.settings.get(
                    "sound_volume", DEFAULT_SETTINGS["sound_volume"],
                )
            )
        )

        self.sound_volume_label = QLabel(
            f"{self.sound_volume_slider.value()}%"
        )
        self.sound_volume_slider.valueChanged.connect(
            lambda value: self.sound_volume_label.setText(f"{value}%")
        )

        global_volume_layout.addWidget(self.sound_volume_slider)
        global_volume_layout.addWidget(self.sound_volume_label)
        sounds_layout.addLayout(global_volume_layout)

        if not SOUND_BACKEND_AVAILABLE:
            backend_warning = QLabel(
                tr("⚠ O pacote QtMultimedia não foi encontrado nesta "
                "instalação do PySide6. Os sons ficam desativados "
                "até ele ser instalado, mas o resto do launcher "
                "funciona normalmente.")
            )
            backend_warning.setWordWrap(True)
            backend_warning.setStyleSheet(
                "color: #e0a030; font-size: 11px;"
            )
            sounds_layout.addWidget(backend_warning)

        sounds_separator = QFrame()
        sounds_separator.setFrameShape(QFrame.Shape.HLine)
        sounds_layout.addWidget(sounds_separator)

        sounds_help = QLabel(
            tr("Cada evento toca, por padrão, o arquivo com o mesmo "
            "nome dentro da pasta 'sons' (ex: sons/click.mp3). "
            "Escolha um arquivo personalizado se quiser trocar, ou "
            "clique em 'Padrão' pra voltar a usar o som original.")
        )
        sounds_help.setWordWrap(True)
        sounds_help.setStyleSheet("color: #888; font-size: 11px;")
        sounds_layout.addWidget(sounds_help)

        sounds_scroll = QScrollArea()
        sounds_scroll.setWidgetResizable(True)
        sounds_scroll.setFrameShape(QFrame.Shape.NoFrame)

        sounds_events_widget = QWidget()
        sounds_events_layout = QVBoxLayout(sounds_events_widget)

        self.sound_event_widgets = {}

        for event_id, event_label in SOUND_EVENTS:

            event_config = self.settings.get("sounds", {}).get(
                event_id, {"enabled": True, "path": "", "volume": 100},
            )

            event_box = QFrame()
            event_box.setFrameShape(QFrame.Shape.StyledPanel)
            event_box_layout = QVBoxLayout(event_box)

            top_row = QHBoxLayout()

            event_checkbox = QCheckBox(tr(event_label))
            event_checkbox.setChecked(
                bool(event_config.get("enabled", True))
            )
            top_row.addWidget(event_checkbox)
            top_row.addStretch()

            test_button = QPushButton(tr("▶ Testar"))
            top_row.addWidget(test_button)

            event_box_layout.addLayout(top_row)

            path_row = QHBoxLayout()

            custom_path = event_config.get("path", "")
            path_label = QLabel(
                custom_path
                or tr("(padrão: sons/{id}.mp3)").format(id=event_id)
            )
            path_label.setStyleSheet("color: #888; font-size: 11px;")
            path_label.setWordWrap(True)

            choose_button = QPushButton(tr("Escolher..."))
            default_button = QPushButton(tr("Padrão"))

            path_row.addWidget(path_label, 1)
            path_row.addWidget(choose_button)
            path_row.addWidget(default_button)

            event_box_layout.addLayout(path_row)

            volume_row = QHBoxLayout()
            volume_row.addWidget(QLabel(tr("Volume:")))

            volume_slider = QSlider(Qt.Orientation.Horizontal)
            volume_slider.setRange(0, 100)
            volume_slider.setValue(int(event_config.get("volume", 100)))

            volume_label = QLabel(f"{volume_slider.value()}%")
            volume_slider.valueChanged.connect(
                lambda value, label=volume_label: label.setText(
                    f"{value}%"
                )
            )

            volume_row.addWidget(volume_slider)
            volume_row.addWidget(volume_label)

            event_box_layout.addLayout(volume_row)

            sounds_events_layout.addWidget(event_box)

            self.sound_event_widgets[event_id] = {
                "enabled": event_checkbox,
                "path": custom_path,
                "path_label": path_label,
                "volume": volume_slider,
            }

            choose_button.clicked.connect(
                lambda _checked=False, eid=event_id:
                    self.choose_sound_file(eid)
            )
            default_button.clicked.connect(
                lambda _checked=False, eid=event_id:
                    self.reset_sound_file(eid)
            )
            test_button.clicked.connect(
                lambda _checked=False, eid=event_id:
                    self.test_sound(eid)
            )

        sounds_events_layout.addStretch()
        sounds_scroll.setWidget(sounds_events_widget)
        sounds_layout.addWidget(sounds_scroll)

        tabs.addTab(sounds_tab, tr("🔊 Sons"))

        layout.addWidget(tabs)

        bottom = QHBoxLayout()

        cancel = QPushButton(tr("Cancelar"))
        cancel.clicked.connect(self.reject)

        save = QPushButton(tr("Aplicar"))
        save.clicked.connect(self.accept)

        bottom.addStretch()
        bottom.addWidget(cancel)
        bottom.addWidget(save)

        layout.addLayout(bottom)

        wire_click_sounds(self, self.sound_manager, exempt={save})

    def choose_color(self):

        current = QColor(self.settings.get("accent", "#8b5cf6"))
        color = QColorDialog.getColor(current, self, tr("Escolher cor"))

        if color.isValid():
            self.settings["accent"] = color.name()
            self.update_color_button()

    def update_color_button(self):

        color = self.settings.get("accent", "#8b5cf6")
        self.accent_button.setText(color)
        self.accent_button.setStyleSheet(
            f"QPushButton {{ background: {color}; "
            f"color: white; padding: 8px; }}"
        )

    def choose_background(self):

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Escolher imagem de fundo"), str(Path.home()),
            tr("Imagens (*.png *.jpg *.jpeg *.webp)"),
        )

        if path:
            self.background_input.setText(path)

    def update_opacity_label(self):

        self.opacity_label.setText(
            f"{self.opacity_slider.value()}%"
        )

    def choose_card_color(self):

        current = QColor(
            self.settings.get(
                "card_color", DEFAULT_SETTINGS["card_color"],
            )
        )
        color = QColorDialog.getColor(
            current, self, tr("Escolher cor dos cards"),
        )

        if color.isValid():
            self.settings["card_color"] = color.name()
            self.update_card_color_button()

    def update_card_color_button(self):

        color = self.settings.get(
            "card_color", DEFAULT_SETTINGS["card_color"],
        )
        self.card_color_button.setText(color)
        self.card_color_button.setStyleSheet(
            f"QPushButton {{ background: {color}; "
            f"color: white; padding: 8px; }}"
        )

    def update_card_opacity_label(self):

        self.card_opacity_label.setText(
            f"{self.card_opacity_slider.value()}%"
        )

    def choose_setting_color(self, key, title, button):

        current = QColor(
            self.settings.get(key, "")
            or DEFAULT_SETTINGS.get(key, "#000000")
            or "#000000"
        )
        color = QColorDialog.getColor(current, self, title)

        if color.isValid():
            self.settings[key] = color.name()
            self.update_setting_color_button(key, button)

    def update_setting_color_button(self, key, button):

        color = self.settings.get(key, "") or DEFAULT_SETTINGS.get(
            key, "",
        )

        if not color:
            color = self.settings.get(
                "accent", DEFAULT_SETTINGS["accent"],
            )

        button.setText(color)
        button.setStyleSheet(
            f"QPushButton {{ background: {color}; "
            f"color: white; padding: 8px; }}"
        )

    def update_button_opacity_label(self):

        self.button_opacity_label.setText(
            f"{self.button_opacity_slider.value()}%"
        )

    def update_hover_controls(self):

        using_accent = self.button_hover_checkbox.isChecked()
        self.button_hover_color_button.setEnabled(not using_accent)
        self.update_setting_color_button(
            "button_hover_color", self.button_hover_color_button,
        )

    def choose_border_color(self):

        current = QColor(
            self.settings.get(
                "border_color", DEFAULT_SETTINGS["border_color"],
            )
        )
        color = QColorDialog.getColor(
            current, self, tr("Escolher cor da borda"),
        )

        if color.isValid():
            self.settings["border_color"] = color.name()
            self.update_border_color_button()

    def update_border_color_button(self):

        color = self.settings.get(
            "border_color", DEFAULT_SETTINGS["border_color"],
        )
        self.border_color_button.setText(color)
        self.border_color_button.setStyleSheet(
            f"QPushButton {{ background: {color}; "
            f"color: white; padding: 8px; }}"
        )

    def test_steamgrid(self):
        """Consulta o SteamGridDB com a chave que está digitada (mesmo
        sem salvar) e mostra o resultado — ou o erro — na tela."""

        key = self.steamgrid_key_input.text().strip()

        if not key:
            self.steamgrid_test_result.setText(
                tr("Cole a chave da API primeiro.")
            )
            return

        name = self.steamgrid_test_input.text().strip() or "Hades"

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            url, status, detail = steamgrid_find_image(key, name)
        finally:
            QApplication.restoreOverrideCursor()

        if status == "ok":
            message = tr("✅ Funcionou! Imagem de “{name}”:\n{url}").format(
                name=name, url=url
            )
        elif status == "none":
            message = (
                tr("⚠ A chave é válida, mas para “{name}”: {detail}.").format(
                    name=name, detail=detail
                )
            )
        elif status == "auth":
            message = tr("❌ O SteamGridDB recusou a chave. Confira se copiou inteira.")
        else:
            message = tr("❌ Falha na consulta:\n{detail}").format(detail=detail)

        self.steamgrid_test_result.setText(message)

    def get_settings(self):

        self.settings["background"] = os.path.expanduser(
            self.background_input.text().strip()
        )
        self.settings["background_opacity"] = self.opacity_slider.value()
        self.settings["card_opacity"] = self.card_opacity_slider.value()
        self.settings["border_width"] = self.border_width_spin.value()
        self.settings["border_radius"] = self.border_radius_spin.value()
        self.settings["discord_rpc_enabled"] = (
            self.discord_enabled_checkbox.isChecked()
        )
        self.settings["steamgriddb_api_key"] = (
            self.steamgrid_key_input.text().strip()
        )
        self.settings["button_opacity"] = self.button_opacity_slider.value()
        self.settings["button_border_width"] = (
            self.button_border_width_spin.value()
        )
        self.settings["button_border_radius"] = (
            self.button_border_radius_spin.value()
        )
        self.settings["loading_window_enabled"] = (
            self.loading_enabled_checkbox.isChecked()
        )
        self.settings["loading_window_seconds"] = (
            self.loading_seconds_spin.value()
        )
        self.settings["wine_debug_enabled"] = (
            self.wine_debug_checkbox.isChecked()
        )
        self.settings["view_mode"] = (
            self.view_mode_combo.currentData() or "grade"
        )
        self.settings["language"] = (
            self.language_combo.currentData() or "auto"
        )
        self.settings["reorder_mode_enabled"] = (
            self.reorder_mode_checkbox.isChecked()
        )

        if self.button_hover_checkbox.isChecked():
            self.settings["button_hover_color"] = ""

        self.settings["sound_enabled"] = (
            self.sound_enabled_checkbox.isChecked()
        )
        self.settings["sound_volume"] = self.sound_volume_slider.value()

        sounds = {}
        for event_id, widgets in self.sound_event_widgets.items():
            sounds[event_id] = {
                "enabled": widgets["enabled"].isChecked(),
                "path": widgets["path"],
                "volume": widgets["volume"].value(),
            }
        self.settings["sounds"] = sounds

        return self.settings

    # ---- sons ----

    def choose_sound_file(self, event_id):

        path, _ = QFileDialog.getOpenFileName(
            self, tr("Escolher som"), str(Path.home()),
            tr("Áudio (*.mp3 *.wav *.ogg)"),
        )

        if not path:
            return

        widgets = self.sound_event_widgets[event_id]
        widgets["path"] = path
        widgets["path_label"].setText(path)

    def reset_sound_file(self, event_id):

        widgets = self.sound_event_widgets[event_id]
        widgets["path"] = ""
        widgets["path_label"].setText(
            tr("(padrão: sons/{id}.mp3)").format(id=event_id)
        )

    def test_sound(self, event_id):

        if not SOUND_BACKEND_AVAILABLE:
            QMessageBox.information(
                self, APP_NAME,
                tr("O suporte a sons (QtMultimedia) não está "
                "instalado nesta máquina."),
            )
            return

        widgets = self.sound_event_widgets[event_id]

        custom_path = widgets["path"]
        expanded = (
            os.path.expanduser(custom_path) if custom_path else ""
        )

        if expanded and os.path.isfile(expanded):
            path = expanded
        else:
            default_path = default_sound_path(event_id)
            if not default_path.is_file():
                QMessageBox.information(
                    self, APP_NAME,
                    tr(
                        "Nenhum arquivo de som encontrado pra esse "
                        "evento ainda. Escolha um arquivo ou coloque "
                        "um '{id}.mp3' na pasta sons/."
                    ).format(id=event_id),
                )
                return
            path = str(default_path)

        volume = widgets["volume"].value() / 100

        if self._test_player is None:
            self._test_player = QMediaPlayer(self)
            self._test_audio_output = QAudioOutput(self)
            self._test_player.setAudioOutput(self._test_audio_output)

        self._test_audio_output.setVolume(volume)
        self._test_player.setSource(QUrl.fromLocalFile(path))
        self._test_player.play()

    # ---- temas (exportar / importar) ----

    def export_theme(self):

        # Garante que o dicionário de configurações reflita o que
        # está nos controles antes de exportar (sem mexer nas
        # chaves que não fazem parte do tema).
        self.settings["background"] = os.path.expanduser(
            self.background_input.text().strip()
        )
        self.settings["background_opacity"] = self.opacity_slider.value()
        self.settings["card_opacity"] = self.card_opacity_slider.value()
        self.settings["border_width"] = self.border_width_spin.value()
        self.settings["border_radius"] = self.border_radius_spin.value()
        self.settings["button_opacity"] = self.button_opacity_slider.value()
        self.settings["button_border_width"] = (
            self.button_border_width_spin.value()
        )
        self.settings["button_border_radius"] = (
            self.button_border_radius_spin.value()
        )

        if self.button_hover_checkbox.isChecked():
            self.settings["button_hover_color"] = ""

        theme_data = {
            key: self.settings.get(key, DEFAULT_SETTINGS.get(key))
            for key in THEME_KEYS
        }

        file_path, _ = QFileDialog.getSaveFileName(
            self, tr("Exportar tema"), str(Path.home() / "tema.json"),
            tr("Tema UltimaLauncher (*.json)"),
        )

        if not file_path:
            return

        if not file_path.lower().endswith(".json"):
            file_path += ".json"

        try:
            with open(file_path, "w", encoding="utf-8") as file:
                json.dump(theme_data, file, indent=4, ensure_ascii=False)

            QMessageBox.information(
                self, tr("Exportar tema"),
                tr("Tema exportado com sucesso para:\n\n{path}").format(
                    path=file_path
                ),
            )

        except Exception as error:
            QMessageBox.critical(
                self, tr("Exportar tema"),
                tr("Não foi possível exportar o tema:\n\n{error}").format(
                    error=error
                ),
            )

    def import_theme(self):

        file_path, _ = QFileDialog.getOpenFileName(
            self, tr("Importar tema"), str(Path.home()),
            tr("Tema UltimaLauncher (*.json)"),
        )

        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as file:
                theme_data = json.load(file)
        except Exception as error:
            QMessageBox.critical(
                self, tr("Importar tema"),
                tr("Não foi possível ler o arquivo de tema:\n\n{error}").format(
                    error=error
                ),
            )
            return

        if not isinstance(theme_data, dict):
            QMessageBox.critical(
                self, tr("Importar tema"),
                tr("Esse arquivo não parece ser um tema válido."),
            )
            return

        applied = 0

        for key in THEME_KEYS:
            if key in theme_data:
                self.settings[key] = theme_data[key]
                applied += 1

        if applied == 0:
            QMessageBox.warning(
                self, tr("Importar tema"),
                tr("Nenhuma configuração de tema reconhecida nesse "
                "arquivo."),
            )
            return

        self.refresh_theme_controls()

        QMessageBox.information(
            self, tr("Importar tema"),
            tr("Tema importado. Clique em Aplicar pra confirmar as "
            "mudanças."),
        )

    def refresh_theme_controls(self):
        """Atualiza todos os controles visuais (cores, sliders,
        spinboxes) das abas Aparência/Cards/Botões pra refletir os
        valores atuais de self.settings — usado após importar um
        tema."""

        self.background_input.setText(
            self.settings.get("background", "")
        )
        self.opacity_slider.setValue(
            int(self.settings.get("background_opacity", 35))
        )
        self.update_color_button()

        self.card_opacity_slider.setValue(
            int(
                self.settings.get(
                    "card_opacity", DEFAULT_SETTINGS["card_opacity"],
                )
            )
        )
        self.update_card_color_button()

        self.border_width_spin.setValue(
            int(
                self.settings.get(
                    "border_width", DEFAULT_SETTINGS["border_width"],
                )
            )
        )
        self.border_radius_spin.setValue(
            int(
                self.settings.get(
                    "border_radius", DEFAULT_SETTINGS["border_radius"],
                )
            )
        )
        self.update_border_color_button()

        self.button_opacity_slider.setValue(
            int(
                self.settings.get(
                    "button_opacity",
                    DEFAULT_SETTINGS["button_opacity"],
                )
            )
        )
        self.button_border_width_spin.setValue(
            int(
                self.settings.get(
                    "button_border_width",
                    DEFAULT_SETTINGS["button_border_width"],
                )
            )
        )
        self.button_border_radius_spin.setValue(
            int(
                self.settings.get(
                    "button_border_radius",
                    DEFAULT_SETTINGS["button_border_radius"],
                )
            )
        )
        self.update_setting_color_button(
            "button_color", self.button_color_button
        )
        self.update_setting_color_button(
            "button_text_color", self.button_text_color_button
        )
        self.update_setting_color_button(
            "button_border_color", self.button_border_color_button
        )

        self.button_hover_checkbox.setChecked(
            not self.settings.get("button_hover_color", "")
        )
        self.update_hover_controls()

    # ---- atualização do launcher ----

    def update_launcher_file(self):

        file_path, _ = QFileDialog.getOpenFileName(
            self, tr("Selecionar novo launcher.py"), str(Path.home()),
            tr("Python (*.py)"),
        )

        if not file_path:
            return

        source = Path(file_path)

        try:
            source_text = source.read_text(encoding="utf-8")
        except Exception as error:
            QMessageBox.critical(
                self, tr("Atualizar launcher"),
                tr(
                    "Não foi possível ler o arquivo selecionado:\n\n{error}"
                ).format(error=error),
            )
            return

        new_version = extract_launcher_version(source_text)

        if new_version is None:
            QMessageBox.critical(
                self, tr("Atualizar launcher"),
                tr("Não encontrei o número de versão nesse arquivo. "
                "Confirme se é mesmo um launcher.py válido."),
            )
            return

        if parse_version(new_version) <= parse_version(LAUNCHER_VERSION):
            QMessageBox.information(
                self, tr("Atualizar launcher"),
                tr(
                    "O arquivo selecionado é a versão {new}, "
                    "que não é mais nova que a versão instalada "
                    "({current}). Nada foi alterado."
                ).format(new=new_version, current=LAUNCHER_VERSION),
            )
            return

        answer = QMessageBox.question(
            self, tr("Atualizar launcher"),
            tr(
                "Foi encontrada a versão {new} (atual: "
                "{current}).\n\nDeseja atualizar agora? O "
                "launcher precisa ser reaberto depois pra aplicar as "
                "mudanças."
            ).format(new=new_version, current=LAUNCHER_VERSION),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            source_bytes = source.read_bytes()
            SCRIPT_PATH.write_bytes(source_bytes)
            try:
                SCRIPT_PATH.chmod(0o755)
            except OSError:
                pass
        except Exception as error:
            QMessageBox.critical(
                self, tr("Atualizar launcher"),
                tr("Não foi possível atualizar o launcher:\n\n{error}").format(
                    error=error
                ),
            )
            return

        self.update_version_label.setText(
            tr("Versão instalada: {version} (reabra o launcher)").format(
                version=new_version
            )
        )

        QMessageBox.information(
            self, tr("Atualizar launcher"),
            tr(
                "Atualizado para a versão {new} com sucesso!\n\n"
                "Feche e abra o {app} de novo pra usar a nova "
                "versão."
            ).format(new=new_version, app=APP_NAME),
        )


# ---- card de jogo (biblioteca) ----

def load_cover_pixmap(path, target_size, mode=DEFAULT_COVER_MODE):
    """Carrega a capa do jogo e recorta pra preencher o
    target_size por completo (estilo "cover"), igual o papel de
    parede do launcher: sem tarjas pretas e sem distorcer a
    proporção original, só cortando as bordas que sobrarem
    (mode="crop"). Com mode="stretch" a imagem inteira é esticada
    pro tamanho do card, sem cortar nada.

    Capa e ícone agora são coisas separadas — esta função é só
    pra capa. Pra ícone, use load_icon_pixmap.
    """

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    image = reader.read()

    if image.isNull():
        return None

    pixmap = QPixmap.fromImage(image)

    if pixmap.width() <= 0 or pixmap.height() <= 0:
        return None

    if mode == "stretch":
        # Imagem inteira esticada pro tamanho exato do card: nada é
        # cortado, mas a proporção original não é mantida.
        return pixmap.scaled(
            target_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    scaled = pixmap.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )

    x = max(0, (scaled.width() - target_size.width()) // 2)
    y = max(0, (scaled.height() - target_size.height()) // 2)

    return scaled.copy(x, y, target_size.width(), target_size.height())


def load_icon_pixmap(path, target_size):
    """Carrega um ícone e redimensiona ele inteiro dentro do
    target_size, mantendo a proporção e centralizado num fundo
    transparente (estilo "contain"). Diferente da capa, o ícone
    nunca é cortado — ele só encolhe ou cresce inteiro."""

    reader = QImageReader(path)
    reader.setAutoTransform(True)
    image = reader.read()

    if image.isNull():
        return None

    pixmap = QPixmap.fromImage(image)

    if pixmap.width() <= 0 or pixmap.height() <= 0:
        return None

    scaled = pixmap.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    canvas = QPixmap(target_size)
    canvas.fill(Qt.GlobalColor.transparent)

    painter = QPainter(canvas)
    x = (target_size.width() - scaled.width()) // 2
    y = (target_size.height() - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()

    return canvas


CARD_MARGIN = 12
CARD_IMAGE_HEIGHT = 130   # altura da área do ícone (modo grade)
CARD_NAME_LINES = 2       # linhas reservadas pro nome do jogo
CARD_HEIGHT = 360         # altura de TODOS os cards no modo grade
CARD_WIDTH = 270          # largura MÁXIMA dos cards no modo grade
CARD_GRID_SPACING = 18    # espaço entre os cards da grade


class FitIconLabel(QLabel):
    """Área de ícone do card sem capa. Em vez de ter um tamanho
    fixo (que ficava mais largo que o card e deslocava o ícone),
    ela ocupa a largura REAL que o card tem e redesenha o ícone
    (estilo "contain", centralizado) toda vez que é redimensionada.
    Sem ícone, mostra o 🎮 com tamanho proporcional à área."""

    def __init__(self, icon_path=""):
        super().__init__()

        self._source = None
        self._last_size = None

        if icon_path:
            reader = QImageReader(icon_path)
            reader.setAutoTransform(True)
            image = reader.read()
            if not image.isNull():
                self._source = QPixmap.fromImage(image)

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "QLabel { border: none; background: transparent; }"
        )
        # Ignored: o tamanho do pixmap não influencia o layout, quem
        # manda é o espaço que o card realmente tem.
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored,
        )
        self.setMinimumSize(1, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)

        size = self.size()

        if size == self._last_size or size.width() < 2 or size.height() < 2:
            return

        self._last_size = size

        if self._source is not None:
            self.setPixmap(
                self._source.scaled(
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            font_px = max(24, min(70, int(size.height() * 0.6)))
            self.setText("🎮")
            self.setStyleSheet(
                f"QLabel {{ font-size: {font_px}px; border: none; "
                "background: transparent; }"
            )


# Tipo MIME próprio pro drag-and-drop de reordenar os cards —
# evita aceitar um drop de qualquer outra coisa (um arquivo do
# gerenciador de janelas, por exemplo) por engano.
REORDER_MIME_TYPE = "application/x-ultimalauncher-game-index"


class CardDropContainer(QWidget):
    """Fundo da grade/fileira de cards. Aceita o arrastar-e-soltar de
    reordenar também nos espaços ENTRE os cards (e na área vazia),
    repassando tudo pro launcher — assim soltar o card num vão não
    cancela a reordenação."""

    def __init__(self, launcher):

        super().__init__()

        self.launcher = launcher
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        self.launcher.card_drag_over(event, event.position().toPoint())

    def dragMoveEvent(self, event):
        self.launcher.card_drag_over(event, event.position().toPoint())

    def dropEvent(self, event):
        self.launcher.card_drag_drop(event)


class GameCard(QFrame):

    def __init__(
        self, game, index, launcher, stretched=False, fixed_height=None,
        allow_reorder=True,
    ):

        super().__init__()

        self.game = game
        self.index = index
        self.launcher = launcher
        self.stretched = stretched

        self.reorder_mode = allow_reorder and bool(
            launcher.settings.get(
                "reorder_mode_enabled",
                DEFAULT_SETTINGS["reorder_mode_enabled"],
            )
        )
        self._drag_start_pos = None

        self._background_pixmap = None
        self._badge_pixmap = None
        self._background_cache_size = None

        # O selo do ícone é um QLabel de verdade (não desenhado no
        # paintEvent) justamente pra poder ficar por cima dos
        # botões "Editar"/"🗑" com raise_() — conteúdo pintado no
        # paintEvent do card sempre fica atrás dos widgets filhos
        # de verdade, então não tinha como competir com eles.
        self.badge_label = QLabel(self)
        self.badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_label.setStyleSheet(
            "QLabel { background: transparent; border: none; }"
        )
        self.badge_label.hide()

        cover = os.path.expanduser(game.get("cover", ""))
        icon = os.path.expanduser(game.get("icon", ""))

        self.has_cover = bool(cover) and os.path.exists(cover)
        self.has_icon = bool(icon) and os.path.exists(icon)

        show_image = game.get("show_image", True)

        # Quando tem capa, ela vira o próprio fundo do card (pintada
        # no paintEvent, esticada/recortada pro card inteiro e com
        # os cantos arredondados). Sem capa, o card volta a ser o
        # fundo sólido de sempre, com o ícone (ou o 🎮) numa área
        # fixa no topo.
        self.full_bleed = show_image and self.has_cover

        self.setObjectName("gameCardCover" if self.full_bleed else "gameCard")

        if stretched:
            height = fixed_height or 260
            width = max(160, int(height * 0.68))
            self.setFixedSize(width, height)
        else:
            # Largura e altura fixas e iguais pra TODOS os cards da
            # grade, em qualquer fileira/coluna. Antes a largura ia
            # de 185 a 270 e o layout repartia o espaço entre as
            # colunas conforme o conteúdo de cada card, então a
            # última coluna acabava mais estreita. Pra mudar o
            # tamanho dos cards é só ajustar CARD_WIDTH/CARD_HEIGHT.
            self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
            self.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Fixed,
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CARD_MARGIN, CARD_MARGIN + 2, CARD_MARGIN, CARD_MARGIN + 2,
        )
        layout.setSpacing(8)

        self.image_label = None

        if self.full_bleed:
            # O conteúdo (nome, tempo jogado, botões) fica
            # empurrado pro rodapé, flutuando sobre a capa. O espaço
            # acima do texto tem altura mínima (como a área de ícone
            # dos cards sem capa) pra capa aparecer e pro selo do
            # ícone ter onde ficar sem encostar no texto.
            layout.addSpacing(40 if stretched else CARD_IMAGE_HEIGHT)
            layout.addStretch()
        elif show_image:
            self.image_label = FitIconLabel(icon if self.has_icon else "")

            if stretched:
                # Modo linha: o card tem altura fixa, então o ícone
                # ocupa o que sobrar depois do texto e dos botões.
                self.image_label.setMinimumHeight(40)
                layout.addWidget(self.image_label, 1)
            else:
                self.image_label.setFixedHeight(CARD_IMAGE_HEIGHT)
                layout.addWidget(self.image_label)

        name = QLabel(game.get("name", tr("Sem nome")))
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setWordWrap(True)
        name.setStyleSheet(
            "QLabel { font-size: 15px; font-weight: bold; "
            "border: none; background: transparent; }"
        )

        # Reserva sempre a altura de 2 linhas pro nome. Sem isso o
        # layout calculava a altura do card com o nome em 1 linha e,
        # quando ele quebrava em 2 (card mais estreito ou nome
        # grande), o texto invadia o que vinha embaixo.
        name_font = name.font()
        name_font.setPixelSize(15)
        name_font.setBold(True)
        name.setFixedHeight(
            QFontMetrics(name_font).lineSpacing() * CARD_NAME_LINES + 4
        )
        name.setToolTip(game.get("name", tr("Sem nome")))
        layout.addWidget(name)

        runner = QLabel(tr(game.get("runner", "Wine")))
        runner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        runner.setStyleSheet(
            "QLabel { color: #ccc; border: none; background: transparent; }"
        )
        layout.addWidget(runner)

        playtime = QLabel(
            "⏱ " + format_playtime(
                game.get("total_playtime_seconds", 0)
            )
        )
        playtime.setAlignment(Qt.AlignmentFlag.AlignCenter)
        playtime.setStyleSheet(
            "QLabel { color: #ccc; font-size: 12px; border: none; "
            "background: transparent; }"
        )
        layout.addWidget(playtime)

        play = QPushButton(tr("▶  Jogar"))
        play.clicked.connect(
            lambda: self._run_later(self.launcher.launch_game)
        )
        layout.addWidget(play)

        # Referência guardada pra posicionar o selo do ícone logo
        # ACIMA do botão "Jogar" (veja position_badge) — ele é o
        # primeiro botão de baixo pra cima, então ancorar nele
        # garante que o selo também limpe o Editar/🗑 que vêm
        # depois dele, sem precisar checar cada um.
        self.footer_top_widget = play

        buttons = QHBoxLayout()

        edit = QPushButton(tr("✏ Editar"))
        edit.clicked.connect(
            lambda: self._run_later(self.launcher.edit_game)
        )

        remove = QPushButton("🗑")
        remove.clicked.connect(
            lambda: self._run_later(self.launcher.remove_game)
        )

        buttons.addWidget(edit)
        buttons.addWidget(remove)
        layout.addLayout(buttons)

        if not self.full_bleed:
            layout.addStretch()

        if self.reorder_mode:
            # No modo de reordenar, os botões do card ficam
            # desativados (clicar neles enquanto arrasta ia ser
            # confuso) e os labels/ícone viram "transparentes" a
            # clique, pra dar pra começar a arrastar de qualquer
            # ponto do card, não só nas bordas vazias.
            play.setEnabled(False)
            edit.setEnabled(False)
            remove.setEnabled(False)

            for widget in (self.image_label, name, runner, playtime):
                if widget is not None:
                    widget.setAttribute(
                        Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                        True,
                    )

            self.badge_label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True,
            )

            self.setAcceptDrops(True)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.setToolTip(
                tr("Arraste pra outra posição pra reordenar")
            )

    def mousePressEvent(self, event):

        if self.reorder_mode and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):

        if (
            self.reorder_mode
            and self._drag_start_pos is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (
                event.position().toPoint() - self._drag_start_pos
            ).manhattanLength() > QApplication.startDragDistance()
        ):
            hot_spot = self._drag_start_pos
            self._drag_start_pos = None

            # Imagem que acompanha o mouse (levemente translúcida).
            grabbed = self.grab()
            ghost = QPixmap(grabbed.size())
            ghost.setDevicePixelRatio(grabbed.devicePixelRatio())
            ghost.fill(Qt.GlobalColor.transparent)

            painter = QPainter(ghost)
            painter.setOpacity(0.85)
            painter.drawPixmap(0, 0, grabbed)
            painter.end()

            mime = QMimeData()
            mime.setData(
                REORDER_MIME_TYPE, str(self.index).encode("utf-8"),
            )

            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(ghost)
            drag.setHotSpot(hot_spot)

            self.launcher.begin_card_drag(self)

            try:
                drag.exec(Qt.DropAction.MoveAction)
            finally:
                # IMPORTANTE: a reordenação em si (que recria todos
                # os cards) NÃO acontece dentro do drop. Antes ela
                # rodava aqui dentro e destruía este card e o card
                # alvo enquanto os dois ainda estavam no meio de um
                # evento — daí o crash. Agora o launcher só agenda
                # o refresh pra DEPOIS que este método terminar.
                self.launcher.finish_card_drag()

            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):

        self._drag_start_pos = None
        super().mouseReleaseEvent(event)

    def _run_later(self, action):
        """Roda a ação do botão (Jogar/Editar/🗑) só depois que o
        clique terminou de ser tratado. Essas ações abrem diálogos e
        acabam recriando todos os cards; fazer isso de dentro do
        próprio clique destruía o card que ainda estava no meio do
        evento — no carrossel (cards dentro de uma QGraphicsScene)
        isso derrubava o launcher."""

        index = self.index
        QTimer.singleShot(0, lambda: action(index))

    def set_drag_ghost(self, active):
        """Deixa o card de origem translúcido enquanto ele é
        arrastado — ele vira o \"espaço vazio\" que mostra onde o
        card vai cair."""

        if active:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(0.35)
            self.setGraphicsEffect(effect)
        else:
            self.setGraphicsEffect(None)

    def _forward_drag(self, event):

        if not (
            self.reorder_mode
            and event.mimeData().hasFormat(REORDER_MIME_TYPE)
        ):
            event.ignore()
            return

        # Posição do mouse nas coordenadas do container (pai do
        # card), que é onde os "slots" do preview são calculados.
        self.launcher.card_drag_over(
            event, self.mapToParent(event.position().toPoint()),
        )

    def dragEnterEvent(self, event):
        self._forward_drag(event)

    def dragMoveEvent(self, event):
        self._forward_drag(event)

    def dropEvent(self, event):

        if not (
            self.reorder_mode
            and event.mimeData().hasFormat(REORDER_MIME_TYPE)
        ):
            event.ignore()
            return

        self.launcher.card_drag_drop(event)

    def refresh_background_pixmap(self, size):
        """Recorta a capa pro tamanho atual do card inteiro e
        prepara o selo do ícone, se houver um pra mostrar. Chamado
        de dentro do paintEvent (mesmo esquema de cache que a janela
        principal já usa pro papel de fundo), então sempre reflete o
        tamanho que o card realmente ocupa na grade."""

        cover = os.path.expanduser(self.game.get("cover", ""))
        self._background_pixmap = load_cover_pixmap(
            cover, size,
            self.game.get("cover_mode", DEFAULT_COVER_MODE),
        )

        self._badge_pixmap = None

        if self.has_icon and self.game.get("show_icon_badge", True):

            icon = os.path.expanduser(self.game.get("icon", ""))
            badge_size = max(
                28,
                int(min(size.width(), size.height()) * 0.22),
            )
            self._badge_pixmap = load_icon_pixmap(
                icon, QSize(badge_size, badge_size),
            )

        self._background_cache_size = size

        self.position_badge()

    def position_badge(self):
        """Posiciona o selo do ícone (QLabel de verdade)
        do card, no canto superior direito, por cima da capa (a
        parte de cima não tem texto nem botão). raise_() garante que
        ele fique na frente da capa."""

        if self._badge_pixmap is None:
            self.badge_label.hide()
            return

        padding = 8
        box_size = self._badge_pixmap.width() + padding

        self.badge_label.setFixedSize(box_size, box_size)
        self.badge_label.setPixmap(self._badge_pixmap)
        self.badge_label.setStyleSheet(
            "QLabel { background-color: rgba(0, 0, 0, 140); "
            f"border-radius: {padding}px; }}"
        )

        # Canto superior direito, dentro da área livre da capa. Antes
        # ele ficava logo acima do botão Jogar — justamente onde
        # estão o runner e o tempo jogado — e acabava por cima
        # desses textos.
        margin = max(8, CARD_MARGIN)
        x = self.width() - box_size - margin
        y = margin

        self.badge_label.move(x, y)
        self.badge_label.show()
        self.badge_label.raise_()

    def paintEvent(self, event):

        if not self.full_bleed:
            super().paintEvent(event)
            return

        size = self.size()

        if (
            size.width() > 0
            and size.height() > 0
            and size != self._background_cache_size
        ):
            self.refresh_background_pixmap(size)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect())
        radius = float(
            self.launcher.settings.get(
                "border_radius", DEFAULT_SETTINGS["border_radius"],
            )
        )

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        if self._background_pixmap is not None:
            painter.drawPixmap(0, 0, self._background_pixmap)
        else:
            card_color = self.launcher.settings.get(
                "card_color", DEFAULT_SETTINGS["card_color"],
            )
            painter.fillRect(self.rect(), QColor(card_color))

        # Gradiente escuro no rodapé pra nome/tempo/botões ficarem
        # legíveis em cima de qualquer capa, clara ou escura.
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(0, 0, 0, 0))
        gradient.setColorAt(0.45, QColor(0, 0, 0, 60))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 205))
        painter.fillRect(self.rect(), gradient)

        painter.end()

        # Deixa o QFrame desenhar a borda por cima (o fundo dele já
        # está transparente pro objectName "gameCardCover").
        super().paintEvent(event)


# ---- widgets de estatística ----

class PlaytimeBar(QWidget):

    def __init__(self, fraction, color="#8b5cf6", parent=None):

        super().__init__(parent)

        self.fraction = max(0.0, min(1.0, fraction))
        self.color = QColor(color)

        self.setFixedHeight(8)
        self.setMinimumWidth(80)

    def paintEvent(self, event):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        radius = h / 2

        bg = QColor(self.color)
        bg.setAlpha(40)

        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, w, h, radius, radius)

        fill_width = int(w * self.fraction)

        if fill_width > 0:
            painter.setBrush(self.color)
            painter.drawRoundedRect(
                0, 0, fill_width, h, radius, radius
            )


class StatSummaryCard(QFrame):

    def __init__(self, icon, value, label, sublabel="", parent=None):

        super().__init__(parent)

        self.setObjectName("statSummaryCard")
        self.setMinimumSize(150, 110)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet(
            "font-size: 22px; background: transparent; border: none;"
        )
        layout.addWidget(icon_label)

        value_label = QLabel(value)
        value_label.setStyleSheet(
            "font-size: 20px; font-weight: bold; "
            "background: transparent; border: none;"
        )
        value_label.setWordWrap(True)
        layout.addWidget(value_label)

        label_label = QLabel(label)
        label_label.setStyleSheet(
            "font-size: 12px; color: #aaa; "
            "background: transparent; border: none;"
        )
        layout.addWidget(label_label)

        if sublabel:
            sub_label = QLabel(sublabel)
            sub_label.setStyleSheet(
                "font-size: 11px; color: #777; "
                "background: transparent; border: none;"
            )
            layout.addWidget(sub_label)

        layout.addStretch()


class RankBadge(QLabel):

    def __init__(self, position, parent=None):

        super().__init__(parent)

        self.setFixedSize(34, 34)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if position == 1:
            text = "🥇"
            bg = "rgba(212, 175, 55, 45)"
            border = "#d4af37"
            font_size = "18px"
        elif position == 2:
            text = "🥈"
            bg = "rgba(192, 192, 192, 45)"
            border = "#c0c0c0"
            font_size = "18px"
        elif position == 3:
            text = "🥉"
            bg = "rgba(205, 127, 50, 45)"
            border = "#cd7f32"
            font_size = "18px"
        else:
            text = str(position)
            bg = "rgba(255, 255, 255, 12)"
            border = "rgba(255, 255, 255, 35)"
            font_size = "13px"

        self.setText(text)

        self.setStyleSheet(
            f"""
            QLabel {{
                background: {bg};
                border: 1px solid {border};
                border-radius: 17px;
                font-size: {font_size};
                font-weight: bold;
                color: #ddd;
            }}
            """
        )


def format_percent(fraction):
    """0.1234 -> '12,3%' (vírgula decimal). Valores minúsculos
    viram '<0,1%' em vez de '0,0%'."""

    percent = fraction * 100

    if percent <= 0:
        return "0%"

    if percent < 0.1:
        return "<0,1%"

    if percent >= 99.95:
        return "100%"

    return f"{percent:.1f}".replace(".", ",") + "%"


class StatRow(QFrame):

    def __init__(self, game, fraction, accent, position, parent=None):

        super().__init__(parent)

        self.setObjectName("statRow")

        name = game.get("name", tr("Sem nome"))
        total = game.get("total_playtime_seconds", 0)
        times = game.get("times_played", 0)
        last = game.get("last_played")
        longest = game.get("longest_session_seconds", 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        badge = RankBadge(position)
        top_row.addWidget(badge)

        name_label = QLabel(name)
        name_label.setStyleSheet(
            "font-size: 15px; font-weight: bold; "
            "background: transparent; border: none;"
        )
        name_label.setWordWrap(True)
        top_row.addWidget(name_label, stretch=1)

        total_label = QLabel(format_playtime(total))
        total_label.setStyleSheet(
            f"font-size: 14px; color: {accent}; "
            f"font-weight: bold; "
            f"background: transparent; border: none;"
        )
        top_row.addWidget(total_label)

        layout.addLayout(top_row)

        # A barra mostra a fatia do TEMPO TOTAL jogado (soma de todos
        # os jogos) que esse jogo representa, com a porcentagem ao
        # lado.
        bar_row = QHBoxLayout()
        bar_row.setSpacing(10)

        bar = PlaytimeBar(fraction, accent)
        bar_row.addWidget(bar, stretch=1)

        percent_label = QLabel(format_percent(fraction))
        percent_label.setMinimumWidth(52)
        percent_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        percent_label.setToolTip(tr("Porcentagem do tempo total jogado"))
        percent_label.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #999; "
            "background: transparent; border: none;"
        )
        bar_row.addWidget(percent_label)

        layout.addLayout(bar_row)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)

        def small(text):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "font-size: 11px; color: #999; "
                "background: transparent; border: none;"
            )
            return lbl

        stats_row.addWidget(small(f"🎮  {times}x"))

        if last:
            stats_row.addWidget(
                small(f"📅  {format_last_played(last)}")
            )
        else:
            stats_row.addWidget(small(tr("📅  nunca jogado")))

        if longest > 0:
            stats_row.addWidget(
                small(f"🏆  {format_playtime(longest)}")
            )

        stats_row.addStretch()
        layout.addLayout(stats_row)


# ---- gráfico de pizza ----

PIE_CHART_COLORS = [
    "#8b5cf6",
    "#ec4899",
    "#f59e0b",
    "#10b981",
    "#3b82f6",
    "#ef4444",
    "#14b8a6",
    "#f97316",
    "#a855f7",
    "#06b6d4",
    "#84cc16",
    "#eab308",
    "#6366f1",
    "#22c55e",
    "#f43f5e",
    "#0ea5e9",
]


def prepare_pie_data(games, max_slices=14):

    data = []

    for game in games:
        secs = game.get("total_playtime_seconds", 0)
        if secs > 0:
            data.append((game.get("name", tr("Sem nome")), secs))

    data.sort(key=lambda item: item[1], reverse=True)

    if len(data) > max_slices:
        top = data[: max_slices - 1]
        rest_secs = sum(secs for _, secs in data[max_slices - 1 :])
        top.append((tr("Outros"), rest_secs))
        return top

    return data


class PieChartWidget(QWidget):

    def __init__(self, data, accent, parent=None):
        super().__init__(parent)

        self.data = data
        self.accent = accent

        self.setMinimumSize(880, 520)

    def _colors(self):

        base = [self.accent] + [
            c for c in PIE_CHART_COLORS
            if c.lower() != self.accent.lower()
        ]

        colors = []

        for i in range(len(self.data)):
            colors.append(QColor(base[i % len(base)]))

        return colors

    def paintEvent(self, event):

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg_color = QColor("#1a1a24")
        painter.fillRect(self.rect(), bg_color)

        total = sum(secs for _, secs in self.data)

        if total <= 0:
            painter.setPen(QColor("#888"))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                tr("Nenhum dado de tempo ainda."),
            )
            return

        colors = self._colors()

        chart_size = min(340, self.height() - 80)
        chart_size = max(chart_size, 200)

        chart_x = 40
        chart_y = (self.height() - chart_size) // 2

        chart_rect = QRect(chart_x, chart_y, chart_size, chart_size)

        start_angle = 90 * 16
        for i, (_, secs) in enumerate(self.data):
            span = int(round(360 * 16 * (secs / total)))
            painter.setBrush(colors[i])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPie(chart_rect, start_angle, -span)
            start_angle -= span

        hole_size = int(chart_size * 0.55)
        hole_rect = QRect(
            chart_x + (chart_size - hole_size) // 2,
            chart_y + (chart_size - hole_size) // 2,
            hole_size,
            hole_size,
        )

        painter.setBrush(bg_color)
        painter.drawEllipse(hole_rect)

        painter.setPen(QColor("#ddd"))
        center_font = painter.font()
        center_font.setPointSize(10)
        center_font.setBold(False)
        painter.setFont(center_font)

        painter.drawText(
            hole_rect,
            Qt.AlignmentFlag.AlignCenter,
            tr("Total") + "\n" + format_playtime(total),
        )

        legend_x0 = chart_x + chart_size + 50
        legend_y0 = 60
        line_h = 24
        col_w = 240

        n = len(self.data)
        n_cols = 2 if n > 10 else 1
        per_col = (n + n_cols - 1) // n_cols

        legend_font = painter.font()
        legend_font.setPointSize(10)
        legend_font.setBold(False)
        painter.setFont(legend_font)

        for i, (name, secs) in enumerate(self.data):

            col = i // per_col
            row = i % per_col

            x = legend_x0 + col * col_w
            y = legend_y0 + row * line_h

            painter.setBrush(colors[i])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, y + 6, 12, 12, 3, 3)

            display_name = name
            if len(display_name) > 16:
                display_name = display_name[:15] + "…"

            time_str = format_playtime(secs)
            pct = secs / total * 100

            painter.setPen(QColor("#ddd"))
            painter.drawText(
                x + 22, y + 17,
                f"{display_name}  {time_str}",
            )

            painter.setPen(QColor("#888"))
            painter.drawText(
                x + col_w - 50, y + 17,
                f"{pct:.0f}%",
            )


class PieChartDialog(QDialog):

    def __init__(self, data, accent, parent=None):

        super().__init__(parent)

        self.setWindowTitle(tr("{app} — Gráfico de horas").format(app=APP_NAME))
        self.setMinimumSize(940, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        chart = PieChartWidget(data, accent)
        layout.addWidget(chart)

        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 12)

        footer.addStretch()

        close = QPushButton(tr("Fechar"))
        close.clicked.connect(self.accept)

        footer.addWidget(close)

        layout.addLayout(footer)

        wire_click_sounds(self, getattr(parent, "sound_manager", None))


# ---- janela principal ----

class CardCarousel(QGraphicsView):
    """Modo fileira: carrossel infinito de cards. O card do meio é
    o foco (tamanho cheio, único com botões ativos); os vizinhos
    ficam menores, mais apagados e atrás dele, dando sensação de
    profundidade. Passou do último, volta pro primeiro (e vice-versa).

    Os cards são widgets de verdade dentro de uma QGraphicsScene —
    assim dá pra escalar e esmaecer cada um com animação suave."""

    FADE_LIMIT = 3.5        # distância (em cards) onde o card some
    SCALE_STEP = 0.17       # quanto cada posição encolhe o card
    DIM_STEP = 0.20         # quanto cada posição apaga o card

    def __init__(self, launcher):

        super().__init__()

        self.launcher = launcher

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
            | QPainter.RenderHint.TextAntialiasing
        )
        self.setViewportUpdateMode(
            QGraphicsView.ViewportUpdateMode.FullViewportUpdate
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            "QGraphicsView { background: transparent; border: none; }"
        )
        self.viewport().setAutoFillBackground(False)

        self._games = []        # [(índice_do_jogo, jogo)]
        self._slots = []        # um proxy por posição do anel
        self._ring = 0
        self._card_w = 0
        self._card_h = 0
        self._center = 0.0      # posição atual (animada, fracionada)
        self._target = 0        # posição de destino (inteira)
        self._wheel = 0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(300)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._normalize)

        self.empty_label = QLabel(
            tr("Nenhum jogo encontrado.\n\n"
            "Clique em + Adicionar para começar."),
            self,
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(
            "QLabel { font-size: 18px; color: #999; "
            "background: transparent; }"
        )
        self.empty_label.hide()

        self.prev_button = self._make_arrow("‹", -1)
        self.next_button = self._make_arrow("›", 1)

    def _make_arrow(self, text, step):

        button = QPushButton(text, self)
        button.setFixedSize(38, 60)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setStyleSheet(
            "QPushButton { padding: 0px; font-size: 26px; }"
        )
        button.clicked.connect(lambda: self.go(step))
        button.hide()
        return button

    # ------------------------------------------------ montagem

    def focused_game_index(self):
        """Índice (em self.games) do jogo que está no meio agora."""

        if not self._games or self._ring == 0:
            return None

        position = int(round(self._center)) % len(self._games)
        return self._games[position][0]

    def clear(self):
        """Remove todos os cards. Os antigos são só escondidos na
        hora e tirados da cena/destruídos logo depois, num momento
        em que nenhum evento deles está em andamento."""

        self._anim.stop()

        old = self._slots

        for proxy in old:
            proxy.setVisible(False)

        self._slots = []
        self._games = []
        self._ring = 0

        if old:
            QTimer.singleShot(0, lambda: self._destroy_proxies(old))

    def _destroy_proxies(self, proxies):

        for proxy in proxies:
            try:
                self._scene.removeItem(proxy)
                proxy.deleteLater()
            except RuntimeError:
                pass

    def set_games(self, games, card_height):

        keep_index = self.focused_game_index()

        self.clear()

        self._games = list(games)
        count = len(self._games)

        self.empty_label.setVisible(count == 0)
        self.prev_button.setVisible(count > 1)
        self.next_button.setVisible(count > 1)
        self._place_overlays()

        if count == 0:
            return

        # Pra o efeito de "infinito" ficar contínuo, o anel precisa
        # ter pelo menos 7 posições. Com poucos jogos, os cards se
        # repetem (cópias) só pra preencher os lados.
        if count == 1:
            copies = 1
        else:
            copies = max(1, -(-7 // count))

        self._ring = count * copies

        stylesheet = self.launcher.styleSheet()

        for slot in range(self._ring):

            index, game = self._games[slot % count]

            card = GameCard(
                game, index, self.launcher,
                stretched=True, fixed_height=card_height,
                allow_reorder=False,
            )

            # Widgets dentro da cena não herdam o estilo da janela
            # principal (não têm pai), então o tema é copiado.
            card.setStyleSheet(stylesheet)

            # Botões sem foco: assim as setas do teclado continuam
            # navegando o carrossel depois de um clique.
            for button in card.findChildren(QPushButton):
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

            proxy = self._scene.addWidget(card)
            proxy.setTransformOriginPoint(proxy.boundingRect().center())

            self._slots.append(proxy)
            self._card_w = card.width()
            self._card_h = card.height()

        start = 0

        if keep_index is not None:
            for position, (index, _game) in enumerate(self._games):
                if index == keep_index:
                    start = position
                    break

        self._center = float(start)
        self._target = start

        self._relayout()

    # ------------------------------------------------ posicionamento

    def _signed_offset(self, slot):
        """Distância (com sinal) do card até o centro, dando a volta
        no anel: o que passou do último reaparece antes do primeiro."""

        ring = self._ring
        return ((slot - self._center + ring / 2) % ring) - ring / 2

    def _opacity_for(self, distance):

        limit = min(self.FADE_LIMIT, self._ring / 2)

        if distance >= limit:
            return 0.0

        fade = 1.0 if distance <= limit - 0.5 else (limit - distance) / 0.5

        return fade * (1.0 - self.DIM_STEP * min(distance, 3.0))

    def _relayout(self):

        if self._ring == 0:
            return

        width = self.viewport().width()
        height = self.viewport().height()

        for slot, proxy in enumerate(self._slots):

            offset = self._signed_offset(slot)
            distance = abs(offset)
            opacity = self._opacity_for(distance)

            if opacity <= 0.01:
                proxy.setVisible(False)
                continue

            scale = max(0.35, 1.0 - self.SCALE_STEP * distance)

            # Espaçamento que vai diminuindo com a distância: os
            # vizinhos ficam parcialmente atrás do card do meio.
            spread = (0.82 * distance - 0.05 * distance * distance)
            sign = 1 if offset >= 0 else -1

            center_x = width / 2 + sign * spread * self._card_w
            center_y = height / 2

            proxy.setScale(scale)
            proxy.setOpacity(opacity)
            proxy.setZValue(100 - distance * 10)
            proxy.setPos(
                center_x - self._card_w / 2,
                center_y - self._card_h / 2,
            )
            proxy.setVisible(True)

    def _place_overlays(self):

        width = self.viewport().width()
        height = self.viewport().height()

        self.empty_label.setGeometry(0, 0, width, height)

        self.prev_button.move(6, (height - self.prev_button.height()) // 2)
        self.next_button.move(
            width - self.next_button.width() - 6,
            (height - self.next_button.height()) // 2,
        )

        self.prev_button.raise_()
        self.next_button.raise_()

    def resizeEvent(self, event):

        super().resizeEvent(event)

        self._scene.setSceneRect(
            0, 0, self.viewport().width(), self.viewport().height(),
        )
        self._place_overlays()
        self._relayout()

    # ------------------------------------------------ navegação

    def go(self, steps):
        self.go_to(self._target + steps)

    def go_to(self, target):

        if self._ring <= 1:
            return

        self._target = int(target)

        self._anim.stop()
        self._anim.setStartValue(float(self._center))
        self._anim.setEndValue(float(self._target))
        self._anim.start()

    def _on_anim_value(self, value):

        self._center = float(value)
        self._relayout()

    def _normalize(self):
        """Evita os números crescerem pra sempre depois de muitas
        voltas — só reduz módulo o tamanho do anel."""

        if self._ring <= 0:
            return

        base = (self._target // self._ring) * self._ring
        self._center -= base
        self._target -= base

    def wheelEvent(self, event):

        delta = event.angleDelta().y() or event.angleDelta().x()

        self._wheel += delta
        steps = int(self._wheel / 120)

        if steps:
            self._wheel -= steps * 120
            self.go(-steps)

        event.accept()

    def keyPressEvent(self, event):

        if event.key() == Qt.Key.Key_Left:
            self.go(-1)
        elif event.key() == Qt.Key.Key_Right:
            self.go(1)
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):

        self.setFocus()

        if event.button() == Qt.MouseButton.LeftButton:

            item = self.itemAt(event.position().toPoint())

            while item is not None and item not in self._slots:
                item = item.parentItem()

            if item is not None:

                offset = self._signed_offset(self._slots.index(item))

                # Clicou num card do lado: traz ele pro centro em
                # vez de apertar botões de um card que está "longe".
                if abs(offset) > 0.35:
                    self.go_to(round(self._center + offset))
                    event.accept()
                    return

        super().mousePressEvent(event)


class HorizontalScrollArea(QScrollArea):
    """QScrollArea que converte o scroll vertical do mouse em
    scroll horizontal — usado no modo de exibição em fileira."""

    def wheelEvent(self, event):

        delta = event.angleDelta().y() or event.angleDelta().x()

        if delta:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta)
            event.accept()
            return

        super().wheelEvent(event)


# ---- ajuda (conteúdo da janela de ajuda, um conjunto por idioma) ----

HELP_TOPICS = {
    "pt": [
        ("🚀 Primeiros passos", """
<h2>Primeiros passos</h2>
<p>O UltimaLauncher é um launcher pessoal de jogos: você cadastra cada
jogo uma vez e abre todos de um lugar só, com capa, tempo jogado e
estatísticas.</p>
<ol>
<li>Clique em <b>+ Adicionar</b> na barra lateral.</li>
<li>Escolha o executável do jogo e o <i>runner</i> (Wine, Nativo ou
comando personalizado).</li>
<li>Se quiser, escolha uma capa e um ícone.</li>
<li>Salve e clique em <b>▶ Jogar</b> no card do jogo.</li>
</ol>
<p>Dica: aperte <b>F1</b> a qualquer momento para abrir esta ajuda.</p>
"""),
        ("🎮 Adicionando jogos", """
<h2>Adicionando e editando jogos</h2>
<p>No diálogo do jogo você preenche o nome, o executável e o
<b>runner</b>, que define como o jogo é aberto:</p>
<ul>
<li><b>Wine</b> — abre o .exe com o Wine. O campo <i>Wine Prefix</i> é
opcional; se ficar vazio, o launcher usa <code>~/.wine</code>.</li>
<li><b>Nativo</b> — executa o arquivo direto (jogos de Linux).</li>
<li><b>Comando personalizado</b> — roda o comando que você escrever.
Use <code>%EXE%</code> no lugar do caminho do executável. Exemplo:
<code>~/cxoffice/bin/wine "%EXE%"</code></li>
</ul>
<p>Se o executável não existir na hora de salvar, o launcher pergunta
se você quer salvar mesmo assim. Para mudar um jogo use
<b>✏ Editar</b> no card; para tirar da biblioteca use <b>🗑</b>.</p>
"""),
        ("🖼 Capas e ícones", """
<h2>Capas e ícones</h2>
<p>A <b>capa</b> é a imagem grande que preenche o card; o
<b>ícone</b> é o logo pequeno do jogo.</p>
<ul>
<li><b>Modo da capa</b>: <i>Preencher</i> corta as bordas sem
distorcer; <i>Esticar</i> mostra a imagem inteira, mas pode
distorcer.</li>
<li>Quando o jogo tem capa e ícone, dá para mostrar o ícone como um
selo sobre a capa. Também dá para esconder a imagem do card.</li>
<li><b>🎨 Usar ícone do executável</b> extrai o ícone do .exe
(precisa do Pillow e do icoextract no ambiente Python).</li>
<li><b>🖼 Procurar capa nos arquivos do jogo</b> procura uma imagem
de capa que já venha junto do executável. Se não achar, escolha uma
manualmente em <i>Capa</i>.</li>
<li>A capa escolhida é copiada para a pasta <code>capas</code> do
launcher, então apagar ou mover a imagem original não quebra o
card.</li>
</ul>
"""),
        ("📚 Biblioteca", """
<h2>Biblioteca</h2>
<ul>
<li>O campo <b>🔍 Procurar jogo...</b> filtra a lista pelo nome.</li>
<li>Há dois modos de exibição (<i>Configurações → Geral</i>):
<b>Grade</b>, com os jogos em colunas, e <b>Fileira</b>, um carrossel
infinito com o jogo do meio em destaque. Na fileira, navegue com a
roda do mouse, com as setas ← → do teclado ou clicando num card do
lado.</li>
<li>Para <b>reordenar</b>, ligue <i>Configurações → Cards →
Reordenar jogos arrastando os cards</i> e arraste os cards (só no modo
grade). Enquanto estiver ligado, os botões dos cards ficam
desativados.</li>
<li>Cada card mostra o runner, o tempo jogado e os botões Jogar,
Editar e remover.</li>
</ul>
"""),
        ("▶ Jogando", """
<h2>Jogando</h2>
<ul>
<li><b>▶ Jogar</b> abre o jogo de acordo com o runner escolhido.</li>
<li>Uma janelinha <i>Iniciando...</i> pode aparecer enquanto o jogo
abre. Ela não bloqueia o launcher e fecha sozinha (dá para desligar ou
mudar o tempo em <i>Configurações → Execução</i>).</li>
<li><b>Modo debug do Wine</b>: em <i>Configurações → Execução</i>, abre
os jogos de Wine dentro de um terminal, para você ver as mensagens do
Wine em tempo real. Requer xterm, gnome-terminal, konsole,
xfce4-terminal ou mate-terminal.</li>
<li>O tempo jogado e as sessões são registrados sozinhos e aparecem
nas Estatísticas.</li>
</ul>
"""),
        ("🧬 Monitor de processos", """
<h2>Monitor de processos</h2>
<p>O botão <b>🧬 Processos</b> lista o que o launcher abriu para
rodar seus jogos, inclusive processos do Wine que continuam vivos
depois que o jogo fechou. A lista se atualiza a cada 2 segundos.</p>
<ul>
<li><b>⏹ Encerrar sessão selecionada</b> encerra todos os processos
daquele jogo de uma vez.</li>
<li><b>✖ Encerrar só este processo</b> encerra apenas o processo
selecionado.</li>
</ul>
<p>Se houver <code>rustc</code> instalado, o launcher compila
sozinho um pequeno auxiliar em Rust para esse monitor. É totalmente
opcional: sem Rust, nada deixa de funcionar.</p>
"""),
        ("📊 Estatísticas", """
<h2>Estatísticas</h2>
<p>A aba <b>📊 Estatísticas</b> mostra o tempo total jogado, o jogo
mais jogado, a maior sessão e o total de sessões, além de um ranking
dos jogos por tempo jogado.</p>
<p><b>🍕 Ver gráfico</b> abre um gráfico de pizza com a porcentagem de
cada jogo; os menores são agrupados em <i>Outros</i>. Os dados ficam
no arquivo <code>dados/sessions.json</code>.</p>
"""),
        ("⚙ Configurações", """
<h2>Configurações</h2>
<ul>
<li><b>🖥 Geral</b> — modo de exibição, idioma, exportar/importar
tema e atualizar o launcher a partir de um novo launcher.py.</li>
<li><b>🎨 Aparência</b> — cor de destaque, imagem de fundo e
opacidade.</li>
<li><b>🃏 Cards</b> — cor, opacidade e borda dos cards, e o modo de
reordenar.</li>
<li><b>🔘 Botões</b> — cores, borda e cor ao passar o mouse.</li>
<li><b>🚀 Execução</b> — janela de carregamento e modo debug do
Wine.</li>
<li><b>🔌 Integrações</b> — Rich Presence do Discord e chave do
SteamGridDB.</li>
<li><b>🔊 Sons</b> — sons do launcher.</li>
</ul>
<p>As mudanças valem ao clicar em <b>Aplicar</b>. A troca de
<b>idioma</b> só vale depois de reabrir o launcher.</p>
<p>Em <i>Geral → Tema</i> você exporta as cores, opacidades e a imagem
de fundo atuais para um arquivo .json e pode importá-lo depois, em
outra instalação.</p>
"""),
        ("🔌 Discord e SteamGridDB", """
<h2>Discord e SteamGridDB</h2>
<p>Com o <b>Rich Presence</b> ligado, o seu perfil do Discord mostra
qual jogo você está jogando (e por qual runner) ou, sem jogo aberto,
quantos jogos há na biblioteca. É preciso estar com o
<b>Discord Desktop</b> aberto (o navegador não conta) e ter o pacote
<code>pypresence</code> instalado.</p>
<p>Para mostrar a <b>imagem do jogo</b>, o launcher usa o SteamGridDB:</p>
<ol>
<li>Crie uma chave em steamgriddb.com → Preferências → API.</li>
<li>Cole em <i>Configurações → Integrações</i> e use <b>🔍 Testar</b>
para conferir.</li>
<li>Se a imagem vier errada, abra <b>✏ Editar</b> no jogo e use
<b>Escolher imagem...</b>. Para voltar ao normal, use
<b>↩ Voltar ao automático</b>.</li>
</ol>
<p>Cada pessoa precisa da própria chave, que fica salva só no seu
computador.</p>
"""),
        ("🔊 Sons", """
<h2>Sons</h2>
<p>O launcher toca sons em quatro eventos: clique em botões, iniciar
jogo, excluir jogo e confirmar/aplicar.</p>
<ul>
<li>Por padrão, cada evento toca o arquivo com o nome dele dentro da
pasta <code>sons</code> (por exemplo, <code>sons/click.mp3</code>).
Sem o arquivo, o evento fica mudo.</li>
<li>Em <i>Configurações → Sons</i> você liga/desliga cada evento,
escolhe um arquivo próprio, ajusta o volume (geral e por evento) e
usa <b>▶ Testar</b>.</li>
<li>Os sons dependem do pacote QtMultimedia do PySide6. Sem ele, o
resto do launcher funciona normalmente.</li>
</ul>
"""),
        ("🖥 Atalho e arquivos", """
<h2>Atalho e arquivos</h2>
<p><b>🖥 Criar atalho</b> adiciona o launcher ao menu de aplicativos e
à área de trabalho (ela pode pedir para você confiar no atalho na
primeira vez).</p>
<p>Tudo fica na pasta <code>~/UltimaLauncher</code>:</p>
<ul>
<li><code>dados</code> — games.json (jogos e configurações) e
sessions.json (tempo jogado)</li>
<li><code>capas</code>, <code>icones</code>, <code>imagens</code> —
imagens usadas pelos jogos e pelo tema</li>
<li><code>sons</code> — sons personalizados</li>
<li><code>logs</code> — um log por execução que teve erro</li>
<li><code>rust</code> — auxiliares opcionais em Rust</li>
</ul>
<p>Para fazer backup, copie essa pasta. Para abrir sem a tela de
abertura (útil no terminal), defina a variável de ambiente
<code>ULTIMA_NO_SPLASH=1</code>.</p>
"""),
        ("🛠 Problemas comuns", """
<h2>Problemas comuns</h2>
<ul>
<li><b>O jogo não abre</b> — ligue o modo debug em <i>Configurações →
Execução</i> para ver as mensagens do Wine, confira o runner e o Wine
Prefix, e olhe a pasta <code>~/UltimaLauncher/logs</code>.</li>
<li><b>Ficaram processos do Wine rodando</b> — abra
<b>🧬 Processos</b> e encerre a sessão.</li>
<li><b>Sem som</b> — confira se o QtMultimedia está instalado e se há
arquivos na pasta <code>sons</code>.</li>
<li><b>O ícone do .exe não é extraído</b> — instale Pillow e
icoextract, ou escolha uma imagem manualmente.</li>
<li><b>O Rich Presence não aparece</b> — confira se o Discord Desktop
está aberto, se o <code>pypresence</code> está instalado e se a opção
está ligada.</li>
<li><b>Imagem errada no Discord</b> — use <b>Escolher imagem...</b> na
edição do jogo.</li>
</ul>
"""),
    ],
    "en": [
        ("🚀 Getting started", """
<h2>Getting started</h2>
<p>UltimaLauncher is a personal game launcher: you register each game
once and open all of them from a single place, with cover art, play
time and statistics.</p>
<ol>
<li>Click <b>+ Add</b> in the sidebar.</li>
<li>Choose the game's executable and the <i>runner</i> (Wine, Native
or custom command).</li>
<li>Optionally, pick a cover and an icon.</li>
<li>Save, then click <b>▶ Play</b> on the game's card.</li>
</ol>
<p>Tip: press <b>F1</b> at any time to open this help.</p>
"""),
        ("🎮 Adding games", """
<h2>Adding and editing games</h2>
<p>In the game dialog you fill in the name, the executable and the
<b>runner</b>, which defines how the game is opened:</p>
<ul>
<li><b>Wine</b> — opens the .exe with Wine. The <i>Wine Prefix</i>
field is optional; if left empty, the launcher uses
<code>~/.wine</code>.</li>
<li><b>Native</b> — runs the file directly (Linux games).</li>
<li><b>Custom command</b> — runs the command you write. Use
<code>%EXE%</code> where the executable path should go. Example:
<code>~/cxoffice/bin/wine "%EXE%"</code></li>
</ul>
<p>If the executable doesn't exist when you save, the launcher asks
whether you want to save anyway. To change a game use <b>✏ Edit</b>
on its card; to remove it from the library use <b>🗑</b>.</p>
"""),
        ("🖼 Covers and icons", """
<h2>Covers and icons</h2>
<p>The <b>cover</b> is the large image that fills the card; the
<b>icon</b> is the game's small logo.</p>
<ul>
<li><b>Cover mode</b>: <i>Fill</i> crops the edges without
distortion; <i>Stretch</i> shows the whole image but may distort
it.</li>
<li>When a game has both a cover and an icon, you can show the icon
as a badge over the cover. You can also hide the image on the
card.</li>
<li><b>🎨 Use the executable's icon</b> extracts the icon from the
.exe (needs Pillow and icoextract in the Python environment).</li>
<li><b>🖼 Look for a cover in the game files</b> searches for a cover
image that already ships next to the executable. If it finds nothing,
pick one manually under <i>Cover</i>.</li>
<li>The chosen cover is copied into the launcher's
<code>capas</code> folder, so deleting or moving the original image
won't break the card.</li>
</ul>
"""),
        ("📚 Library", """
<h2>Library</h2>
<ul>
<li>The <b>🔍 Search game...</b> box filters the list by name.</li>
<li>There are two display modes (<i>Settings → General</i>):
<b>Grid</b>, with the games in columns, and <b>Row</b>, an infinite
carousel with the middle game highlighted. In row mode, navigate with
the mouse wheel, the ← → arrow keys, or by clicking a card on the
side.</li>
<li>To <b>reorder</b>, turn on <i>Settings → Cards → Reorder games by
dragging the cards</i> and drag the cards (grid mode only). While it
is on, the card buttons are disabled.</li>
<li>Each card shows the runner, the play time and the Play, Edit and
remove buttons.</li>
</ul>
"""),
        ("▶ Playing", """
<h2>Playing</h2>
<ul>
<li><b>▶ Play</b> opens the game according to the chosen runner.</li>
<li>A small <i>Starting...</i> window may appear while the game opens.
It doesn't block the launcher and closes by itself (you can turn it
off or change the time in <i>Settings → Execution</i>).</li>
<li><b>Wine debug mode</b>: in <i>Settings → Execution</i>, opens Wine
games inside a terminal so you can follow Wine's messages in real
time. Requires xterm, gnome-terminal, konsole, xfce4-terminal or
mate-terminal.</li>
<li>Play time and sessions are recorded automatically and show up in
Statistics.</li>
</ul>
"""),
        ("🧬 Process monitor", """
<h2>Process monitor</h2>
<p>The <b>🧬 Processes</b> button lists what the launcher opened to run
your games, including Wine processes that stay alive after the game
has closed. The list refreshes every 2 seconds.</p>
<ul>
<li><b>⏹ Stop selected session</b> stops all processes of that game at
once.</li>
<li><b>✖ Stop only this process</b> stops just the selected
process.</li>
</ul>
<p>If <code>rustc</code> is installed, the launcher compiles a small
Rust helper for this monitor by itself. It is entirely optional:
without Rust, nothing stops working.</p>
"""),
        ("📊 Statistics", """
<h2>Statistics</h2>
<p>The <b>📊 Statistics</b> tab shows total play time, the most played
game, the longest session and the total number of sessions, plus a
ranking of games by play time.</p>
<p><b>🍕 View chart</b> opens a pie chart with each game's share; the
smallest ones are grouped under <i>Others</i>. The data is stored in
the file <code>dados/sessions.json</code>.</p>
"""),
        ("⚙ Settings", """
<h2>Settings</h2>
<ul>
<li><b>🖥 General</b> — display mode, language, export/import theme,
and updating the launcher from a new launcher.py.</li>
<li><b>🎨 Appearance</b> — accent color, background image and
opacity.</li>
<li><b>🃏 Cards</b> — card color, opacity and border, plus the
reorder mode.</li>
<li><b>🔘 Buttons</b> — colors, border and hover color.</li>
<li><b>🚀 Execution</b> — loading window and Wine debug mode.</li>
<li><b>🔌 Integrations</b> — Discord Rich Presence and SteamGridDB
key.</li>
<li><b>🔊 Sounds</b> — launcher sounds.</li>
</ul>
<p>Changes take effect when you click <b>Apply</b>. Changing the
<b>language</b> only takes effect after reopening the launcher.</p>
<p>Under <i>General → Theme</i> you can export the current colors,
opacities and background image to a .json file and import it later,
on another installation.</p>
"""),
        ("🔌 Discord and SteamGridDB", """
<h2>Discord and SteamGridDB</h2>
<p>With <b>Rich Presence</b> on, your Discord profile shows which game
you're playing (and through which runner) or, with no game open, how
many games are in the library. <b>Discord Desktop</b> must be open
(the browser doesn't count) and the <code>pypresence</code> package
must be installed.</p>
<p>To show the <b>game image</b>, the launcher uses SteamGridDB:</p>
<ol>
<li>Create a key at steamgriddb.com → Preferences → API.</li>
<li>Paste it in <i>Settings → Integrations</i> and use
<b>🔍 Test</b> to check it.</li>
<li>If the image is wrong, open <b>✏ Edit</b> on the game and use
<b>Choose image...</b>. To go back to normal, use
<b>↩ Back to automatic</b>.</li>
</ol>
<p>Everyone needs their own key, which is saved only on your
computer.</p>
"""),
        ("🔊 Sounds", """
<h2>Sounds</h2>
<p>The launcher plays sounds on four events: clicking buttons,
starting a game, deleting a game, and confirm/apply.</p>
<ul>
<li>By default, each event plays the file with its own name inside the
<code>sons</code> folder (for example, <code>sons/click.mp3</code>).
Without the file, the event stays silent.</li>
<li>In <i>Settings → Sounds</i> you can turn each event on/off, pick
your own file, adjust the volume (master and per event) and use
<b>▶ Test</b>.</li>
<li>Sounds depend on PySide6's QtMultimedia package. Without it, the
rest of the launcher works normally.</li>
</ul>
"""),
        ("🖥 Shortcut and files", """
<h2>Shortcut and files</h2>
<p><b>🖥 Create shortcut</b> adds the launcher to the application menu
and to the desktop (it may ask you to trust the shortcut the first
time).</p>
<p>Everything lives in the <code>~/UltimaLauncher</code> folder:</p>
<ul>
<li><code>dados</code> — games.json (games and settings) and
sessions.json (play time)</li>
<li><code>capas</code>, <code>icones</code>, <code>imagens</code> —
images used by the games and the theme</li>
<li><code>sons</code> — custom sounds</li>
<li><code>logs</code> — one log per run that had an error</li>
<li><code>rust</code> — optional Rust helpers</li>
</ul>
<p>To back up, copy that folder. To start without the opening screen
(handy in a terminal), set the environment variable
<code>ULTIMA_NO_SPLASH=1</code>.</p>
"""),
        ("🛠 Troubleshooting", """
<h2>Troubleshooting</h2>
<ul>
<li><b>The game won't open</b> — turn on debug mode in
<i>Settings → Execution</i> to see Wine's messages, check the runner
and the Wine Prefix, and look in the
<code>~/UltimaLauncher/logs</code> folder.</li>
<li><b>Wine processes were left running</b> — open
<b>🧬 Processes</b> and stop the session.</li>
<li><b>No sound</b> — check that QtMultimedia is installed and that
there are files in the <code>sons</code> folder.</li>
<li><b>The .exe icon isn't extracted</b> — install Pillow and
icoextract, or pick an image manually.</li>
<li><b>Rich Presence doesn't show</b> — check that Discord Desktop is
open, that <code>pypresence</code> is installed and that the option is
on.</li>
<li><b>Wrong image on Discord</b> — use <b>Choose image...</b> when
editing the game.</li>
</ul>
"""),
    ],
}


def help_topics():
    """Tópicos de ajuda no idioma atual: lista de (título, html)."""
    return HELP_TOPICS.get(_LANG, HELP_TOPICS["pt"])


class HelpDialog(QDialog):
    """Janela de ajuda: tópicos à esquerda, texto à direita, com busca.
    Não é modal, então dá pra consultar enquanto usa o launcher."""

    def __init__(self, parent=None):

        super().__init__(parent)

        self.setWindowTitle(tr("Ajuda — {app}").format(app=APP_NAME))
        self.resize(860, 580)
        self.setMinimumSize(640, 400)

        self.topics = help_topics()
        self.plain_texts = [
            (title + " " + re.sub(r"<[^>]+>", " ", html)).lower()
            for title, html in self.topics
        ]

        layout = QVBoxLayout(self)

        body = QHBoxLayout()

        left = QVBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("Buscar na ajuda..."))
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.filter_topics)
        left.addWidget(self.search_input)

        self.topic_list = QListWidget()
        self.topic_list.setFixedWidth(230)
        for title, _html in self.topics:
            self.topic_list.addItem(title)
        self.topic_list.currentRowChanged.connect(self.show_topic)
        left.addWidget(self.topic_list, 1)

        body.addLayout(left)

        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)
        body.addWidget(self.viewer, 1)

        layout.addLayout(body, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()

        close_button = QPushButton(tr("Fechar"))
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)

        layout.addLayout(buttons)

        self.topic_list.setCurrentRow(0)

    def show_topic(self, row):

        if 0 <= row < len(self.topics):
            self.viewer.setHtml(self.topics[row][1])

    def filter_topics(self, text):

        query = text.strip().lower()
        first_visible = -1

        for row in range(self.topic_list.count()):
            visible = query in self.plain_texts[row]
            self.topic_list.item(row).setHidden(not visible)
            if visible and first_visible < 0:
                first_visible = row

        current = self.topic_list.currentRow()
        current_hidden = (
            current < 0 or self.topic_list.item(current).isHidden()
        )

        if current_hidden and first_visible >= 0:
            self.topic_list.setCurrentRow(first_visible)
        elif first_visible < 0:
            self.viewer.setHtml(
                "<p>" + tr("Nada encontrado.") + "</p>"
            )



class UltimaLauncher(QMainWindow):

    def __init__(self):

        super().__init__()

        self.games, self.settings = load_data()

        set_language(self.settings.get("language", "auto"))
        self.help_dialog = None

        if migrate_covers_to_folder(self.games):
            save_data(self.games, self.settings)

        self.sound_manager = SoundManager(self.settings)

        self._bg_cache_path = None
        self._bg_cache_size = None
        self._bg_cache_pixmap = None

        self.setWindowTitle(APP_NAME)
        self.resize(1100, 700)

        self.apply_window_icon()

        self.setup_ui()
        self.apply_theme()
        self.refresh_games()
        self.refresh_stats_tab()

        self.session_start_time = int(time.time())

        self.active_game_process = None
        self.active_game_name = None
        self.active_runner = None
        self.active_game_index = None
        self.active_session_start = None

        self.game_sessions = self.load_sessions()

        self.loading_dialog = None

        self.discord = DiscordPresence()

        self.steamgrid_cache = load_steamgrid_cache()
        self._steamgrid_pending = set()
        self._steamgrid_auth_warned = False
        self.steamgrid_signals = SteamGridSignals(self)
        self.steamgrid_signals.resolved.connect(self.on_steamgrid_resolved)

        self.setup_discord_presence()

        self._card_drag = None
        self._cancel_anims = []

        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.refresh_games)

        self.presence_timer = QTimer(self)
        self.presence_timer.timeout.connect(self.check_active_game)
        self.presence_timer.start(4000)

    def apply_window_icon(self):

        icon_path = self.settings.get("app_icon", "")

        icon = None

        if icon_path and Path(icon_path).exists():
            icon = QIcon(icon_path)

        # Sem ícone escolhido pelo usuário, usa o do próprio launcher
        # (procurado em vários lugares — veja load_app_icon).
        if icon is None or icon.isNull():
            icon = load_app_icon()

        if icon is not None:
            self.setWindowIcon(icon)
            QApplication.instance().setWindowIcon(icon)

    def build_sidebar_title(self):
        """Título da barra lateral: ícone do launcher + nome. Se o
        ícone não for encontrado/aberto, cai pro título de texto
        com o emoji, que sempre funciona."""

        icon = load_app_icon()
        pixmap = icon.pixmap(28, 28) if icon is not None else None

        if pixmap is None or pixmap.isNull():
            title = QLabel(f"🎮 {APP_NAME}")
            title.setWordWrap(True)
            title.setStyleSheet(
                "QLabel { font-size: 17px; font-weight: bold; "
                "border: none; background: transparent; }"
            )
            return title

        container = QWidget()
        container.setStyleSheet("background: transparent;")

        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        logo = QLabel()
        logo.setPixmap(pixmap)
        logo.setFixedSize(28, 28)
        logo.setStyleSheet("border: none; background: transparent;")
        row.addWidget(logo)

        name = QLabel(APP_NAME)
        name.setWordWrap(True)
        name.setStyleSheet(
            "QLabel { font-size: 17px; font-weight: bold; "
            "border: none; background: transparent; }"
        )
        row.addWidget(name, 1)

        return container

    def setup_ui(self):

        central = QWidget()
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---- barra lateral ----
        # Navegação (Biblioteca/Estatísticas) e os botões de ação
        # (Adicionar, Processos, Criar atalho, Configurações) iguais
        # à maioria dos launchers de jogo, em vez de uma fileira de
        # botões no topo.

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 18, 14, 18)
        sidebar_layout.setSpacing(4)

        sidebar_layout.addWidget(self.build_sidebar_title())
        sidebar_layout.addSpacing(20)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        def add_nav_button(label, tab_index):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.clicked.connect(
                lambda: self.main_tabs.setCurrentIndex(tab_index)
            )
            self.nav_group.addButton(button)
            sidebar_layout.addWidget(button)
            return button

        library_nav_button = add_nav_button(tr("📚 Biblioteca"), 0)
        add_nav_button(tr("📊 Estatísticas"), 1)

        library_nav_button.setChecked(True)

        sidebar_layout.addSpacing(16)

        nav_separator = QFrame()
        nav_separator.setFrameShape(QFrame.Shape.HLine)
        sidebar_layout.addWidget(nav_separator)
        sidebar_layout.addSpacing(16)

        add_button = QPushButton(tr("+ Adicionar"))
        add_button.clicked.connect(self.add_game)
        sidebar_layout.addWidget(add_button)

        processes_button = QPushButton(tr("🧬 Processos"))
        processes_button.setToolTip(
            tr("Ver e encerrar processos abertos pelo launcher — "
            "inclusive os que sobram do Wine depois que o jogo "
            "já fechou.")
        )
        processes_button.clicked.connect(self.open_process_monitor)
        sidebar_layout.addWidget(processes_button)

        shortcut_button = QPushButton(tr("🖥 Criar atalho"))
        shortcut_button.clicked.connect(self.create_desktop_entry)
        sidebar_layout.addWidget(shortcut_button)

        settings_button = QPushButton(tr("⚙ Configurações"))
        settings_button.clicked.connect(self.open_settings)
        sidebar_layout.addWidget(settings_button)

        help_button = QPushButton(tr("❓ Ajuda"))
        help_button.setToolTip(tr("Abre a ajuda do launcher (F1)."))
        help_button.clicked.connect(self.open_help)
        sidebar_layout.addWidget(help_button)

        help_shortcut = QShortcut(QKeySequence("F1"), self)
        help_shortcut.activated.connect(self.open_help)

        sidebar_layout.addStretch()

        root_layout.addWidget(sidebar)

        # conteúdo (abas sem a barra de abas nativa — a navegação
        # é feita pelos botões da barra lateral acima)

        content = QWidget()
        self.main_layout = QVBoxLayout(content)
        self.main_layout.setContentsMargins(20, 18, 20, 18)

        self.main_tabs = QTabWidget()
        self.main_tabs.tabBar().setVisible(False)
        self.main_layout.addWidget(self.main_tabs)

        root_layout.addWidget(content, 1)

        # ---- aba: biblioteca ----

        library_tab = QWidget()
        library_tab.setObjectName("libraryTab")

        library_layout = QVBoxLayout(library_tab)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("🔍 Procurar jogo..."))
        self.search.textChanged.connect(self.refresh_games)
        library_layout.addWidget(self.search)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.container = CardDropContainer(self)
        self.grid = QGridLayout(self.container)
        # AlignHCenter: a grade ocupa só a largura dos cards e fica
        # centralizada, sobrando margem igual dos dois lados.
        self.grid.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )
        self.grid.setSpacing(CARD_GRID_SPACING)

        self.scroll.setWidget(self.container)

        # Modo de exibição em fileira única (alternativa à grade):
        # os cards ficam numa linha só, esticados verticalmente e
        # roláveis na horizontal.
        self.row_scroll = HorizontalScrollArea()
        self.row_scroll.setWidgetResizable(True)
        self.row_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.row_container = CardDropContainer(self)
        self.row_layout = QHBoxLayout(self.row_container)
        self.row_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )
        self.row_layout.setSpacing(18)
        self.row_layout.setContentsMargins(4, 4, 4, 4)

        self.row_scroll.setWidget(self.row_container)

        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(self.scroll)
        self.view_stack.addWidget(self.row_scroll)

        # O modo fileira usa o carrossel (o row_scroll acima não é
        # mais exibido).
        self.carousel = CardCarousel(self)
        self.view_stack.addWidget(self.carousel)

        library_layout.addWidget(self.view_stack)

        self.main_tabs.addTab(library_tab, tr("📚 Biblioteca"))

        # ---- aba: estatísticas ----

        stats_tab = QWidget()
        stats_tab.setObjectName("statsTab")

        stats_outer = QVBoxLayout(stats_tab)
        stats_outer.setContentsMargins(0, 0, 0, 0)

        self.stats_scroll = QScrollArea()
        self.stats_scroll.setWidgetResizable(True)
        self.stats_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.stats_container = QWidget()
        self.stats_container.setObjectName("statsContainer")

        self.stats_layout = QVBoxLayout(self.stats_container)
        self.stats_layout.setContentsMargins(8, 12, 8, 12)
        self.stats_layout.setSpacing(14)
        self.stats_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.stats_scroll.setWidget(self.stats_container)

        stats_outer.addWidget(self.stats_scroll)

        self.main_tabs.addTab(stats_tab, tr("📊 Estatísticas"))

    # ---- tema ----

    def paintEvent(self, event):

        background = self.settings.get("background", "")

        if background and os.path.exists(background):

            size = self.size()

            if (
                self._bg_cache_path != background
                or self._bg_cache_size != size
                or self._bg_cache_pixmap is None
            ):

                pixmap = QPixmap(background)

                if not pixmap.isNull():

                    if (
                        pixmap.width() > BACKGROUND_CACHE_MAX.width()
                        or pixmap.height() > BACKGROUND_CACHE_MAX.height()
                    ):
                        pixmap = pixmap.scaled(
                            BACKGROUND_CACHE_MAX,
                            Qt.KeepAspectRatioByExpanding,
                            Qt.SmoothTransformation,
                        )

                    pixmap = pixmap.scaled(
                        size,
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation,
                    )

                    self._bg_cache_pixmap = pixmap
                    self._bg_cache_path = background
                    self._bg_cache_size = size

            if self._bg_cache_pixmap is not None:

                painter = QPainter(self)

                x = (
                    self.width() - self._bg_cache_pixmap.width()
                ) // 2
                y = (
                    self.height() - self._bg_cache_pixmap.height()
                ) // 2

                painter.drawPixmap(x, y, self._bg_cache_pixmap)

                opacity = int(
                    self.settings.get("background_opacity", 35)
                )
                overlay_alpha = int(255 * (100 - opacity) / 100)

                painter.fillRect(
                    self.rect(),
                    QColor(16, 16, 20, overlay_alpha),
                )
                return

        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(16, 16, 20))

    def apply_theme(self):

        accent = self.settings.get("accent", "#8b5cf6")

        card_color = self.settings.get(
            "card_color", DEFAULT_SETTINGS["card_color"],
        )
        card_opacity = int(
            self.settings.get(
                "card_opacity", DEFAULT_SETTINGS["card_opacity"],
            )
        )
        border_color = self.settings.get(
            "border_color", DEFAULT_SETTINGS["border_color"],
        )
        border_width = int(
            self.settings.get(
                "border_width", DEFAULT_SETTINGS["border_width"],
            )
        )
        border_radius = int(
            self.settings.get(
                "border_radius", DEFAULT_SETTINGS["border_radius"],
            )
        )

        card_background = hex_to_rgba(card_color, card_opacity)
        card_border = hex_to_rgba(border_color, 100)

        button_color = self.settings.get(
            "button_color", DEFAULT_SETTINGS["button_color"],
        )
        button_opacity = int(
            self.settings.get(
                "button_opacity", DEFAULT_SETTINGS["button_opacity"],
            )
        )
        button_text_color = self.settings.get(
            "button_text_color", DEFAULT_SETTINGS["button_text_color"],
        )
        button_border_color = self.settings.get(
            "button_border_color",
            DEFAULT_SETTINGS["button_border_color"],
        )
        button_border_width = int(
            self.settings.get(
                "button_border_width",
                DEFAULT_SETTINGS["button_border_width"],
            )
        )
        button_border_radius = int(
            self.settings.get(
                "button_border_radius",
                DEFAULT_SETTINGS["button_border_radius"],
            )
        )

        button_hover_color = (
            self.settings.get("button_hover_color", "") or accent
        )

        button_background = hex_to_rgba(button_color, button_opacity)
        button_hover = hex_to_rgba(button_hover_color, button_opacity)
        button_pressed = hex_to_rgba(
            button_hover_color, max(button_opacity - 20, 0),
        )

        self.setStyleSheet(
            f"""
            QMainWindow > QWidget {{ background: transparent; }}

            QTabWidget::pane {{ background: transparent; border: none; }}
            QTabBar {{ background: transparent; }}

            #libraryTab, #statsTab {{ background: transparent; }}

            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}

            #gameCard, #gameCardCover {{
                border: {border_width}px solid {card_border};
                border-radius: {border_radius}px;
            }}

            #gameCard {{
                background: {card_background};
            }}

            #gameCardCover {{
                background: transparent;
            }}

            #statSummaryCard {{
                background: {card_background};
                border: {border_width}px solid {card_border};
                border-radius: {border_radius}px;
            }}

            #statRow {{
                background: {card_background};
                border: {border_width}px solid {card_border};
                border-radius: {border_radius}px;
            }}

            #statsContainer {{ background: transparent; }}

            QPushButton {{
                background: {button_background};
                color: {button_text_color};
                border: {button_border_width}px solid {button_border_color};
                border-radius: {button_border_radius}px;
                padding: 8px 12px;
            }}

            QPushButton:hover {{ background: {button_hover}; }}
            QPushButton:pressed {{ background: {button_pressed}; }}
            QPushButton:disabled {{ color: #777; }}

            #sidebar {{
                background: {card_background};
                border: none;
                border-right: {border_width}px solid {card_border};
            }}

            #navButton {{
                background: transparent;
                border: none;
                border-radius: {button_border_radius}px;
                text-align: left;
                padding: 10px 12px;
                font-size: 14px;
            }}

            #navButton:hover {{ background: {button_hover}; }}

            #navButton:checked {{
                background: {accent};
                color: white;
                font-weight: bold;
            }}
            """
        )

    def clear_grid(self):

        pending = []

        while self.grid.count():

            item = self.grid.takeAt(0)
            widget = item.widget()

            if widget:
                widget.setParent(None)
                widget.deleteLater()
                pending.append(widget)

        if pending:
            QCoreApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete,
            )

    def clear_row(self):

        pending = []

        while self.row_layout.count():

            item = self.row_layout.takeAt(0)
            widget = item.widget()

            if widget:
                widget.setParent(None)
                widget.deleteLater()
                pending.append(widget)

        if pending:
            QCoreApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete,
            )

    # ---- jogos ----

    def refresh_games(self):

        query = self.search.text().strip().lower()
        games = []

        for index, game in enumerate(self.games):
            name = game.get("name", "").lower()
            if query in name:
                games.append((index, game))

        view_mode = self.settings.get("view_mode", "grade")

        if view_mode == "fileira":
            self.refresh_games_row(games)
        else:
            self.refresh_games_grid(games)

    def refresh_games_grid(self, games):

        self.carousel.clear()
        self.clear_grid()
        self.view_stack.setCurrentWidget(self.scroll)

        if not games:

            empty = QLabel(
                tr("Nenhum jogo encontrado.\n\n"
                "Clique em + Adicionar para começar.")
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                "QLabel { font-size: 18px; color: #999; "
                "padding: 80px; }"
            )
            self.grid.addWidget(empty, 0, 0)
            return

        # Mesma quantidade de colunas de antes (largura da janela
        # dividida por 280: 3 na janela, 4 em tela cheia etc.).
        columns = max(1, self.width() // 280)

        # Largura ÚNICA pra todos os cards: divide a área disponível
        # igualmente entre as colunas (entre 185 e 270px). Assim a
        # última coluna não fica mais estreita que as outras.
        available = self.scroll.viewport().width()

        if not self.scroll.verticalScrollBar().isVisible():
            available -= self.scroll.verticalScrollBar().sizeHint().width()

        if available < 200:
            available = self.width() - 40

        margins = self.grid.contentsMargins()
        available -= margins.left() + margins.right()

        card_width = (
            available - CARD_GRID_SPACING * (columns - 1)
        ) // columns
        card_width = max(185, min(CARD_WIDTH, card_width))

        for position, (index, game) in enumerate(games):
            row = position // columns
            column = position % columns
            card = GameCard(game, index, self)
            card.setFixedWidth(card_width)
            self.grid.addWidget(card, row, column)

    def refresh_games_row(self, games):

        self.view_stack.setCurrentWidget(self.carousel)

        available_height = self.carousel.viewport().height()

        if available_height <= 40:
            available_height = max(320, self.height() - 230)

        card_height = max(270, available_height - 30)

        self.carousel.set_games(games, card_height)

    # ---- estatísticas ----

    def refresh_stats_tab(self):

        pending = []

        while self.stats_layout.count():

            item = self.stats_layout.takeAt(0)
            widget = item.widget()

            if widget:
                widget.setParent(None)
                widget.deleteLater()
                pending.append(widget)
                continue

            inner = item.layout()

            if inner:
                while inner.count():
                    sub = inner.takeAt(0)
                    sub_widget = sub.widget()
                    if sub_widget:
                        sub_widget.setParent(None)
                        sub_widget.deleteLater()
                        pending.append(sub_widget)

        if pending:
            QCoreApplication.sendPostedEvents(
                None, QEvent.Type.DeferredDelete,
            )

        accent = self.settings.get("accent", "#8b5cf6")

        total_seconds = sum(
            g.get("total_playtime_seconds", 0)
            for g in self.games
        )
        total_sessions = sum(
            g.get("times_played", 0) for g in self.games
        )

        most_played = None

        if self.games:
            most_played = max(
                self.games,
                key=lambda g: g.get("total_playtime_seconds", 0),
            )

        longest_session = 0

        if self.games:
            longest_session = max(
                g.get("longest_session_seconds", 0)
                for g in self.games
            )

        if self.games:

            summary_row = QHBoxLayout()
            summary_row.setSpacing(12)

            summary_row.addWidget(
                StatSummaryCard(
                    "⏱",
                    format_playtime(total_seconds)
                    if total_seconds else "—",
                    tr("tempo total"),
                )
            )

            if (
                most_played
                and most_played.get("total_playtime_seconds", 0) > 0
            ):
                summary_row.addWidget(
                    StatSummaryCard(
                        "🎮",
                        most_played.get("name", "—"),
                        tr("mais jogado"),
                        sublabel=format_playtime(
                            most_played.get(
                                "total_playtime_seconds", 0
                            )
                        ),
                    )
                )
            else:
                summary_row.addWidget(
                    StatSummaryCard("🎮", "—", tr("mais jogado"))
                )

            summary_row.addWidget(
                StatSummaryCard(
                    "🏆",
                    format_playtime(longest_session)
                    if longest_session else "—",
                    tr("maior sessão"),
                )
            )

            summary_row.addWidget(
                StatSummaryCard(
                    "📊",
                    str(total_sessions) if total_sessions else "0",
                    tr("sessões totais"),
                )
            )

            self.stats_layout.addLayout(summary_row)

        if not self.games:

            empty = QLabel(
                tr("Nenhum jogo na biblioteca ainda.\n\n"
                "Adicione jogos para começar a acompanhar "
                "suas estatísticas.")
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(
                "color: #888; font-size: 14px; "
                "padding: 60px; background: transparent;"
            )
            self.stats_layout.addWidget(empty)
            return

        header_row = QHBoxLayout()
        header_row.setContentsMargins(4, 8, 4, 0)

        section = QLabel(tr("Por tempo jogado"))
        section.setStyleSheet(
            "font-size: 13px; font-weight: bold; "
            "color: #999; background: transparent;"
        )
        header_row.addWidget(section)
        header_row.addStretch()

        chart_btn = QPushButton(tr("🍕 Ver gráfico"))
        chart_btn.clicked.connect(self.show_pie_chart)
        header_row.addWidget(chart_btn)

        self.stats_layout.addLayout(header_row)

        sorted_games = sorted(
            self.games,
            key=lambda g: g.get("total_playtime_seconds", 0),
            reverse=True,
        )

        # Soma do tempo de todos os jogos: base da porcentagem.
        all_time = sum(
            g.get("total_playtime_seconds", 0) for g in self.games
        )

        for position, game in enumerate(sorted_games, start=1):

            total = game.get("total_playtime_seconds", 0)

            fraction = (total / all_time) if all_time > 0 else 0

            row = StatRow(game, fraction, accent, position)

            self.stats_layout.addWidget(row)

    def show_pie_chart(self):

        self.sound_manager.play("click")

        data = prepare_pie_data(self.games)

        if not data:
            QMessageBox.information(
                self,
                APP_NAME,
                tr("Nenhum jogo foi jogado ainda.\n\n"
                "As estatísticas aparecem depois que você "
                "jogar algo."),
            )
            return

        accent = self.settings.get("accent", "#8b5cf6")

        dialog = PieChartDialog(data, accent, self)
        dialog.exec()

    # ---- adicionar / editar / remover ----

    def add_game(self):

        self.sound_manager.play("click")

        dialog = GameDialog(parent=self)

        if dialog.exec():

            new_game = dialog.get_data()
            new_game.update(DEFAULT_GAME_STATS.copy())

            self.games.append(new_game)

            save_data(self.games, self.settings)

            self.refresh_games()
            self.refresh_stats_tab()
            self.refresh_idle_presence_if_idle()

    def edit_game(self, index):

        if index >= len(self.games):
            return

        self.sound_manager.play("click")

        dialog = GameDialog(self.games[index], self)

        if dialog.exec():

            updated_game = dialog.get_data()

            for key in DEFAULT_GAME_STATS:
                updated_game[key] = self.games[index].get(
                    key, DEFAULT_GAME_STATS[key],
                )

            old_cover = self.games[index].get("cover", "")

            self.games[index] = updated_game

            if old_cover != updated_game.get("cover", ""):
                release_cover(old_cover, self.games)

            save_data(self.games, self.settings)

            self.refresh_games()
            self.refresh_stats_tab()

            if (
                self.active_game_name
                and self.active_game_index == index
            ):
                self.update_playing_presence(
                    self.active_game_name, self.active_runner,
                )

    def remove_game(self, index):

        if index >= len(self.games):
            return

        self.sound_manager.play("click")

        name = self.games[index].get("name", tr("este jogo"))

        answer = QMessageBox.question(
            self, APP_NAME,
            tr('Remover "{name}" da biblioteca?').format(name=name),
        )

        if answer == QMessageBox.StandardButton.Yes:

            removed = self.games.pop(index)
            release_cover(removed.get("cover", ""), self.games)

            save_data(self.games, self.settings)
            self.sound_manager.play("delete")

            self.refresh_games()
            self.refresh_stats_tab()
            self.refresh_idle_presence_if_idle()

    # ---- reordenar arrastando (com preview ao vivo) ----

    def visible_game_cards(self):
        """Cards que estão na tela agora, na ordem do layout."""

        if self.settings.get("view_mode", "grade") == "fileira":
            layout = self.row_layout
        else:
            layout = self.grid

        cards = []

        for position in range(layout.count()):
            widget = layout.itemAt(position).widget()
            if isinstance(widget, GameCard):
                cards.append(widget)

        return cards

    def begin_card_drag(self, card):
        """Chamado quando o arrasto começa: guarda a posição original
        (\"slot\") de cada card pra poder animar os outros."""

        self._cancel_anims = []

        cards = self.visible_game_cards()

        if card not in cards:
            self._card_drag = None
            return

        self._card_drag = {
            "cards": cards,
            "slots": [c.geometry() for c in cards],
            "order": list(cards),
            "source": card,
            "current": cards.index(card),
            "anims": {},
            "pending": None,
        }

        card.set_drag_ghost(True)

    def _animate_card(self, card, target, duration=180):

        anims = self._card_drag["anims"]

        old = anims.get(card)

        if old is not None:
            old.stop()

        if card.pos() == target:
            return

        animation = QPropertyAnimation(card, b"pos")
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(card.pos())
        animation.setEndValue(target)
        animation.start()

        # Sem pai de propósito: a referência no dicionário é o que
        # mantém a animação viva.
        anims[card] = animation

    def card_drag_over(self, event, pos):
        """Mouse passando por cima de um card (ou do fundo) durante
        o arrasto: descobre em qual \"slot\" ele está e anima os
        outros cards pra abrir espaço — o resultado final já fica
        visível antes de soltar."""

        state = self._card_drag

        if state is None or not event.mimeData().hasFormat(
            REORDER_MIME_TYPE
        ):
            event.ignore()
            return

        event.acceptProposedAction()

        self._autoscroll_during_drag(pos)

        target = None

        for position, rect in enumerate(state["slots"]):
            if rect.contains(pos):
                target = position
                break

        # Mouse num vão entre cards, ou ainda no mesmo slot.
        if target is None or target == state["current"]:
            return

        state["current"] = target

        others = [c for c in state["cards"] if c is not state["source"]]
        others.insert(target, state["source"])
        state["order"] = others

        for slot, card in zip(state["slots"], others):
            self._animate_card(card, slot.topLeft())

    def _autoscroll_during_drag(self, pos):
        """Rola a lista quando o card é arrastado perto da borda."""

        if self.settings.get("view_mode", "grade") == "fileira":
            area = self.row_scroll
            container = self.row_container
            bar = area.horizontalScrollBar()
            point = container.mapTo(area.viewport(), pos).x()
            limit = area.viewport().width()
        else:
            area = self.scroll
            container = self.container
            bar = area.verticalScrollBar()
            point = container.mapTo(area.viewport(), pos).y()
            limit = area.viewport().height()

        margin = 50
        step = 18

        if point < margin:
            bar.setValue(bar.value() - step)
        elif point > limit - margin:
            bar.setValue(bar.value() + step)

    def card_drag_drop(self, event):
        """Soltou o card. Aqui só ANOTA o que fazer — recriar os
        cards dentro do próprio evento de drop é o que travava o
        launcher. A reordenação de verdade roda em finish_card_drag,
        depois que o arrasto terminou por completo."""

        state = self._card_drag

        if state is None or not event.mimeData().hasFormat(
            REORDER_MIME_TYPE
        ):
            event.ignore()
            return

        event.acceptProposedAction()

        source = state["source"]
        final_position = state["order"].index(source)

        if state["cards"][final_position] is not source:
            state["pending"] = (
                source.index,
                state["cards"][final_position].index,
            )

    def finish_card_drag(self):
        """Fim do arrasto (soltou ou cancelou). Chamado de fora do
        drop, depois do drag.exec() retornar."""

        state = self._card_drag
        self._card_drag = None

        if state is None:
            return

        for animation in state["anims"].values():
            animation.stop()

        state["source"].set_drag_ghost(False)

        pending = state["pending"]

        if pending is not None:

            # Deixa cada card exatamente no lugar final, pra não
            # dar "piscada" quando a lista for recriada.
            for slot, card in zip(state["slots"], state["order"]):
                card.move(slot.topLeft())

            QTimer.singleShot(
                0, lambda: self.reorder_game(pending[0], pending[1]),
            )
            return

        # Soltou fora de qualquer lugar válido (ou no mesmo lugar):
        # os cards deslizam de volta pra posição original.
        self._card_drag = {
            "cards": state["cards"],
            "slots": state["slots"],
            "order": state["order"],
            "source": state["source"],
            "current": 0,
            "anims": {},
            "pending": None,
        }

        for card, slot in zip(state["cards"], state["slots"]):
            self._animate_card(card, slot.topLeft(), duration=150)

        self._cancel_anims = list(self._card_drag["anims"].values())
        self._card_drag = None

    def reorder_game(self, source_index, target_index):
        """Move o jogo de source_index pra onde target_index está
        (empurra os outros, não troca só os dois de lugar — igual
        reordenar uma playlist). Usado pelo arrastar-e-soltar dos
        cards quando "reorder_mode_enabled" está ligado."""

        if source_index == target_index:
            return

        if not (0 <= source_index < len(self.games)):
            return

        if not (0 <= target_index < len(self.games)):
            return

        game = self.games.pop(source_index)
        self.games.insert(target_index, game)

        save_data(self.games, self.settings)
        self.sound_manager.play("conf_and_apply")

        self.refresh_games()

    # ---- executar ----

    def launch_game(self, index):

        self.sound_manager.play("start_game")

        game = self.games[index]

        name = game.get("name", tr("Jogo"))
        exe = os.path.expanduser(game.get("exe", ""))
        runner = game.get("runner", "Wine")
        prefix = os.path.expanduser(game.get("prefix", ""))
        command = game.get("command", "")

        # Sempre resolve um WINEPREFIX explícito (mesmo quando o
        # campo fica em branco) e SEMPRE define a variável de
        # ambiente pro processo do Wine — antes, se o campo estava
        # vazio, a variável nunca era setada (o Wine resolvia o
        # padrão por conta própria), o que fazia o rastreio por
        # WINEPREFIX no monitor de processos nunca achar nada,
        # porque a variável simplesmente não existia no ambiente
        # dos processos.
        resolved_prefix = prefix or str(Path.home() / ".wine")

        if not os.path.isfile(exe):
            QMessageBox.warning(
                self, APP_NAME,
                tr("Executável não encontrado:\n\n{exe}").format(exe=exe),
            )
            return

        wine_debug = bool(
            self.settings.get("wine_debug_enabled", False)
        )
        loading_enabled = bool(
            self.settings.get("loading_window_enabled", True)
        )
        loading_seconds = int(
            self.settings.get("loading_window_seconds", 8)
        )

        if self.loading_dialog is not None:
            try:
                self.loading_dialog.close()
            except Exception:
                pass
            self.loading_dialog = None

        try:

            if runner == "Wine":

                if wine_debug:

                    parts = []

                    parts.append(
                        f"WINEPREFIX={shlex.quote(resolved_prefix)}"
                    )

                    parts.append("wine")
                    parts.append(shlex.quote(exe))

                    wine_cmd = " ".join(parts)

                    process = launch_in_terminal(
                        wine_cmd, cwd=str(Path(exe).parent),
                    )

                else:

                    env = os.environ.copy()

                    env["WINEPREFIX"] = resolved_prefix

                    process = subprocess.Popen(
                        ["wine", exe],
                        cwd=str(Path(exe).parent),
                        env=env,
                        start_new_session=True,
                    )

                    if loading_enabled:
                        self.loading_dialog = LoadingDialog(
                            name,
                            seconds=loading_seconds,
                            parent=self,
                        )
                        self.loading_dialog.show()

            elif runner == "Nativo":

                process = subprocess.Popen(
                    [exe], cwd=str(Path(exe).parent),
                    start_new_session=True,
                )

            elif runner == "Comando personalizado":

                final_command = command.replace(
                    "%EXE%", shlex.quote(exe),
                )

                process = subprocess.Popen(
                    final_command,
                    shell=True,
                    cwd=str(Path(exe).parent),
                    start_new_session=True,
                )

            else:
                process = None

            self.active_game_process = process
            self.active_game_name = name
            self.active_runner = runner
            self.active_game_index = index
            self.active_session_start = time.time()

            self.register_session(
                process, name, runner, resolved_prefix,
            )

            self.update_playing_presence(name, runner)

        except Exception as error:

            QMessageBox.critical(
                self, APP_NAME,
                tr("Erro ao iniciar {name}:\n\n{error}").format(
                    name=name, error=error
                ),
            )

    # ---- rich presence ----

    def setup_discord_presence(self):

        self.discord.configure(
            enabled=self.settings.get(
                "discord_rpc_enabled",
                DEFAULT_SETTINGS["discord_rpc_enabled"],
            ),
            client_id=DISCORD_CLIENT_ID,
        )

        if self.active_game_name:
            self.update_playing_presence(
                self.active_game_name, self.active_runner,
            )
        else:
            self.update_idle_presence()

    def update_idle_presence(self):

        self.discord.send(
            details=tr("No menu"),
            state=(
                tr("1 jogo na biblioteca")
                if len(self.games) == 1
                else tr("{n} jogos na biblioteca").format(
                    n=len(self.games)
                )
            ),
            large_image=DISCORD_LARGE_IMAGE_KEY,
            large_text=APP_NAME,
            start=self.session_start_time,
        )

    def update_playing_presence(self, name, runner):

        payload = {
            "details": tr("Jogando {name}").format(name=name),
            "state": tr("via {runner}").format(runner=tr(runner)),
            "large_image": DISCORD_LARGE_IMAGE_KEY,
            "large_text": APP_NAME,
            # Usa o início real da sessão: reenviar a presença (ex:
            # quando a imagem chega) não zera o tempo de jogo.
            "start": int(self.active_session_start or time.time()),
        }

        # Ícone do launcher fica grande; a imagem do jogo vai ao
        # lado, pequena.
        image = self.get_chosen_presence_image() or (
            self.get_presence_game_image(name)
        )

        if image:
            payload["small_image"] = image
            payload["small_text"] = name

        self.discord.send(**payload)

    # ---- imagem do jogo (SteamGridDB) ----

    def get_chosen_presence_image(self):
        """Imagem que o usuário escolheu à mão pro jogo em execução
        (vale mais que a busca automática e não precisa de chave)."""

        index = self.active_game_index

        if index is None or not (0 <= index < len(self.games)):
            return ""

        return str(self.games[index].get("presence_image", "") or "")

    def steamgrid_key(self):
        return str(self.settings.get("steamgriddb_api_key", "") or "").strip()

    def get_presence_game_image(self, name):
        """URL da imagem do jogo, se já estiver no cache. Se ainda
        não tiver, dispara a busca numa thread (sem travar a janela)
        e devolve "" — a presença é reenviada quando a imagem chega."""

        api_key = self.steamgrid_key()

        if not api_key or not self.settings.get(
            "discord_rpc_enabled", DEFAULT_SETTINGS["discord_rpc_enabled"],
        ):
            return ""

        name_key = (name or "").strip().lower()

        if not name_key:
            return ""

        entry = self.steamgrid_cache.get(name_key)

        if isinstance(entry, dict):

            if entry.get("url"):
                return entry["url"]

            try:
                checked = float(entry.get("checked", 0))
            except (TypeError, ValueError):
                checked = 0

            if time.time() - checked < STEAMGRID_RETRY_SECONDS:
                return ""

        if name_key in self._steamgrid_pending:
            return ""

        self._steamgrid_pending.add(name_key)

        threading.Thread(
            target=self._steamgrid_worker,
            args=(name_key, name, api_key),
            daemon=True,
        ).start()

        return ""

    def _steamgrid_worker(self, name_key, name, api_key):

        url, status, detail = steamgrid_find_image(api_key, name)

        print(
            f"SteamGridDB [{name}]: {status}"
            + (f" — {detail}" if detail else f" — {url}")
        )

        self.steamgrid_signals.resolved.emit(name_key, url, status)

    def on_steamgrid_resolved(self, name_key, url, status):

        self._steamgrid_pending.discard(name_key)

        if status == "auth":

            if not self._steamgrid_auth_warned:
                self._steamgrid_auth_warned = True
                QMessageBox.warning(
                    self, APP_NAME,
                    tr("O SteamGridDB recusou a chave da API.\n\n"
                    "Confira a chave em Configurações → Integrações."),
                )

            return

        # Falha de rede: não guarda nada, tenta de novo na próxima.
        if status == "error":
            return

        self.steamgrid_cache[name_key] = {
            "url": url,
            "checked": time.time(),
        }
        save_steamgrid_cache(self.steamgrid_cache)

        if (
            url
            and self.active_game_name
            and self.active_game_name.strip().lower() == name_key
        ):
            self.update_playing_presence(
                self.active_game_name, self.active_runner,
            )

    def refresh_idle_presence_if_idle(self):

        if self.active_game_name is None:
            self.update_idle_presence()

    def check_active_game(self):

        if (
            self.active_game_process is not None
            and self.active_game_process.poll() is not None
        ):

            if self.loading_dialog is not None:
                try:
                    self.loading_dialog.close()
                except Exception:
                    pass
                self.loading_dialog = None

            self.finish_active_session()
            self.update_idle_presence()

        # Roda mesmo sem jogo ativo: processos do Wine podem
        # continuar vivos bem depois do jogo em si ter fechado.
        self.prune_dead_sessions()

    def finish_active_session(self):

        if (
            self.active_game_process is None
            or self.active_game_index is None
        ):
            return

        duration = 0

        if self.active_session_start:
            duration = int(
                time.time() - self.active_session_start
            )

        if self.active_game_index < len(self.games):

            game = self.games[self.active_game_index]

            game["times_played"] = (
                game.get("times_played", 0) + 1
            )
            game["total_playtime_seconds"] = (
                game.get("total_playtime_seconds", 0) + duration
            )
            game["longest_session_seconds"] = max(
                game.get("longest_session_seconds", 0),
                duration,
            )
            game["last_played"] = int(time.time())

            save_data(self.games, self.settings)

            self.refresh_games()
            self.refresh_stats_tab()

        self.active_game_process = None
        self.active_game_name = None
        self.active_runner = None
        self.active_game_index = None
        self.active_session_start = None

    # ---- processos (rastreio de sessões de jogo) ----
    # "Sessão" aqui não é o processo que rastreamos em
    # active_game_process — é o grupo de processos inteiro que
    # aquele lançamento criou (veja start_new_session=True no
    # launch_game). Por isso uma sessão continua existindo aqui
    # mesmo depois do jogo "fechar" pro resto do launcher: só some
    # de verdade quando prune_dead_sessions() confirmar que não
    # sobrou nenhum processo vivo naquele grupo.

    def load_sessions(self):

        if not SESSIONS_FILE.exists():
            return []

        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                return data
        except Exception as error:
            print(f"Não foi possível carregar sessions.json: {error}")

        return []

    def save_sessions(self):

        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as file:
                json.dump(
                    self.game_sessions, file, indent=4, ensure_ascii=False,
                )
        except Exception as error:
            print(f"Não foi possível salvar sessions.json: {error}")

    def register_session(self, process, name, runner, prefix=None):

        if process is None:
            return

        resolved_prefix = None

        if runner == "Wine":
            resolved_prefix = os.path.normpath(
                prefix or str(Path.home() / ".wine")
            )

        # start_new_session=True faz do PID do processo direto o
        # próprio id do grupo de processos inteiro.
        self.game_sessions.append({
            "pgid": process.pid,
            "name": name,
            "runner": runner,
            "prefix": resolved_prefix,
            "started_at": time.time(),
        })

        self.save_sessions()

    def prune_dead_sessions(self):
        """Remove da lista as sessões cujo grupo de processos já
        não tem mais ninguém vivo. Chamado periodicamente, então o
        arquivo de sessões nunca fica crescendo pra sempre."""

        alive = [
            session for session in self.game_sessions
            if list_session_pids(session)
        ]

        if len(alive) != len(self.game_sessions):
            self.game_sessions = alive
            self.save_sessions()

    def open_process_monitor(self):

        self.sound_manager.play("click")

        dialog = ProcessMonitorDialog(self, self)
        dialog.exec()

    # ---- configurações ----

    def open_settings(self):

        self.sound_manager.play("click")

        old_steamgrid_key = self.steamgrid_key()
        old_language = self.settings.get("language", "auto")

        dialog = SettingsDialog(self.settings, self)

        if dialog.exec():

            self.settings = dialog.get_settings()

            if self.settings.get("language", "auto") != old_language:
                # Mensagem nos dois idiomas: ainda estamos no idioma
                # antigo e o novo só vale depois de reabrir.
                QMessageBox.information(
                    self, APP_NAME,
                    "Idioma alterado. Reabra o launcher para aplicar.\n\n"
                    "Language changed. Reopen the launcher to apply it.",
                )

            # Chave nova: jogos que "não tinham imagem" podem ter
            # falhado por causa da chave antiga — tenta de novo.
            if self.steamgrid_key() != old_steamgrid_key:
                self._steamgrid_auth_warned = False
                self.steamgrid_cache = {
                    key: value
                    for key, value in self.steamgrid_cache.items()
                    if isinstance(value, dict) and value.get("url")
                }
                save_steamgrid_cache(self.steamgrid_cache)
            self.sound_manager.update_settings(self.settings)
            self.sound_manager.play("conf_and_apply")

            save_data(self.games, self.settings)

            self._bg_cache_path = None
            self._bg_cache_size = None
            self._bg_cache_pixmap = None

            self.apply_theme()
            self.refresh_games()
            self.refresh_stats_tab()
            self.setup_discord_presence()

    # ---- ajuda ----

    def open_help(self):

        self.sound_manager.play("click")

        if self.help_dialog is None:
            self.help_dialog = HelpDialog(self)

        self.help_dialog.show()
        self.help_dialog.raise_()
        self.help_dialog.activateWindow()

    # ---- ícone do atalho ----

    def choose_shortcut_icon(self):

        answer = QMessageBox.question(
            self, APP_NAME,
            tr("Deseja escolher um ícone personalizado para o atalho?"),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )

        if answer != QMessageBox.StandardButton.Yes:
            return self.settings.get("app_icon", "")

        file_path, _ = QFileDialog.getOpenFileName(
            self, tr("Escolher ícone do atalho"), str(Path.home()),
            tr("Imagens (*.png *.svg *.ico *.jpg *.jpeg *.bmp *.webp)"),
        )

        if not file_path:
            return self.settings.get("app_icon", "")

        stored_icon = self.store_shortcut_icon(file_path)

        if not stored_icon:
            return self.settings.get("app_icon", "")

        self.settings["app_icon"] = stored_icon

        save_data(self.games, self.settings)
        self.apply_window_icon()

        return stored_icon

    def store_shortcut_icon(self, source_path):

        source = Path(source_path)

        if not source.exists():
            return ""

        try:

            if source.suffix.lower() == ".ico":

                dest = ICON_DIR / f"{APP_SLUG}-icon.png"

                if not convert_ico_to_png(source, dest):
                    QMessageBox.warning(
                        self, APP_NAME,
                        tr("Não foi possível converter o ícone .ico.\n"
                        "Será usado o ícone padrão do sistema."),
                    )
                    return ""

                return str(dest)

            dest = (
                ICON_DIR
                / f"{APP_SLUG}-icon{source.suffix.lower()}"
            )

            shutil.copy(str(source), str(dest))

            return str(dest)

        except Exception as error:

            QMessageBox.warning(
                self, APP_NAME,
                tr("Não foi possível copiar o ícone:\n\n{error}").format(
                    error=error
                ),
            )
            return ""

    # ---- desktop entry ----

    def create_desktop_entry(self):

        self.sound_manager.play("click")

        icon_path = self.choose_shortcut_icon()

        if not icon_path:
            # Sem ícone escolhido: usa o do próprio launcher (achado
            # em qualquer um dos lugares de app_icon_candidates) e só
            # então o genérico do tema.
            icon_path = next(
                (str(path) for path in app_icon_candidates()), "",
            )

        icon_value = icon_path if icon_path else "applications-games"

        applications_dir = (
            Path.home() / ".local" / "share" / "applications"
        )
        applications_dir.mkdir(parents=True, exist_ok=True)

        desktop_file = applications_dir / f"{APP_SLUG}.desktop"

        install_script()

        # Cria o wrapper com a lógica de detecção de venv.
        try:
            wrapper_path = WRAPPER_PATH
            wrapper_path.write_text(
                build_wrapper_content(),
                encoding="utf-8",
            )
            wrapper_path.chmod(0o755)
        except Exception as error:
            QMessageBox.critical(
                self, APP_NAME,
                tr("Não foi possível criar o wrapper:\n\n{error}").format(
                    error=error
                ),
            )
            return

        content = f"""[Desktop Entry]
Name={APP_NAME}
Comment=Personal game launcher
Comment[pt_BR]=Launcher pessoal de jogos
Exec={shlex.quote(str(WRAPPER_PATH))}
Icon={icon_value}
Path={APP_DIR}
Terminal=false
Type=Application
Categories=Game;
StartupNotify=true
"""

        try:

            desktop_file.write_text(content, encoding="utf-8")
            desktop_file.chmod(0o755)

            self.remove_old_shortcut(applications_dir)

            desktop_paths = [
                tr("Atalho criado no menu de aplicativos.")
            ]

            desktop_dir = self.find_desktop_dir()

            if desktop_dir:

                desktop_dir.mkdir(parents=True, exist_ok=True)

                desktop_copy = desktop_dir / f"{APP_SLUG}.desktop"

                desktop_copy.write_text(content, encoding="utf-8")
                desktop_copy.chmod(0o755)

                self.remove_old_shortcut(desktop_dir)

                desktop_paths.append(
                    tr("Atalho criado na área de trabalho "
                    "(pode pedir pra confiar/executar na "
                    "primeira vez que clicar).")
                )

            self.refresh_desktop_database(applications_dir)

            desktop_paths.append(
                tr(
                    "Se o atalho ainda assim não abrir, rode "
                    "'bash {wrapper}' num terminal, ou confira "
                    "a pasta de logs ({logs}) pra ver o erro."
                ).format(wrapper=WRAPPER_PATH, logs=LOG_DIR)
            )

            QMessageBox.information(
                self, APP_NAME, "\n\n".join(desktop_paths),
            )

        except Exception as error:

            QMessageBox.critical(
                self, APP_NAME,
                tr("Não foi possível criar o aplicativo:\n\n{error}").format(
                    error=error
                ),
            )

    def remove_old_shortcut(self, directory):

        old_file = directory / "game-launcher.desktop"

        try:
            if old_file.exists():
                old_file.unlink()
        except OSError:
            pass

    def refresh_desktop_database(self, applications_dir):

        try:
            subprocess.run(
                ["update-desktop-database", str(applications_dir)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def find_desktop_dir(self):

        try:
            result = subprocess.run(
                ["xdg-user-dir", "DESKTOP"],
                capture_output=True,
                text=True,
                check=True,
            )

            path = Path(result.stdout.strip())

            if path and str(path) != str(Path.home()):
                return path

        except Exception:
            pass

        fallback = Path.home() / "Desktop"
        return fallback if fallback.exists() else None

    # ---- redimensionamento ----

    def resizeEvent(self, event):

        super().resizeEvent(event)
        self.resize_timer.start(150)

    # ---- fechamento ----

    def closeEvent(self, event):

        if self.loading_dialog is not None:
            try:
                self.loading_dialog.close()
            except Exception:
                pass
            self.loading_dialog = None

        self.finish_active_session()

        self.discord.clear()
        self.discord.disconnect()

        super().closeEvent(event)


# ---- main ----

def main():

    app = QApplication(sys.argv)

    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Kelos")

    # Splash de abertura (opcional: se a variável de ambiente
    # ULTIMA_NO_SPLASH estiver setada, pula a splash — útil pra
    # debug rápido pelo terminal).
    splash = None

    if not os.environ.get("ULTIMA_NO_SPLASH"):
        splash = BootSplash()
        splash.showFullScreen()
        app.processEvents()

    window = UltimaLauncher()

    # Só mostra a janela principal depois que a splash desaparecer.
    if splash is not None:
        splash._fade.finished.connect(window.show)
    else:
        window.show()

    if install_script():

        QMessageBox.information(
            window,
            APP_NAME,
            tr(
                "O launcher foi organizado numa pasta própria:\n\n"
                "{app_dir}\n\n"
                "Seus jogos, imagens e configurações foram movidos "
                "para lá.\n\n"
                "A partir de agora edite e execute o arquivo "
                "{script}, porque é esse que o atalho abre."
            ).format(app_dir=APP_DIR, script=SCRIPT_PATH),
        )

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
