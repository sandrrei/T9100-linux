#!/usr/bin/env python3
"""Web control panel for the A13 photo frame (Portuguese / English UI).

Minimal web server using only the Python standard library (no extra packages).
Open it from a phone or PC: http://<tablet-ip>/  or  http://a13-tablet.local/

Controls: screen on/off, brightness, schedule, next photo, slideshow interval,
photo mode (cobrir = fill / desfocado = blurred background), photo upload,
gallery with delete, status, reboot and power off.
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

# Paths (overridable with environment variables, useful for testing on a PC)
PORTA = int(os.environ.get("PAINEL_PORTA", "80"))
BACKLIGHT = Path(os.environ.get("PAINEL_BACKLIGHT", "/sys/class/backlight/backlight"))
BATERIA = Path(os.environ.get("PAINEL_BATERIA", "/sys/class/power_supply/axp20x-battery"))
TEMPERATURA = Path(os.environ.get("PAINEL_TEMP", "/sys/class/thermal/thermal_zone0/temp"))
FOTOS = Path(os.environ.get("PAINEL_FOTOS", "/srv/fotos"))
CACHE = Path(os.environ.get("PAINEL_CACHE", "/var/cache/moldura"))
SCRIPT = Path(os.environ.get("PAINEL_SCRIPT", "/usr/local/bin/moldura.sh"))
SYSTEMCTL = os.environ.get("PAINEL_SYSTEMCTL", "systemctl")
HORARIO = Path(os.environ.get("PAINEL_HORARIO", "/etc/moldura-horario.json"))

PASTA_ENVIOS = FOTOS / "Enviadas"     # uploads folder
MAX_ENVIO = 60 * 1024 * 1024          # per photo (streamed to disk, no memory cost)
EXTENSOES = {".jpg", ".jpeg", ".png"}


def m(pt, en):
    """A message in both languages; the page shows the one the user picked."""
    return {"pt": pt, "en": en}


class Erro(ValueError):
    """User-facing error with a bilingual message."""
    def __init__(self, pt, en):
        super().__init__(en)
        self.msg = m(pt, en)


# ---------------------------------------------------------------- helpers

def ler(caminho, omissao=""):
    try:
        return Path(caminho).read_text().strip()
    except OSError:
        return omissao


def escrever(caminho, valor):
    Path(caminho).write_text(f"{valor}\n")


def em_segundo_plano(*cmd):
    """Run a command without waiting (single-core A13: systemctl can be slow)."""
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def reiniciar_moldura():
    em_segundo_plano(SYSTEMCTL, "restart", "moldura")


def contar_fotos(pasta):
    total = 0
    for _raiz, _dirs, ficheiros in os.walk(pasta):
        total += sum(1 for f in ficheiros if Path(f).suffix.lower() in EXTENSOES)
    return total


def ler_script():
    """Read interval (first '-t N') and mode ('MODO=...') from moldura.sh."""
    texto = ler(SCRIPT)
    intervalo = re.search(r"-t (\d+)", texto)
    modo = re.search(r"MODO=(\w+)", texto)
    return texto, (int(intervalo.group(1)) if intervalo else None), (modo.group(1) if modo else None)


def gravar_script(texto):
    """Write a new file and swap it in (safe even while the script is running)."""
    novo = SCRIPT.with_name(SCRIPT.name + ".new")
    novo.write_text(texto)
    novo.chmod(0o755)
    novo.replace(SCRIPT)


def ip_local():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 80))
            return s.getsockname()[0]
    except OSError:
        return "?"


# ---------------------------------------------------------------- schedule

HORARIO_OMISSAO = {"ativo": False, "desligar": "23:00", "ligar": "08:00"}
HORA_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def ler_horario():
    try:
        dados = json.loads(HORARIO.read_text())
        return {**HORARIO_OMISSAO, **dados}
    except (OSError, ValueError):
        return dict(HORARIO_OMISSAO)


def minutos(hhmm):
    h, mm = hhmm.split(":")
    return int(h) * 60 + int(mm)


def periodo_desligado(horario, agora_min):
    """True if 'now' is between the off time and the on time (handles midnight)."""
    desl, lig = minutos(horario["desligar"]), minutos(horario["ligar"])
    if desl == lig:
        return False
    if desl < lig:
        return desl <= agora_min < lig
    return agora_min >= desl or agora_min < lig


def vigiar_horario():
    """Background thread: only touches the screen when entering or leaving the off period,
    so a manual 'on' at night lasts until the next scheduled time."""
    anterior = None
    while True:
        h = ler_horario()
        if h["ativo"]:
            t = time.localtime()
            agora = periodo_desligado(h, t.tm_hour * 60 + t.tm_min)
            if agora != anterior:
                try:
                    escrever(BACKLIGHT / "bl_power", 4 if agora else 0)
                except OSError:
                    pass
                anterior = agora
        else:
            anterior = None
        time.sleep(30)


# ---------------------------------------------------------------- actions

def estado():
    _texto, intervalo, modo = ler_script()
    uptime = float(ler("/proc/uptime", "0").split()[0] or 0)
    temp = ler(TEMPERATURA)
    return {
        "brilho": int(ler(BACKLIGHT / "brightness", "0") or 0),
        "brilho_max": int(ler(BACKLIGHT / "max_brightness", "10") or 10),
        "ecra_ligado": ler(BACKLIGHT / "bl_power", "0") == "0",
        "intervalo": intervalo,
        "modo": modo,
        "fotos": contar_fotos(FOTOS),
        "preparadas": contar_fotos(CACHE),
        "bateria": ler(BATERIA / "capacity", "?"),
        "bateria_estado": ler(BATERIA / "status", "?"),
        "temperatura": f"{int(temp) / 1000:.1f}" if temp.isdigit() else "?",
        "ligado_ha": f"{int(uptime // 3600)} h {int(uptime % 3600 // 60)} min",
        "ip": ip_local(),
        "horario": ler_horario(),
    }


def acao_brilho(dados):
    maximo = int(ler(BACKLIGHT / "max_brightness", "10") or 10)
    valor = max(0, min(maximo, int(dados.get("valor", 0))))
    escrever(BACKLIGHT / "brightness", valor)
    return m(f"Brilho: {valor}", f"Brightness: {valor}")


def acao_ecra(dados):
    ligar = bool(dados.get("ligar"))
    escrever(BACKLIGHT / "bl_power", 0 if ligar else 4)
    return m("Ecrã ligado", "Screen on") if ligar else m("Ecrã desligado", "Screen off")


def acao_seguinte(_dados):
    reiniciar_moldura()
    return m("A mudar de foto...", "Changing photo...")


def acao_intervalo(dados):
    segundos = max(3, min(3600, int(dados.get("segundos", 10))))
    texto, _i, _m = ler_script()
    if not re.search(r"-t \d+", texto):
        raise Erro("não encontrei o intervalo no moldura.sh", "interval not found in moldura.sh")
    gravar_script(re.sub(r"-t \d+", f"-t {segundos}", texto, count=1))
    reiniciar_moldura()
    return m(f"Intervalo: {segundos} s", f"Interval: {segundos} s")


def acao_modo(dados):
    modo = dados.get("modo")
    if modo not in ("cobrir", "desfocado"):
        raise Erro("modo inválido", "invalid mode")
    texto, _i, atual = ler_script()
    if modo == atual:
        return m("Esse modo já está ativo", "That mode is already active")
    gravar_script(re.sub(r"MODO=\w+", f"MODO={modo}", texto, count=1))
    for f in CACHE.glob("*.jpg"):
        f.unlink(missing_ok=True)
    reiniciar_moldura()
    return m("Modo alterado: a converter as fotos outra vez (10 a 20 min)",
             "Mode changed: converting the photos again (10 to 20 min)")


def acao_horario(dados):
    desligar, ligar = str(dados.get("desligar", "")), str(dados.get("ligar", ""))
    if not (HORA_RE.match(desligar) and HORA_RE.match(ligar)):
        raise Erro("horas inválidas (use HH:MM)", "invalid times (use HH:MM)")
    if desligar == ligar:
        raise Erro("as duas horas não podem ser iguais", "the two times cannot be the same")
    h = {"ativo": bool(dados.get("ativo")), "desligar": desligar, "ligar": ligar}
    novo = HORARIO.with_name(HORARIO.name + ".new")
    novo.write_text(json.dumps(h))
    novo.replace(HORARIO)
    if not h["ativo"]:
        return m("Horário desligado", "Schedule disabled")
    return m(f"Horário: desliga às {desligar}, liga às {ligar}",
             f"Schedule: off at {desligar}, on at {ligar}")


def acao_atualizar(_dados):
    reiniciar_moldura()
    return m("A preparar as fotos novas...", "Preparing the new photos...")


def acao_energia(dados):
    comando = dados.get("comando")
    if comando not in ("reboot", "poweroff"):
        raise Erro("comando inválido", "invalid command")
    em_segundo_plano("sh", "-c", f"sleep 2; {SYSTEMCTL} {comando}")
    if comando == "reboot":
        return m("A reiniciar...", "Restarting...")
    return m("A desligar...", "Shutting down...")


# ---------------------------------------------------------------- gallery

POR_PAGINA = 24
ID_RE = re.compile(r"^[0-9a-f]{16}$")


def todas_as_fotos():
    """All original photos. The id is the cache copy's name (same hash as prepara-fotos.sh)."""
    fotos = []
    for raiz, dirs, ficheiros in os.walk(FOTOS):
        dirs.sort()
        for f in sorted(ficheiros):
            if Path(f).suffix.lower() not in EXTENSOES:
                continue
            caminho = os.path.join(raiz, f)
            pasta = os.path.relpath(raiz, FOTOS)
            fotos.append({
                "id": hashlib.md5(os.fsencode(caminho)).hexdigest()[:16],
                "nome": f,
                "pasta": "" if pasta == "." else pasta,
                "caminho": caminho,
            })
    return fotos


def listar_fotos(pasta, pagina):
    fotos = todas_as_fotos()
    pastas = sorted({f["pasta"] for f in fotos})
    if pasta is not None:
        fotos = [f for f in fotos if f["pasta"] == pasta]
    paginas = max(1, -(-len(fotos) // POR_PAGINA))
    pagina = max(1, min(paginas, pagina))
    inicio = (pagina - 1) * POR_PAGINA
    return {
        "pastas": pastas,
        "total": len(fotos),
        "pagina": pagina,
        "paginas": paginas,
        "fotos": [
            {"id": f["id"], "nome": f["nome"], "pasta": f["pasta"],
             "preparada": (CACHE / f"{f['id']}.jpg").exists()}
            for f in fotos[inicio:inicio + POR_PAGINA]
        ],
    }


def acao_apagar(dados):
    ids = {i for i in dados.get("ids", []) if isinstance(i, str) and ID_RE.match(i)}
    if not ids:
        raise Erro("nenhuma foto selecionada", "no photo selected")
    raiz = os.path.realpath(FOTOS)
    apagadas = 0
    for f in todas_as_fotos():
        if f["id"] not in ids:
            continue
        if not os.path.realpath(f["caminho"]).startswith(raiz + os.sep):
            continue                                  # never delete outside /srv/fotos
        Path(f["caminho"]).unlink(missing_ok=True)
        (CACHE / f"{f['id']}.jpg").unlink(missing_ok=True)
        apagadas += 1
    if apagadas:
        reiniciar_moldura()
    return m(f"{apagadas} foto(s) apagada(s)", f"{apagadas} photo(s) deleted")


ACOES = {
    "apagar": acao_apagar,
    "brilho": acao_brilho,
    "ecra": acao_ecra,
    "seguinte": acao_seguinte,
    "intervalo": acao_intervalo,
    "modo": acao_modo,
    "atualizar": acao_atualizar,
    "horario": acao_horario,
    "energia": acao_energia,
}


# ---------------------------------------------------------------- upload

def nome_seguro(nome):
    nome = re.sub(r"[^\w.\- ]", "_", os.path.basename(nome or "")).strip(" .")
    return nome or "foto.jpg"


def guardar_envio(nome, entrada, tamanho):
    """Save a raw uploaded photo in 64 KB chunks (almost no memory used)."""
    nome = nome_seguro(nome)
    if Path(nome).suffix.lower() not in EXTENSOES:
        raise Erro("só são aceites fotos .jpg, .jpeg ou .png", "only .jpg, .jpeg or .png photos are accepted")
    PASTA_ENVIOS.mkdir(parents=True, exist_ok=True)
    destino = PASTA_ENVIOS / nome
    n = 1
    while destino.exists():
        destino = PASTA_ENVIOS / f"{Path(nome).stem}_{n}{Path(nome).suffix}"
        n += 1
    temporario = destino.with_name("." + destino.name + ".parcial")
    falta = tamanho
    try:
        with open(temporario, "wb") as f:
            while falta > 0:
                bloco = entrada.read(min(65536, falta))
                if not bloco:
                    raise OSError("upload interrupted")
                f.write(bloco)
                falta -= len(bloco)
        temporario.replace(destino)       # only appears in the folder once complete
    finally:
        temporario.unlink(missing_ok=True)
    return destino.name


# ---------------------------------------------------------------- page

PAGINA = """<!doctype html>
<html lang="pt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Moldura</title>
<style>
  :root { --fundo:#f4f2ee; --cartao:#fff; --texto:#222; --suave:#777; --acento:#2f6f62; --perigo:#a33; }
  @media (prefers-color-scheme: dark) {
    :root { --fundo:#16181a; --cartao:#212427; --texto:#eee; --suave:#999; --acento:#5fb3a1; --perigo:#e06c6c; }
  }
  * { box-sizing: border-box; }
  body { margin:0; padding:16px; background:var(--fundo); color:var(--texto);
         font:16px/1.4 system-ui, sans-serif; max-width:520px; margin-inline:auto; }
  header { display:flex; align-items:center; justify-content:space-between; margin:4px 0 16px; }
  h1 { font-size:1.4rem; margin:0; }
  #lingua { flex:0; min-width:0; padding:6px 12px; font-size:.9rem; background:transparent;
            color:var(--acento); border:1.5px solid var(--acento); }
  section { background:var(--cartao); border-radius:12px; padding:14px 16px; margin-bottom:12px; }
  h2 { font-size:.85rem; text-transform:uppercase; letter-spacing:.05em; color:var(--suave); margin:0 0 10px; }
  .linha { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  button { flex:1; min-width:110px; padding:12px; border:0; border-radius:10px; font-size:1rem;
           background:var(--acento); color:#fff; cursor:pointer; }
  button.sec { background:transparent; color:var(--acento); border:1.5px solid var(--acento); }
  button.perigo { background:transparent; color:var(--perigo); border:1.5px solid var(--perigo); }
  button.ativo { outline:3px solid var(--texto); }
  button:disabled { opacity:.4; cursor:default; }
  input[type=range] { width:100%; accent-color:var(--acento); }
  select, input[type=file], input[type=time] { width:100%; padding:10px; font-size:1rem; border-radius:10px;
           border:1px solid var(--suave); background:var(--cartao); color:var(--texto); }
  dl { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; margin:0; }
  dt { color:var(--suave); } dd { margin:0; }
  #msg { position:fixed; left:16px; right:16px; bottom:16px; max-width:488px; margin:auto;
         background:var(--texto); color:var(--fundo); padding:12px 16px; border-radius:10px;
         opacity:0; transition:opacity .3s; pointer-events:none; }
  #msg.ver { opacity:.95; }
  .grelha { display:grid; grid-template-columns:repeat(2, 1fr); gap:8px; }
  .foto { position:relative; aspect-ratio:5/3; border-radius:8px; overflow:hidden; cursor:pointer;
          background:var(--fundo); border:3px solid transparent; }
  .foto img { width:100%; height:100%; object-fit:cover; display:block; }
  .foto .nome { position:absolute; inset:auto 0 0 0; padding:2px 6px; font-size:.75rem;
                background:rgba(0,0,0,.55); color:#fff; white-space:nowrap; overflow:hidden;
                text-overflow:ellipsis; }
  .foto.sem-img { display:flex; align-items:center; justify-content:center; color:var(--suave);
                  font-size:.8rem; padding:6px; text-align:center; }
  .foto.sel { border-color:var(--perigo); }
  .foto.sel::after { content:"✓"; position:absolute; top:4px; right:6px; width:24px; height:24px;
                     border-radius:50%; background:var(--perigo); color:#fff; text-align:center;
                     line-height:24px; font-weight:bold; }
</style>
</head>
<body>
<header>
  <h1 data-t="titulo"></h1>
  <button id="lingua" onclick="mudarLingua()" aria-label="Language / Língua"></button>
</header>

<section>
  <h2 data-t="ecra"></h2>
  <div class="linha" style="margin-bottom:12px">
    <button id="ligar" onclick="api('ecra',{ligar:true})" data-t="ligar"></button>
    <button id="desligar" class="sec" onclick="api('ecra',{ligar:false})" data-t="desligar"></button>
  </div>
  <label for="brilho"><span data-t="brilho"></span>: <span id="brilho-v">-</span></label>
  <input id="brilho" type="range" min="0" max="10" step="1"
         oninput="document.getElementById('brilho-v').textContent=this.value"
         onchange="api('brilho',{valor:+this.value})">
</section>

<section>
  <h2 data-t="horario"></h2>
  <label class="linha" style="margin-bottom:10px">
    <input id="h-ativo" type="checkbox" style="width:22px;height:22px;accent-color:var(--acento)">
    <span data-t="h_auto"></span>
  </label>
  <div class="linha" style="margin-bottom:10px">
    <label style="flex:1"><span data-t="h_desligar"></span><br><input id="h-desligar" type="time" value="23:00"></label>
    <label style="flex:1"><span data-t="h_ligar"></span><br><input id="h-ligar" type="time" value="08:00"></label>
  </div>
  <div class="linha">
    <button onclick="guardarHorario()" data-t="h_guardar"></button>
  </div>
</section>

<section>
  <h2 data-t="fotos"></h2>
  <div class="linha" style="margin-bottom:12px">
    <button onclick="api('seguinte')" data-t="seguinte"></button>
  </div>
  <label for="intervalo" data-t="mudar_cada"></label>
  <select id="intervalo" onchange="api('intervalo',{segundos:+this.value})">
    <option value="5" data-t="s5"></option><option value="10" data-t="s10"></option>
    <option value="30" data-t="s30"></option><option value="60" data-t="m1"></option>
    <option value="300" data-t="m5"></option><option value="900" data-t="m15"></option>
  </select>
  <p style="margin:12px 0 6px" data-t="modo"></p>
  <div class="linha">
    <button id="m-cobrir" class="sec" onclick="mudarModo('cobrir')" data-t="cobrir"></button>
    <button id="m-desfocado" class="sec" onclick="mudarModo('desfocado')" data-t="desfocado"></button>
  </div>
</section>

<section>
  <h2 data-t="enviar_t"></h2>
  <input id="ficheiros" type="file" accept="image/jpeg,image/png" multiple>
  <div class="linha" style="margin-top:10px">
    <button onclick="enviar()" data-t="enviar"></button>
  </div>
  <p id="envio" style="color:var(--suave);margin:8px 0 0"></p>
</section>

<section>
  <h2 data-t="guardadas"></h2>
  <div class="linha" id="g-abrir">
    <button class="sec" onclick="abrirGaleria()" data-t="ver_apagar"></button>
  </div>
  <div id="galeria" hidden>
    <select id="g-pasta" onchange="carregarGaleria(1)"></select>
    <p id="g-info" style="color:var(--suave);margin:8px 0"></p>
    <div id="g-grelha" class="grelha"></div>
    <div class="linha" style="margin-top:10px">
      <button class="sec" id="g-ant" onclick="carregarGaleria(gal.pagina - 1)" data-t="anterior"></button>
      <button class="sec" id="g-seg" onclick="carregarGaleria(gal.pagina + 1)" data-t="proxima"></button>
    </div>
    <div class="linha" style="margin-top:10px">
      <button class="perigo" id="g-apagar" onclick="apagarSelecionadas()" disabled></button>
      <button class="sec" onclick="fecharGaleria()" data-t="fechar"></button>
    </div>
  </div>
</section>

<section>
  <h2 data-t="estado"></h2>
  <dl id="estado"></dl>
</section>

<section>
  <h2 data-t="energia"></h2>
  <div class="linha">
    <button class="sec" onclick="energia('reboot')" data-t="reiniciar"></button>
    <button class="perigo" onclick="energia('poweroff')" data-t="desligar_tablet"></button>
  </div>
</section>

<div id="msg"></div>

<script>
const TXT = {
  pt: {
    titulo: 'Moldura', ecra: 'Ecrã', ligar: 'Ligar', desligar: 'Desligar', brilho: 'Brilho',
    horario: 'Horário', h_auto: 'Desligar o ecrã automaticamente', h_desligar: 'Desligar às',
    h_ligar: 'Ligar às', h_guardar: 'Guardar horário',
    fotos: 'Fotos', seguinte: 'Foto seguinte', mudar_cada: 'Mudar a cada',
    s5: '5 segundos', s10: '10 segundos', s30: '30 segundos', m1: '1 minuto', m5: '5 minutos', m15: '15 minutos',
    modo: 'Modo', cobrir: 'Ecrã inteiro', desfocado: 'Fundo desfocado',
    enviar_t: 'Enviar fotos', enviar: 'Enviar',
    guardadas: 'Fotos guardadas', ver_apagar: 'Ver e apagar fotos',
    anterior: '‹ Anterior', proxima: 'Seguinte ›', fechar: 'Fechar',
    estado: 'Estado', energia: 'Energia', reiniciar: 'Reiniciar', desligar_tablet: 'Desligar tablet',
    sem_ligacao: 'Sem ligação ao tablet',
    conf_modo: 'Mudar o modo converte todas as fotos outra vez (10 a 20 minutos). Continuar?',
    conf_reboot: 'Reiniciar o tablet?', conf_poweroff: 'Desligar o tablet?',
    escolhe: 'Escolhe primeiro as fotos',
    a_enviar: (i, n, f) => `A enviar ${i} de ${n}: ${f}`,
    enviadas: (ok, n) => `${ok} de ${n} fotos enviadas.`,
    erro_envio: 'Erro no envio', falhou: f => `Falhou: ${f}`,
    st_fotos: 'Fotos', st_fotos_v: (p, n) => `${p} preparadas de ${n}`,
    st_bateria: 'Bateria', st_temp: 'Temperatura', st_ligado: 'Ligado há', st_ip: 'Endereço',
    bat: {Charging: 'a carregar', Discharging: 'a descarregar', 'Not charging': 'sem carregar',
          Full: 'carregada', Unknown: 'desconhecido'},
    todas: 'Todas as pastas', principal: '(pasta principal)',
    g_info: (t, p, ps) => `${t} fotos · página ${p} de ${ps}`, g_sel: n => ` · ${n} selecionada(s)`,
    a_preparar: '(a preparar)',
    apagar_sel: 'Apagar selecionadas', apagar_n: n => `Apagar ${n} selecionada(s)`,
    conf_apagar: n => `Apagar ${n} foto(s) do tablet? Isto não se pode desfazer.`,
  },
  en: {
    titulo: 'Photo frame', ecra: 'Screen', ligar: 'On', desligar: 'Off', brilho: 'Brightness',
    horario: 'Schedule', h_auto: 'Turn the screen off automatically', h_desligar: 'Off at',
    h_ligar: 'On at', h_guardar: 'Save schedule',
    fotos: 'Photos', seguinte: 'Next photo', mudar_cada: 'Change every',
    s5: '5 seconds', s10: '10 seconds', s30: '30 seconds', m1: '1 minute', m5: '5 minutes', m15: '15 minutes',
    modo: 'Mode', cobrir: 'Fill screen', desfocado: 'Blurred background',
    enviar_t: 'Upload photos', enviar: 'Upload',
    guardadas: 'Saved photos', ver_apagar: 'View and delete photos',
    anterior: '‹ Previous', proxima: 'Next ›', fechar: 'Close',
    estado: 'Status', energia: 'Power', reiniciar: 'Restart', desligar_tablet: 'Shut down tablet',
    sem_ligacao: 'No connection to the tablet',
    conf_modo: 'Changing the mode converts all photos again (10 to 20 minutes). Continue?',
    conf_reboot: 'Restart the tablet?', conf_poweroff: 'Shut down the tablet?',
    escolhe: 'Choose the photos first',
    a_enviar: (i, n, f) => `Uploading ${i} of ${n}: ${f}`,
    enviadas: (ok, n) => `${ok} of ${n} photos uploaded.`,
    erro_envio: 'Upload error', falhou: f => `Failed: ${f}`,
    st_fotos: 'Photos', st_fotos_v: (p, n) => `${p} prepared of ${n}`,
    st_bateria: 'Battery', st_temp: 'Temperature', st_ligado: 'Uptime', st_ip: 'Address',
    bat: {Charging: 'charging', Discharging: 'discharging', 'Not charging': 'not charging',
          Full: 'full', Unknown: 'unknown'},
    todas: 'All folders', principal: '(main folder)',
    g_info: (t, p, ps) => `${t} photos · page ${p} of ${ps}`, g_sel: n => ` · ${n} selected`,
    a_preparar: '(preparing)',
    apagar_sel: 'Delete selected', apagar_n: n => `Delete ${n} selected`,
    conf_apagar: n => `Delete ${n} photo(s) from the tablet? This cannot be undone.`,
  },
};

function lerLingua() {
  try { const l = localStorage.getItem('lingua'); if (l === 'pt' || l === 'en') return l; } catch (e) {}
  return (navigator.language || 'en').toLowerCase().startsWith('pt') ? 'pt' : 'en';
}
let lingua = lerLingua();
const T = () => TXT[lingua];
const tr = x => (x && typeof x === 'object') ? (x[lingua] || x.en) : x;   // server messages

function aplicarLingua() {
  document.documentElement.lang = lingua;
  document.title = T().titulo;
  document.querySelectorAll('[data-t]').forEach(el => { el.textContent = T()[el.dataset.t]; });
  $('lingua').textContent = lingua === 'pt' ? 'EN' : 'PT';
  atualizarBotaoApagar();
}
function mudarLingua() {
  lingua = lingua === 'pt' ? 'en' : 'pt';
  try { localStorage.setItem('lingua', lingua); } catch (e) {}
  aplicarLingua();
  atualizar();
  if (!$('galeria').hidden) carregarGaleria(gal.pagina);
}

const $ = id => document.getElementById(id);
let tMsg;
function mostrar(texto) {
  $('msg').textContent = tr(texto); $('msg').classList.add('ver');
  clearTimeout(tMsg); tMsg = setTimeout(() => $('msg').classList.remove('ver'), 3000);
}
async function api(acao, dados = {}) {
  try {
    const r = await fetch('/api/' + acao, {method:'POST',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(dados)});
    const j = await r.json();
    mostrar(j.msg || j.erro);
  } catch (e) { mostrar(T().sem_ligacao); }
  setTimeout(atualizar, 500);
}
function mudarModo(modo) {
  if (confirm(T().conf_modo)) api('modo', {modo});
}
let horarioCarregado = false;
function guardarHorario() {
  api('horario', {ativo: $('h-ativo').checked, desligar: $('h-desligar').value, ligar: $('h-ligar').value});
}
function energia(cmd) {
  if (confirm(cmd === 'reboot' ? T().conf_reboot : T().conf_poweroff)) api('energia', {comando:cmd});
}
async function enviar() {
  const fs = [...$('ficheiros').files];
  if (!fs.length) { mostrar(T().escolhe); return; }
  let ok = 0;
  for (const [i, f] of fs.entries()) {
    $('envio').textContent = T().a_enviar(i + 1, fs.length, f.name);
    try {
      const r = await fetch('/api/enviar?nome=' + encodeURIComponent(f.name), {method:'POST',
        headers:{'Content-Type':'application/octet-stream'}, body:f});
      if (r.ok) ok++; else mostrar((await r.json()).erro || T().erro_envio);
    } catch (e) { mostrar(T().falhou(f.name)); }
  }
  $('envio').textContent = T().enviadas(ok, fs.length);
  $('ficheiros').value = '';
  if (ok) api('atualizar');
}
async function atualizar() {
  try {
    const e = await (await fetch('/api/estado')).json();
    $('brilho').max = e.brilho_max; $('brilho').value = e.brilho; $('brilho-v').textContent = e.brilho;
    $('ligar').className = e.ecra_ligado ? 'ativo' : 'sec';
    $('desligar').className = e.ecra_ligado ? 'sec' : 'ativo';
    if (e.intervalo) $('intervalo').value = String(e.intervalo);
    $('m-cobrir').className = e.modo === 'cobrir' ? 'ativo' : 'sec';
    $('m-desfocado').className = e.modo === 'desfocado' ? 'ativo' : 'sec';
    if (!horarioCarregado && e.horario) {   // first read only, so it does not overwrite your edits
      $('h-ativo').checked = e.horario.ativo;
      $('h-desligar').value = e.horario.desligar;
      $('h-ligar').value = e.horario.ligar;
      horarioCarregado = true;
    }
    const t = T();
    const linhas = [
      [t.st_fotos, t.st_fotos_v(e.preparadas, e.fotos)],
      [t.st_bateria, `${e.bateria}% (${t.bat[e.bateria_estado] || e.bateria_estado})`],
      [t.st_temp, `${e.temperatura} °C`],
      [t.st_ligado, e.ligado_ha],
      [t.st_ip, e.ip],
    ];
    $('estado').innerHTML = linhas.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(String(v))}</dd>`).join('');
  } catch (e) { /* tablet busy; try again shortly */ }
}

const gal = {pagina: 1, sel: new Set()};
const esc = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function abrirGaleria() { $('galeria').hidden = false; $('g-abrir').hidden = true; carregarGaleria(1, true); }
function fecharGaleria() { $('galeria').hidden = true; $('g-abrir').hidden = false; $('g-grelha').innerHTML = ''; gal.sel.clear(); }
async function carregarGaleria(pagina, primeira) {
  const pasta = primeira ? null : $('g-pasta').value;
  const q = new URLSearchParams({pagina});
  if (pasta !== null && pasta !== '*') q.set('pasta', pasta);
  let d;
  try { d = await (await fetch('/api/fotos?' + q)).json(); }
  catch (e) { mostrar(T().sem_ligacao); return; }
  gal.pagina = d.pagina;
  const t = T();
  const atual = $('g-pasta').value || '*';
  $('g-pasta').innerHTML = `<option value="*">${esc(t.todas)}</option>` +
    d.pastas.map(p => `<option value="${esc(p)}">${p ? esc(p) : esc(t.principal)}</option>`).join('');
  $('g-pasta').value = d.pastas.includes(atual) ? atual : '*';
  $('g-info').textContent = t.g_info(d.total, d.pagina, d.paginas) + (gal.sel.size ? t.g_sel(gal.sel.size) : '');
  $('g-ant').disabled = d.pagina <= 1;
  $('g-seg').disabled = d.pagina >= d.paginas;
  $('g-grelha').innerHTML = d.fotos.map(f => {
    const sel = gal.sel.has(f.id) ? ' sel' : '';
    const titulo = esc((f.pasta ? f.pasta + '/' : '') + f.nome);
    return f.preparada
      ? `<div class="foto${sel}" data-id="${f.id}" title="${titulo}">
           <img loading="lazy" src="/foto/${f.id}.jpg" alt="">
           <div class="nome">${esc(f.nome)}</div></div>`
      : `<div class="foto sem-img${sel}" data-id="${f.id}" title="${titulo}">
           ${esc(f.nome)}<br>${esc(t.a_preparar)}</div>`;
  }).join('');
  atualizarBotaoApagar();
}
$('g-grelha').addEventListener('click', ev => {
  const el = ev.target.closest('.foto'); if (!el) return;
  const id = el.dataset.id;
  if (gal.sel.has(id)) gal.sel.delete(id); else gal.sel.add(id);
  el.classList.toggle('sel');
  atualizarBotaoApagar();
});
function atualizarBotaoApagar() {
  $('g-apagar').disabled = gal.sel.size === 0;
  $('g-apagar').textContent = gal.sel.size ? T().apagar_n(gal.sel.size) : T().apagar_sel;
}
async function apagarSelecionadas() {
  const n = gal.sel.size;
  if (!n || !confirm(T().conf_apagar(n))) return;
  try {
    const r = await fetch('/api/apagar', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ids: [...gal.sel]})});
    const j = await r.json(); mostrar(j.msg || j.erro);
  } catch (e) { mostrar(T().sem_ligacao); }
  gal.sel.clear();
  carregarGaleria(gal.pagina);
  setTimeout(atualizar, 500);
}

aplicarLingua();
atualizar(); setInterval(atualizar, 10000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- server

class Painel(BaseHTTPRequestHandler):
    server_version = "Moldura/1.0"

    def _json(self, codigo, dados):
        corpo = json.dumps(dados).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        partes = urlsplit(self.path)
        caminho, q = partes.path, parse_qs(partes.query, keep_blank_values=True)
        if caminho in ("/", "/index.html"):
            corpo = PAGINA.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif caminho == "/api/estado":
            self._json(200, estado())
        elif caminho == "/api/fotos":
            pasta = q["pasta"][0] if "pasta" in q else None
            try:
                pagina = int(q.get("pagina", ["1"])[0])
            except ValueError:
                pagina = 1
            self._json(200, listar_fotos(pasta, pagina))
        elif mt := re.fullmatch(r"/foto/([0-9a-f]{16})\.jpg", caminho):
            ficheiro = CACHE / f"{mt.group(1)}.jpg"
            try:
                tamanho = ficheiro.stat().st_size
                with open(ficheiro, "rb") as f:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(tamanho))
                    self.send_header("Cache-Control", "max-age=86400")
                    self.end_headers()
                    while bloco := f.read(65536):
                        self.wfile.write(bloco)
            except OSError:
                self._json(404, {"erro": m("foto ainda não preparada", "photo not prepared yet")})
        else:
            self._json(404, {"erro": m("não encontrado", "not found")})

    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        partes = urlsplit(self.path)
        acao = partes.path.removeprefix("/api/")
        if tamanho > MAX_ENVIO:
            self.close_connection = True
            self._json(413, {"erro": m("ficheiro demasiado grande", "file too large")})
            return
        try:
            if acao == "enviar":
                nome = parse_qs(partes.query).get("nome", ["foto.jpg"])[0]
                try:
                    guardada = guardar_envio(nome, self.rfile, tamanho)
                except ValueError:
                    self.close_connection = True   # body not read: close the connection
                    raise
                self._json(200, {"msg": m(f"Guardada: {guardada}", f"Saved: {guardada}")})
                return
            if tamanho > 64 * 1024:
                raise Erro("pedido demasiado grande", "request too large")
            corpo = self.rfile.read(tamanho)
            if acao in ACOES:
                dados = json.loads(corpo or b"{}")
                self._json(200, {"msg": ACOES[acao](dados)})
            else:
                self._json(404, {"erro": m("ação desconhecida", "unknown action")})
        except Erro as e:
            self._json(400, {"erro": m("Erro: " + e.msg["pt"], "Error: " + e.msg["en"])})
        except (ValueError, OSError) as e:
            self._json(400, {"erro": m(f"Erro: {e}", f"Error: {e}")})

    def log_message(self, fmt, *args):
        pass  # no per-request logging (saves the SD card)


if __name__ == "__main__":
    threading.Thread(target=vigiar_horario, daemon=True).start()
    print(f"Photo frame panel at http://{ip_local()}:{PORTA}/")
    ThreadingHTTPServer(("", PORTA), Painel).serve_forever()
