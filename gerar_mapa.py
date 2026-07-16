import folium
from folium.plugins import Fullscreen, MousePosition
from branca.element import MacroElement, Template
import geopandas as gpd
from pathlib import Path
import warnings
import base64
import re

warnings.filterwarnings('ignore')  # Ignorar avisos do geopandas

# ---------------------------------------------------------------- identidade
COR_MARCA = '#1E3A72'  # azul-marinho da RTA (extraído do logo)

# Cores por tipo de camada (dados por região)
COR_REGIAO = '#1E3A72'
COR_SRE = '#2563eb'
COR_CRITICO = '#ef2b2b'

# Cores das camadas de contexto (estaduais)
COR_ESTADO = '#94A3B8'  # slate: legível no satélite E no painel branco
COR_HIDRO = '#38BDF8'   # azul-céu

# Situação dos trechos (códigos oficiais do SRE) -> (descrição, cor).
# As cores são agrupadas por FAMÍLIA para o mapa se ler à distância:
#   verdes  = duplicadas | azuis = pavimentadas simples
#   laranjas = em obras  | marrons = não pavimentadas | cinza = planejada
# A ordem do dicionário vai do melhor padrão ao mais precário (e define a
# ordem de exibição na legenda).
SITUACAO_INFO = {
    'DUP': ('Duplicada',                     '#047857'),  # verde escuro
    'PDU': ('Duplicada em perímetro urbano', '#10B981'),  # verde claro
    'PPS': ('Pavimentada pista simples',     '#2563EB'),  # azul
    'PSU': ('Simples urbana',                '#60A5FA'),  # azul claro
    'EOD': ('Em obras de duplicação',        '#FB923C'),  # laranja
    'EOP': ('Em obras de pavimentação',      '#F59E0B'),  # âmbar
    'IMP': ('Implantada',                    '#A16207'),  # ocre
    'LEN': ('Leito natural',                 '#B45309'),  # marrom (terra)
    'PLA': ('Planejada',                     '#94A3B8'),  # cinza
}
DESC_OUTRA = 'Não classificada'
COR_SIT_OUTRA = '#CBD5E1'  # fallback para código fora da tabela do SRE

# Hidrografia: nuordemcda 1 = curso principal (Rio Tocantins, bacia de
# ~940 mil km²); ordens altas são riachos. Filtramos aos principais para o
# mapa não estourar de tamanho (base completa tem 77.795 feições).
HIDRO_ORDEM_MAX = 3
HIDRO_SIMPLIFY = 0.0005  # ~55 m

TIPO_META = {
    'regiao':   ('Formato da Região', COR_REGIAO,  'poli'),
    'trechos':  ('Trechos',           COR_MARCA,   'multi'),
    'sre':      ('Pontos SRE',        COR_SRE,     'ponto'),
    'criticos': ('Pontos críticos',   COR_CRITICO, 'crit'),
}
TIPO_ORDEM = ['regiao', 'trechos', 'sre', 'criticos']

# Arquivos redundantes/brutos que não devem entrar no mapa
IGNORAR = {
    'esatado_tocantins',     # duplicata (erro de digitação) de estado_tocantins
    'hidrografia_estado_1',  # duplicata de hidrografia_estado
    'hidrografia',           # base bruta: extrapola o TO (usar a recortada)
}


def limpar_atributos(gdf):
    """
    Remove quebras de linha (\\r\\n) e espaços extras dos campos de texto.
    Sem isso, 'PSU' e 'PSU\\r\\n' viram categorias diferentes na legenda.
    """
    for col in gdf.columns:
        if col == 'geometry' or gdf[col].dtype != object:
            continue
        gdf[col] = (gdf[col].astype(str)
                    .str.replace(r'[\r\n\t]+', ' ', regex=True)
                    .str.strip()
                    .replace({'nan': None, 'None': None, '': None}))
    return gdf


def validar_nomenclatura_sre(gdf, nome_camada):
    """Controle de Qualidade: alerta sobre SRE vazio ou duplicado."""
    colunas_sre = [c for c in gdf.columns if 'SRE' in str(c).upper()]
    if not colunas_sre:
        return
    for col in colunas_sre:
        serie = gdf[col]
        total = len(serie)
        vazios = serie.isna() | serie.astype(str).str.strip().isin(['', 'None', 'nan'])
        n_vazios = int(vazios.sum())
        if n_vazios > 0:
            print(f"  [QUALIDADE] '{nome_camada}' -> '{col}': "
                  f"{n_vazios} de {total} registro(s) com SRE VAZIO.")
        preenchidos = serie[~vazios]
        duplicados = preenchidos[preenchidos.duplicated(keep=False)]
        if len(duplicados) > 0:
            print(f"  [QUALIDADE] '{nome_camada}' -> '{col}': "
                  f"SRE DUPLICADO(S): {', '.join(sorted(set(duplicados.astype(str))))}")
        if n_vazios == 0 and len(duplicados) == 0:
            print(f"  [QUALIDADE] '{nome_camada}' -> '{col}': OK ({total} registros).")


def classificar(nome):
    """Identifica o tipo da camada pelo nome do arquivo."""
    low = nome.lower()
    if low in IGNORAR:
        return 'skip'
    if low.startswith('hidrografia'):      # antes de 'estado' (hidrografia_estado)
        return 'hidrografia'
    if 'estado' in low and 'tocantins' in low:
        return 'estado'
    if 'pontos críticos' in low or 'pontos criticos' in low:
        return 'criticos'
    if 'pontos_sre' in low:
        return 'sre'
    if 'trechos' in low:
        return 'trechos'
    if 'regi' in low:
        return 'regiao'
    return None


def coluna_situacao(gdf):
    for c in gdf.columns:
        if 'SITUA' in str(c).upper():
            return c
    return None


def ordenar_situacoes(codigos):
    """Ordem fixa (a do SITUACAO_INFO) para comparar regiões lado a lado."""
    ordem = list(SITUACAO_INFO)
    return sorted(codigos, key=lambda c: (ordem.index(c) if c in ordem else 99, c))


def info_situacao(codigo):
    """Devolve (descrição, cor) de um código de situação do SRE."""
    return SITUACAO_INFO.get(codigo, (DESC_OUTRA, COR_SIT_OUTRA))


def js_safe(valor):
    """Texto seguro para embutir dentro de uma string JavaScript."""
    if valor is None:
        return ''
    return (str(valor).replace('\\', ' ').replace('"', ' ')
            .replace('\r', ' ').replace('\n', ' ').strip())


class PainelControle(MacroElement):
    """
    Painel de controle unificado (estilo geoportal) com identidade RTA.
    Árvore de 3 níveis: Região > Trechos > Situação (PPS/LEN/...).
    """

    def __init__(self, basemaps, default_base, regioes, contexto,
                 filtros_situacao, subtitulo, logo=None):
        super().__init__()
        self._name = 'PainelControle'
        self.basemaps = basemaps
        self.default_base = default_base
        self.regioes = regioes
        self.contexto = contexto
        self.filtros_situacao = filtros_situacao
        self.subtitulo = subtitulo
        self.logo = logo
        self._template = Template(u"""
        {% macro html(this, kwargs) %}
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
            #gp-painel { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                width: 272px; max-height: 86vh; overflow-y: auto;
                background: rgba(255,255,255,0.97);
                -webkit-backdrop-filter: blur(16px) saturate(1.3); backdrop-filter: blur(16px) saturate(1.3);
                border: 1px solid rgba(255,255,255,0.6); border-radius: 16px;
                box-shadow: 0 2px 6px rgba(0,0,0,0.10), 0 16px 44px rgba(0,0,0,0.26);
                padding: 15px 16px; color: #1e293b; user-select: none;
                animation: gp-in 0.4s cubic-bezier(0.16,1,0.3,1); }
            @keyframes gp-in { from { opacity:0; transform:translateX(-10px);} to { opacity:1; transform:translateX(0);} }
            #gp-painel::-webkit-scrollbar { width: 6px; }
            #gp-painel::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.15); border-radius: 3px; }

            #gp-painel .gp-header { display:flex; flex-direction:column; align-items:flex-start; gap:8px; padding-bottom:13px; margin-bottom:2px; border-bottom:1px solid rgba(0,0,0,0.08); }
            #gp-painel .gp-logo-img { width:154px; height:auto; display:block; }
            #gp-painel .gp-sub { display:flex; align-items:center; gap:6px; font-size:10.5px; font-weight:500; letter-spacing:0.02em; color:#64748b; }
            #gp-painel .gp-sub::before { content:''; width:6px; height:6px; border-radius:50%; background:#1E3A72; flex:0 0 auto; }

            #gp-painel .gp-sec { padding:13px 0; border-bottom:1px solid rgba(0,0,0,0.06); }
            #gp-painel .gp-sec:last-child { border-bottom:none; padding-bottom:2px; }
            #gp-painel .gp-sec-h { display:flex; align-items:center; gap:7px; font-size:10.5px; font-weight:700; letter-spacing:0.07em; text-transform:uppercase; color:#64748b; margin-bottom:9px; }
            #gp-painel .gp-sec-h svg { width:14px; height:14px; flex:0 0 auto; color:#1E3A72; }

            /* Busca */
            #gp-painel .gp-busca-wrap { position:relative; }
            #gp-painel #gp-busca { width:100%; box-sizing:border-box; height:32px; padding:0 26px 0 30px;
                border:1px solid #e2e8f0; border-radius:9px; background:#f8fafc;
                font-family:inherit; font-size:12px; color:#0f172a; outline:none; transition:border-color .15s, background .15s; }
            #gp-painel #gp-busca::placeholder { color:#94a3b8; }
            #gp-painel #gp-busca:focus { border-color:#1E3A72; background:#fff; box-shadow:0 0 0 3px rgba(30,58,114,0.10); }
            #gp-painel .gp-lupa { position:absolute; left:9px; top:50%; transform:translateY(-50%); width:14px; height:14px; color:#94a3b8; pointer-events:none; }
            #gp-painel #gp-limpar { position:absolute; right:6px; top:50%; transform:translateY(-50%); display:none;
                width:18px; height:18px; border:none; border-radius:50%; background:#cbd5e1; color:#fff;
                font-size:12px; line-height:1; cursor:pointer; padding:0; }
            #gp-painel #gp-limpar:hover { background:#94a3b8; }
            #gp-painel #gp-resultados { max-height:172px; overflow-y:auto; margin-top:6px; }
            #gp-painel #gp-resultados::-webkit-scrollbar { width:5px; }
            #gp-painel #gp-resultados::-webkit-scrollbar-thumb { background:rgba(0,0,0,0.12); border-radius:3px; }
            #gp-painel .gp-res { display:flex; align-items:center; gap:7px; padding:5px 7px; border-radius:6px; cursor:pointer; transition:background .12s; }
            #gp-painel .gp-res:hover { background:rgba(30,58,114,0.08); }
            #gp-painel .gp-res-txt { flex:1; min-width:0; display:flex; flex-direction:column; gap:1px; }
            #gp-painel .gp-res-sre { font-size:10.5px; font-weight:700; color:#1E3A72;
                font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace; }
            #gp-painel .gp-res-sub { font-size:9px; color:#94a3b8; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
            #gp-painel .gp-res-tag { font-size:8.5px; font-weight:700; color:#fff; background:var(--c);
                padding:2px 5px; border-radius:4px; letter-spacing:0.03em; flex:0 0 auto; }
            #gp-painel .gp-vazio { font-size:10.5px; color:#94a3b8; padding:8px 7px; text-align:center; }

            /* Chips de filtro por situação */
            #gp-painel .gp-chips { display:flex; flex-wrap:wrap; gap:4px; margin-top:9px; }
            #gp-painel .gp-chip { display:inline-flex; align-items:center; gap:4px; padding:3px 7px;
                border:1px solid #e2e8f0; border-radius:20px; background:#fff; cursor:pointer;
                font-family:inherit; font-size:9.5px; font-weight:700; color:#cbd5e1;
                letter-spacing:0.04em; transition:all .15s; }
            #gp-painel .gp-chip::before { content:''; width:7px; height:7px; border-radius:50%; background:#e2e8f0; transition:background .15s; }
            #gp-painel .gp-chip.on { color:#334155; border-color:color-mix(in srgb, var(--c) 45%, #e2e8f0); background:color-mix(in srgb, var(--c) 8%, #fff); }
            #gp-painel .gp-chip.on::before { background:var(--c); }
            #gp-painel .gp-chip:hover { border-color:var(--c); }
            #gp-painel .gp-chip b { font-weight:600; opacity:0.65; font-variant-numeric:tabular-nums; }

            /* Mapas de fundo: cartões com ícone */
            #gp-painel .gp-bases { display:grid; grid-template-columns:repeat(3,1fr); gap:5px; }
            #gp-painel .gp-base-card { position:relative; cursor:pointer; }
            #gp-painel .gp-base-card input { position:absolute; opacity:0; width:0; height:0; }
            #gp-painel .gp-base-in { display:flex; flex-direction:column; align-items:center; gap:5px;
                padding:9px 3px; border:1px solid #e2e8f0; border-radius:10px; background:#f8fafc;
                transition:border-color .15s, background .15s, box-shadow .15s; }
            #gp-painel .gp-base-in svg { width:17px; height:17px; color:#94a3b8; transition:color .15s; }
            #gp-painel .gp-base-lbl { font-size:9.5px; font-weight:600; color:#64748b; letter-spacing:0.02em; }
            #gp-painel .gp-base-card:hover .gp-base-in { border-color:#cbd5e1; background:#fff; }
            #gp-painel .gp-base-card input:checked + .gp-base-in { border-color:#1E3A72;
                background:rgba(30,58,114,0.07); box-shadow:0 0 0 2px rgba(30,58,114,0.10); }
            #gp-painel .gp-base-card input:checked + .gp-base-in svg { color:#1E3A72; }
            #gp-painel .gp-base-card input:checked + .gp-base-in .gp-base-lbl { color:#1E3A72; font-weight:700; }
            #gp-painel .gp-base-card input:focus-visible + .gp-base-in { outline:2px solid #1E3A72; outline-offset:2px; }

            /* Nível 1: região */
            #gp-painel .gp-reg-head { display:flex; align-items:center; gap:8px; padding:7px 6px; margin:1px -6px; border-radius:8px; cursor:pointer; transition:background .15s; }
            #gp-painel .gp-reg-head:hover { background:rgba(15,23,42,0.05); }
            #gp-painel .gp-chev { flex:0 0 auto; width:11px; color:#94a3b8; transition:transform .2s; }
            #gp-painel .gp-reg.open > .gp-reg-head .gp-chev { transform:rotate(90deg); }
            #gp-painel .gp-reg-nome { flex:1; font-size:13px; font-weight:600; color:#0f172a; }
            #gp-painel .gp-reg-body { max-height:0; overflow:hidden; transition:max-height .3s ease; padding-left:15px; }
            #gp-painel .gp-reg.open > .gp-reg-body { max-height:620px; }

            /* Nível 2: item (Formato / Trechos / Pontos SRE) */
            #gp-painel .gp-sub-item { display:flex; align-items:center; gap:9px; padding:5px 6px; margin:1px -6px; border-radius:7px; }
            #gp-painel .gp-sub-item:hover { background:rgba(15,23,42,0.04); }
            #gp-painel .gp-sub-lbl { flex:1; font-size:12.5px; color:#475569; }
            #gp-painel .gp-tre-head { display:flex; align-items:center; gap:9px; padding:5px 6px; margin:1px -6px; border-radius:7px; cursor:pointer; transition:background .15s; }
            #gp-painel .gp-tre-head:hover { background:rgba(15,23,42,0.04); }
            #gp-painel .gp-chev2 { flex:0 0 auto; width:9px; color:#cbd5e1; transition:transform .2s; }
            #gp-painel .gp-tre.open > .gp-tre-head .gp-chev2 { transform:rotate(90deg); }
            #gp-painel .gp-tre.open > .gp-tre-head .gp-chev2 { color:#94a3b8; }
            #gp-painel .gp-tre-body { max-height:0; overflow:hidden; transition:max-height .25s ease; padding-left:14px; }
            #gp-painel .gp-tre.open > .gp-tre-body { max-height:440px; }

            /* Nível 3: situação */
            #gp-painel .gp-sit-head { display:flex; align-items:center; gap:8px; padding:3px 6px; margin:1px -6px; border-radius:6px; cursor:pointer; transition:background .15s; }
            #gp-painel .gp-sit-head:hover { background:rgba(15,23,42,0.04); }
            #gp-painel .gp-chev3 { flex:0 0 auto; width:8px; color:#cbd5e1; transition:transform .2s; }
            #gp-painel .gp-sit.open > .gp-sit-head .gp-chev3 { transform:rotate(90deg); color:#94a3b8; }
            #gp-painel .gp-sit-txt { flex:1; min-width:0; display:flex; flex-direction:column; gap:1px; padding:1px 0; }
            #gp-painel .gp-sit-cod { font-size:10.5px; font-weight:700; color:#475569; letter-spacing:0.05em;
                font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace; }
            #gp-painel .gp-sit-desc { font-size:9.5px; line-height:1.25; color:#94a3b8; }

            /* Nível 4: lista de SREs (clique = voa até o trecho) */
            #gp-painel .gp-sre-body { max-height:0; overflow:hidden; transition:max-height .25s ease; }
            #gp-painel .gp-sit.open > .gp-sre-body { max-height:150px; overflow-y:auto; }
            #gp-painel .gp-sre-body::-webkit-scrollbar { width:5px; }
            #gp-painel .gp-sre-body::-webkit-scrollbar-thumb { background:rgba(0,0,0,0.12); border-radius:3px; }
            #gp-painel .gp-sre { display:flex; align-items:center; gap:6px; padding:3px 6px 3px 22px; margin:0 -6px;
                font-size:10.5px; font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;
                color:#64748b; cursor:pointer; border-radius:5px; transition:background .12s, color .12s; }
            #gp-painel .gp-sre:hover { background:rgba(30,58,114,0.09); color:#1E3A72; }
            #gp-painel .gp-sre::before { content:''; width:4px; height:4px; border-radius:50%; background:currentColor; opacity:0.5; flex:0 0 auto; }

            #gp-painel .gp-switch { position:relative; display:inline-block; width:34px; height:20px; flex:0 0 auto; }
            #gp-painel .gp-switch input { opacity:0; width:0; height:0; }
            #gp-painel .gp-slider { position:absolute; inset:0; background:#cbd5e1; border-radius:20px; transition:.2s; cursor:pointer; }
            #gp-painel .gp-slider::before { content:''; position:absolute; width:16px; height:16px; left:2px; top:2px; background:#fff; border-radius:50%; transition:.2s; box-shadow:0 1px 3px rgba(0,0,0,0.3); }
            #gp-painel .gp-switch input:checked + .gp-slider { background:#1E3A72; }
            #gp-painel .gp-switch input:checked + .gp-slider::before { transform:translateX(14px); }
            #gp-painel .gp-switch.sm { width:30px; height:18px; }
            #gp-painel .gp-switch.sm .gp-slider::before { width:14px; height:14px; }
            #gp-painel .gp-switch.sm input:checked + .gp-slider::before { transform:translateX(12px); }
            #gp-painel .gp-switch.xs { width:26px; height:15px; }
            #gp-painel .gp-switch.xs .gp-slider::before { width:11px; height:11px; }
            #gp-painel .gp-switch.xs input:checked + .gp-slider::before { transform:translateX(11px); }

            #gp-painel .gp-cnt { font-variant-numeric:tabular-nums; font-weight:700; color:#0f172a; font-size:11px; background:rgba(15,23,42,0.06); padding:1px 8px; border-radius:20px; }
            #gp-painel .gp-cnt.xs { font-size:10px; padding:1px 6px; font-weight:600; color:#475569; }

            #gp-painel .sw { flex:0 0 auto; display:inline-block; }
            #gp-painel .sw.ponto, #gp-painel .sw.crit { width:14px; height:14px; border-radius:50%; background:var(--c); border:2px solid #fff; box-shadow:0 0 0 1px rgba(0,0,0,0.18); }
            #gp-painel .sw.crit { box-shadow:0 0 0 1px rgba(0,0,0,0.12), 0 0 0 4px color-mix(in srgb, var(--c) 22%, transparent); }
            #gp-painel .sw.linha { width:20px; height:5px; border-radius:3px; background:var(--c); }
            #gp-painel .sw.linha-xs { width:16px; height:4px; border-radius:2px; background:var(--c); }
            #gp-painel .sw.poli { width:16px; height:16px; border-radius:4px; border:2px solid var(--c); background:color-mix(in srgb, var(--c) 30%, transparent); }
            #gp-painel .sw.contorno { width:16px; height:16px; border-radius:4px; border:2px dashed var(--c); background:transparent; }
            #gp-painel .sw.multi { width:20px; height:5px; border-radius:3px;
                background:linear-gradient(90deg,#047857 0 25%,#2563EB 25% 50%,#F59E0B 50% 75%,#B45309 75% 100%); }

            /* Acessibilidade: foco visível por teclado */
            #gp-painel input:focus-visible + .gp-dot,
            #gp-painel input:focus-visible + .gp-slider { outline:2px solid #1E3A72; outline-offset:2px; }
        </style>

        <div id="gp-painel">
            <div class="gp-header">
                {% if this.logo %}
                <img class="gp-logo-img" src="{{ this.logo }}" alt="RTA Engenheiros Consultores">
                {% endif %}
                <div class="gp-sub">{{ this.subtitulo }}</div>
            </div>

            <div class="gp-sec">
                <div class="gp-sec-h">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M12 2l9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>
                    Mapas de Fundo
                </div>
                <div class="gp-bases">
                    {% for b in this.basemaps %}
                    <label class="gp-base-card" title="{{ b.nome }}">
                        <input type="radio" name="gp-base" value="{{ b.layer.get_name() }}" {% if b.layer.get_name() == this.default_base.get_name() %}checked{% endif %}>
                        <span class="gp-base-in">
                            {% if b.icone == 'mapa' %}
                            <svg viewBox="0 0 24 24" fill="none"><path d="M9 3L3 5.5v15L9 18l6 2.5 6-2.5v-15L15 5.5 9 3z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M9 3v15M15 5.5v15" stroke="currentColor" stroke-width="1.7"/></svg>
                            {% elif b.icone == 'satelite' %}
                            <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.7"/><path d="M3 12h18M12 3c2.5 2.6 2.5 15.4 0 18M12 3c-2.5 2.6-2.5 15.4 0 18" stroke="currentColor" stroke-width="1.5"/></svg>
                            {% else %}
                            <svg viewBox="0 0 24 24" fill="none"><path d="M20.5 14.5A8.5 8.5 0 019.5 3.5a8.5 8.5 0 1011 11z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>
                            {% endif %}
                            <span class="gp-base-lbl">{{ b.nome }}</span>
                        </span>
                    </label>
                    {% endfor %}
                </div>
            </div>

            <div class="gp-sec">
                <div class="gp-sec-h">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M3 6l9-4 9 4-9 4-9-4zM3 12l9 4 9-4M3 18l9 4 9-4" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
                    Camadas por Região
                </div>
                {% for reg in this.regioes %}
                <div class="gp-reg {% if loop.first %}open{% endif %}" data-reg="{{ reg.id }}">
                    <div class="gp-reg-head">
                        <svg class="gp-chev" viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
                        <label class="gp-switch" onclick="event.stopPropagation()">
                            <input type="checkbox" data-regiao="{{ reg.id }}" checked>
                            <span class="gp-slider"></span>
                        </label>
                        <span class="gp-reg-nome">{{ reg.nome }}</span>
                    </div>
                    <div class="gp-reg-body">
                        {% for it in reg.itens %}
                        {% if it.situacoes %}
                        <div class="gp-tre" data-grp="{{ it.grupo }}">
                            <div class="gp-tre-head">
                                <svg class="gp-chev2" viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>
                                <label class="gp-switch sm" onclick="event.stopPropagation()">
                                    <input type="checkbox" data-regiao="{{ reg.id }}" data-grupo="{{ it.grupo }}" checked>
                                    <span class="gp-slider"></span>
                                </label>
                                <span class="sw multi"></span>
                                <span class="gp-sub-lbl">{{ it.nome }}</span>
                                <span class="gp-cnt">{{ it.count }}</span>
                            </div>
                            <div class="gp-tre-body">
                                {% for s in it.situacoes %}
                                <div class="gp-sit">
                                    <div class="gp-sit-head">
                                        <svg class="gp-chev3" viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
                                        <label class="gp-switch xs" onclick="event.stopPropagation()">
                                            <input type="checkbox" data-regiao="{{ reg.id }}" data-grupo="{{ it.grupo }}" data-sit="{{ s.codigo }}" data-camada="{{ s.layer.get_name() }}" checked>
                                            <span class="gp-slider"></span>
                                        </label>
                                        <span class="sw linha-xs" style="--c: {{ s.cor }}"></span>
                                        <span class="gp-sit-txt">
                                            <span class="gp-sit-cod">{{ s.codigo }}</span>
                                            <span class="gp-sit-desc">{{ s.desc }}</span>
                                        </span>
                                        <span class="gp-cnt xs">{{ s.count }}</span>
                                    </div>
                                    <div class="gp-sre-body">
                                        {% for sr in s.sres %}
                                        <div class="gp-sre" data-b="{{ sr.b }}" data-sre="{{ sr.sre }}" data-fg="{{ s.layer.get_name() }}" title="Ir para {{ sr.sre }}">{{ sr.sre }}</div>
                                        {% endfor %}
                                    </div>
                                </div>
                                {% endfor %}
                            </div>
                        </div>
                        {% else %}
                        <div class="gp-sub-item">
                            <label class="gp-switch sm" onclick="event.stopPropagation()">
                                <input type="checkbox" data-regiao="{{ reg.id }}" data-camada="{{ it.layer.get_name() }}" checked>
                                <span class="gp-slider"></span>
                            </label>
                            <span class="sw {{ it.forma }}" style="--c: {{ it.cor }}"></span>
                            <span class="gp-sub-lbl">{{ it.nome }}</span>
                            <span class="gp-cnt">{{ it.count }}</span>
                        </div>
                        {% endif %}
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>

            {% if this.contexto %}
            <div class="gp-sec">
                <div class="gp-sec-h">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M12 21s7-5.686 7-11a7 7 0 10-14 0c0 5.314 7 11 7 11z" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="10" r="2.4" stroke="currentColor" stroke-width="1.6"/></svg>
                    Camadas de Contexto
                </div>
                {% for c in this.contexto %}
                <div class="gp-sub-item">
                    <label class="gp-switch sm">
                        <input type="checkbox" data-camada="{{ c.layer.get_name() }}" {% if c.ativo %}checked{% endif %}>
                        <span class="gp-slider"></span>
                    </label>
                    <span class="sw {{ c.forma }}" style="--c: {{ c.cor }}"></span>
                    <span class="gp-sub-lbl">{{ c.nome }}</span>
                    <span class="gp-cnt">{{ c.count }}</span>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            <div class="gp-sec">
                <div class="gp-sec-h">
                    <svg viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="1.8"/><path d="M20 20l-3.5-3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
                    Busca &amp; Filtros
                </div>
                <div class="gp-busca-wrap">
                    <svg class="gp-lupa" viewBox="0 0 24 24" fill="none"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2"/><path d="M20 20l-3.5-3.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
                    <input id="gp-busca" type="text" placeholder="SRE, rodovia ou cidade…" autocomplete="off" aria-label="Buscar SRE, rodovia ou cidade">
                    <button id="gp-limpar" type="button" aria-label="Limpar busca">&times;</button>
                </div>
                <div id="gp-resultados"></div>
                <div class="gp-chips">
                    {% for f in this.filtros_situacao %}
                    <button type="button" class="gp-chip on" data-sit="{{ f.codigo }}" style="--c: {{ f.cor }}" title="{{ f.desc }}">{{ f.codigo }} <b>{{ f.count }}</b></button>
                    {% endfor %}
                </div>
            </div>
        </div>
        {% endmacro %}

        {% macro script(this, kwargs) %}
            (function() {
                var map = {{ this._parent.get_name() }};

                var Painel = L.Control.extend({
                    options: { position: 'topleft' },
                    onAdd: function() {
                        var d = document.getElementById('gp-painel');
                        L.DomEvent.disableClickPropagation(d);
                        L.DomEvent.disableScrollPropagation(d);
                        return d;
                    }
                });
                new Painel().addTo(map);
                L.control.zoom({ position: 'topright' }).addTo(map);

                // Accordions (região e trechos)
                document.querySelectorAll('#gp-painel .gp-reg-head').forEach(function(h){
                    h.addEventListener('click', function(){ h.parentNode.classList.toggle('open'); });
                });
                document.querySelectorAll('#gp-painel .gp-tre-head').forEach(function(h){
                    h.addEventListener('click', function(e){ e.stopPropagation(); h.parentNode.classList.toggle('open'); });
                });
                document.querySelectorAll('#gp-painel .gp-sit-head').forEach(function(h){
                    h.addEventListener('click', function(e){ e.stopPropagation(); h.parentNode.classList.toggle('open'); });
                });

                // --- Mapas de fundo ---
                var bases = { {% for b in this.basemaps %}"{{ b.layer.get_name() }}": {{ b.layer.get_name() }}{% if not loop.last %}, {% endif %}{% endfor %} };
                var padrao = {{ this.default_base.get_name() }};
                Object.keys(bases).forEach(function(k){ if (map.hasLayer(bases[k])) map.removeLayer(bases[k]); });
                map.addLayer(padrao);
                document.querySelectorAll('input[name="gp-base"]').forEach(function(r){
                    r.addEventListener('change', function(){
                        Object.keys(bases).forEach(function(k){ if (map.hasLayer(bases[k])) map.removeLayer(bases[k]); });
                        map.addLayer(bases[this.value]);
                    });
                });

                // --- Camadas (folhas: formato, situações dos trechos, SRE, contexto) ---
                var camadas = { {% for reg in this.regioes %}{% for it in reg.itens %}{% if it.situacoes %}{% for s in it.situacoes %}"{{ s.layer.get_name() }}": {{ s.layer.get_name() }}, {% endfor %}{% else %}"{{ it.layer.get_name() }}": {{ it.layer.get_name() }}, {% endif %}{% endfor %}{% endfor %}{% for c in this.contexto %}"{{ c.layer.get_name() }}": {{ c.layer.get_name() }}, {% endfor %} };

                function aplicar(input) {
                    var lyr = camadas[input.getAttribute('data-camada')];
                    if (!lyr) return;
                    if (input.checked) { if (!map.hasLayer(lyr)) map.addLayer(lyr); }
                    else { if (map.hasLayer(lyr)) map.removeLayer(lyr); }
                }

                function sincronizarPais(rid, grp) {
                    if (grp) {
                        var f = document.querySelectorAll('#gp-painel input[data-camada][data-grupo="'+grp+'"]');
                        var g = document.querySelector('#gp-painel input[data-grupo="'+grp+'"]:not([data-camada])');
                        if (g) g.checked = Array.prototype.some.call(f, function(c){ return c.checked; });
                    }
                    if (rid) {
                        var fr = document.querySelectorAll('#gp-painel input[data-camada][data-regiao="'+rid+'"]');
                        var mr = document.querySelector('#gp-painel input[data-regiao="'+rid+'"]:not([data-camada]):not([data-grupo])');
                        if (mr) mr.checked = Array.prototype.some.call(fr, function(c){ return c.checked; });
                    }
                }

                // Estado inicial: sincroniza o mapa com os interruptores
                document.querySelectorAll('#gp-painel input[data-camada]').forEach(aplicar);

                // Nível 3 / folhas
                document.querySelectorAll('#gp-painel input[data-camada]').forEach(function(t){
                    t.addEventListener('change', function(){
                        aplicar(this);
                        sincronizarPais(this.getAttribute('data-regiao'), this.getAttribute('data-grupo'));
                    });
                });

                // Nível 2: grupo "Trechos" -> liga/desliga todas as situações
                document.querySelectorAll('#gp-painel input[data-grupo]:not([data-camada])').forEach(function(g){
                    g.addEventListener('change', function(){
                        var grp = this.getAttribute('data-grupo'), on = this.checked;
                        document.querySelectorAll('#gp-painel input[data-camada][data-grupo="'+grp+'"]').forEach(function(c){
                            c.checked = on; aplicar(c);
                        });
                        sincronizarPais(this.getAttribute('data-regiao'), null);
                    });
                });

                // Voa até um trecho e abre a ficha dele
                function irPara(bstr, fgNome, sre) {
                    var p = bstr.split(',').map(Number);
                    map.fitBounds([[p[0], p[1]], [p[2], p[3]]], { maxZoom: 15, padding: [50, 50] });
                    var fg = camadas[fgNome];
                    if (!fg || !map.hasLayer(fg)) return;
                    fg.eachLayer(function(gj){
                        if (!gj.eachLayer) return;
                        gj.eachLayer(function(l){
                            if (l.feature && l.feature.properties &&
                                String(l.feature.properties.SRE) === sre && l.openPopup) {
                                setTimeout(function(){ l.openPopup(); }, 350);
                            }
                        });
                    });
                }

                // Nível 4: clique no SRE da árvore
                document.querySelectorAll('#gp-painel .gp-sre').forEach(function(el){
                    el.addEventListener('click', function(e){
                        e.stopPropagation();
                        irPara(this.getAttribute('data-b'), this.getAttribute('data-fg'),
                               this.getAttribute('data-sre'));
                    });
                });

                // ------------------------------------------------ BUSCA
                var indice = [
                {%- for reg in this.regioes %}{%- for it in reg.itens %}{%- if it.situacoes %}{%- for s in it.situacoes %}{%- for sr in s.sres %}
                {s:"{{ sr.sre }}",r:"{{ sr.rod }}",c:"{{ sr.cid }}",g:"{{ reg.nome }}",t:"{{ s.codigo }}",k:"{{ s.cor }}",b:"{{ sr.b }}",f:"{{ s.layer.get_name() }}"},
                {%- endfor %}{%- endfor %}{%- endif %}{%- endfor %}{%- endfor %}
                ];
                var campoBusca = document.getElementById('gp-busca');
                var caixaRes = document.getElementById('gp-resultados');
                var btnLimpar = document.getElementById('gp-limpar');

                function normalizar(t) {
                    return (t || '').toString().toLowerCase()
                        .normalize('NFD').replace(/[̀-ͯ]/g, '');
                }

                function renderResultados(termo) {
                    caixaRes.innerHTML = '';
                    btnLimpar.style.display = termo ? 'block' : 'none';
                    if (!termo) return;
                    var q = normalizar(termo);
                    var achados = indice.filter(function(i){
                        return normalizar(i.s).indexOf(q) >= 0 ||
                               normalizar(i.r).indexOf(q) >= 0 ||
                               normalizar(i.c).indexOf(q) >= 0;
                    });
                    if (!achados.length) {
                        caixaRes.innerHTML = '<div class="gp-vazio">Nada encontrado</div>';
                        return;
                    }
                    var total = achados.length;
                    achados.slice(0, 40).forEach(function(i){
                        var d = document.createElement('div');
                        d.className = 'gp-res';
                        d.innerHTML = '<span class="gp-res-txt">' +
                            '<span class="gp-res-sre">' + i.s + '</span>' +
                            '<span class="gp-res-sub">' + [i.r, i.c, i.g].filter(Boolean).join(' · ') + '</span>' +
                            '</span><span class="gp-res-tag" style="--c:' + i.k + '">' + i.t + '</span>';
                        d.addEventListener('click', function(){ irPara(i.b, i.f, i.s); });
                        caixaRes.appendChild(d);
                    });
                    if (total > 40) {
                        var m2 = document.createElement('div');
                        m2.className = 'gp-vazio';
                        m2.textContent = '… e mais ' + (total - 40) + ' resultado(s)';
                        caixaRes.appendChild(m2);
                    }
                }

                campoBusca.addEventListener('input', function(){ renderResultados(this.value.trim()); });
                btnLimpar.addEventListener('click', function(){
                    campoBusca.value = ''; renderResultados(''); campoBusca.focus();
                });

                // ------------------------------------------------ FILTROS (chips)
                document.querySelectorAll('#gp-painel .gp-chip').forEach(function(chip){
                    chip.addEventListener('click', function(){
                        var sit = this.getAttribute('data-sit');
                        var ligar = !this.classList.contains('on');
                        this.classList.toggle('on', ligar);
                        document.querySelectorAll('#gp-painel input[data-sit="' + sit + '"]').forEach(function(c){
                            c.checked = ligar;
                            aplicar(c);
                            sincronizarPais(c.getAttribute('data-regiao'), c.getAttribute('data-grupo'));
                        });
                    });
                });

                // Mantém o chip coerente quando a situação é mexida pela árvore
                function sincronizarChips() {
                    document.querySelectorAll('#gp-painel .gp-chip').forEach(function(chip){
                        var sit = chip.getAttribute('data-sit');
                        var alvos = document.querySelectorAll('#gp-painel input[data-sit="' + sit + '"]');
                        var algum = Array.prototype.some.call(alvos, function(c){ return c.checked; });
                        chip.classList.toggle('on', algum);
                    });
                }
                document.querySelectorAll('#gp-painel input[type="checkbox"]').forEach(function(c){
                    c.addEventListener('change', function(){ setTimeout(sincronizarChips, 0); });
                });

                // Nível 1: mestre da região -> liga/desliga tudo da região
                document.querySelectorAll('#gp-painel input[data-regiao]:not([data-camada]):not([data-grupo])').forEach(function(mst){
                    mst.addEventListener('change', function(){
                        var rid = this.getAttribute('data-regiao'), on = this.checked;
                        document.querySelectorAll('#gp-painel input[data-camada][data-regiao="'+rid+'"]').forEach(function(c){
                            c.checked = on; aplicar(c);
                        });
                        document.querySelectorAll('#gp-painel input[data-grupo][data-regiao="'+rid+'"]:not([data-camada])').forEach(function(g){
                            g.checked = on;
                        });
                    });
                });
            })();
        {% endmacro %}
        """)


def create_webgis():
    print("Iniciando a criação do WebGIS...")

    base_dir = Path(__file__).parent
    camadas_dir = base_dir / "camadas"
    output_file = base_dir / "mapa_interativo.html"

    if not camadas_dir.exists():
        print(f"Erro: A pasta {camadas_dir} não existe.")
        return

    m = folium.Map(location=[-15.7801, -47.9292], zoom_start=4, tiles=None,
                   max_zoom=21, zoom_control=False)

    # 1. Mapas de Fundo (o último adicionado abre por padrão -> Satélite)
    tl_padrao = folium.TileLayer('OpenStreetMap', name='Padrão', control=False,
                                 max_zoom=21, max_native_zoom=19)
    tl_escuro = folium.TileLayer(
        tiles='https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        name='Escuro', control=False, max_zoom=21, max_native_zoom=19)
    tl_satelite = folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
        name='Satélite', control=False, max_zoom=21, max_native_zoom=17)
    for tl in (tl_padrao, tl_escuro, tl_satelite):
        tl.add_to(m)

    # 2. Ler shapefiles
    regioes = {}
    contexto = {}
    bounds = []
    total_situacao = {}

    for shp_file in sorted(camadas_dir.glob('*.shp')):
        nome = shp_file.stem
        tipo = classificar(nome)

        if tipo == 'skip':
            print(f"Ignorando (duplicata/base bruta): {shp_file.name}")
            continue
        if tipo is None:
            print(f"Aviso: tipo não reconhecido para '{nome}'. Ignorada.")
            continue

        mm = re.match(r'^(R\d+)_', nome.upper())
        rid = mm.group(1) if mm else 'OUTROS'
        if tipo in TIPO_ORDEM and rid in regioes and tipo in regioes[rid]:
            print(f"Ignorando duplicata: {shp_file.name} ({rid}/{tipo})")
            continue

        print(f"Lendo camada: {shp_file.name}")
        try:
            gdf = gpd.read_file(shp_file)

            if gdf.crs and gdf.crs.to_string() != 'EPSG:4326':
                gdf = gdf.to_crs(epsg=4326)
            elif not gdf.crs:
                print(f"  Aviso: {shp_file.name} sem CRS. Assumindo EPSG:4326.")
                gdf.set_crs(epsg=4326, inplace=True)

            gdf = limpar_atributos(gdf)

            # ---------------------------------------------- camadas de contexto
            if tipo == 'hidrografia':
                antes = len(gdf)
                if 'nuordemcda' in gdf.columns:
                    gdf = gdf[gdf['nuordemcda'] <= HIDRO_ORDEM_MAX].copy()
                gdf['geometry'] = gdf.geometry.simplify(HIDRO_SIMPLIFY, preserve_topology=False)
                print(f"  Hidrografia filtrada (ordem <= {HIDRO_ORDEM_MAX}): "
                      f"{antes} -> {len(gdf)} feições (simplificada)")
                fg = folium.FeatureGroup(name='Hidrografia', show=False, control=False)
                folium.GeoJson(data=gdf[['geometry']],
                               style_function=lambda x: {'color': COR_HIDRO, 'weight': 1.2,
                                                         'opacity': 0.75}).add_to(fg)
                contexto['hidrografia'] = {'fg': fg, 'count': len(gdf)}
                continue

            if tipo == 'estado':
                fg = folium.FeatureGroup(name='Limite do Estado', show=True, control=False)
                folium.GeoJson(data=gdf[['geometry']],
                               style_function=lambda x: {'color': COR_ESTADO, 'weight': 2.5,
                                                         'opacity': 0.9, 'fill': False,
                                                         'dashArray': '8,5'}).add_to(fg)
                contexto['estado'] = {'fg': fg, 'count': len(gdf)}
                continue

            # ------------------------------------------- dados por região
            minx, miny, maxx, maxy = gdf.total_bounds
            bounds.append([[miny, minx], [maxy, maxx]])

            validar_nomenclatura_sre(gdf, nome)

            cols = [c for c in gdf.columns if c.lower() != 'geometry']
            popup = folium.GeoJsonPopup(fields=cols, aliases=cols, localize=True) if cols else None
            _, cor, _ = TIPO_META[tipo]

            if tipo == 'trechos':
                # Uma camada por SITUAÇÃO: permite ligar/desligar cada classe
                csit = coluna_situacao(gdf)
                gdf['SITUACAO'] = (gdf[csit].astype(str).str.strip().str.upper()
                                   if csit else '')
                gdf['SITUACAO'] = gdf['SITUACAO'].replace({'NONE': '—', '': '—'})
                col_sre = next((c for c in gdf.columns if str(c).upper() == 'SRE'), None)
                col_rod = next((c for c in gdf.columns if str(c).upper() == 'RODOVIA'), None)
                col_cid = next((c for c in gdf.columns if 'CIDADE' in str(c).upper()), None)
                situacoes = []
                for sit in ordenar_situacoes(gdf['SITUACAO'].unique()):
                    sub = gdf[gdf['SITUACAO'] == sit]
                    desc_sit, cor_sit = info_situacao(sit)
                    cols_sub = [c for c in sub.columns if c.lower() != 'geometry']
                    fg_sit = folium.FeatureGroup(name=f'{rid}_trechos_{sit}',
                                                 show=True, control=False)
                    folium.GeoJson(
                        data=sub, name=f'{nome}_{sit}',
                        popup=folium.GeoJsonPopup(fields=cols_sub, aliases=cols_sub, localize=True),
                        style_function=(lambda x, c=cor_sit: {'color': c, 'weight': 3, 'opacity': 0.9}),
                    ).add_to(fg_sit)

                    # 4º nível: lista de SREs com o retângulo (bounds) de cada
                    # trecho, para o clique no código voar até ele no mapa.
                    lista_sre = []
                    for _, row in sub.iterrows():
                        if row.geometry is None or row.geometry.is_empty:
                            continue
                        minx_t, miny_t, maxx_t, maxy_t = row.geometry.bounds
                        lista_sre.append({
                            'sre': js_safe(row[col_sre]) if col_sre else '—',
                            'rod': js_safe(row[col_rod]) if col_rod else '',
                            'cid': js_safe(row[col_cid]) if col_cid else '',
                            'b': f'{miny_t:.6f},{minx_t:.6f},{maxy_t:.6f},{maxx_t:.6f}',
                        })
                    lista_sre.sort(key=lambda s: s['sre'])

                    situacoes.append({'codigo': sit, 'desc': desc_sit, 'fg': fg_sit,
                                      'count': len(sub), 'cor': cor_sit, 'sres': lista_sre})
                    total_situacao[sit] = total_situacao.get(sit, 0) + len(sub)
                regioes.setdefault(rid, {})['trechos'] = {'count': len(gdf),
                                                          'situacoes': situacoes}
                print(f"  '{nome}' -> {rid} / trechos ({len(gdf)} feições em "
                      f"{len(situacoes)} situações: "
                      f"{', '.join(s['codigo'] + '=' + str(s['count']) for s in situacoes)})")
                continue

            style_fn = (lambda x, color=cor: {'color': color, 'fillColor': color,
                                              'weight': 3, 'fillOpacity': 0.35})
            fg = folium.FeatureGroup(name=f'{rid}_{tipo}', show=True, control=False)
            folium.GeoJson(
                data=gdf, name=nome, popup=popup, style_function=style_fn,
                marker=folium.CircleMarker(radius=6, color=cor, fill_color=cor,
                                           fill_opacity=0.9, weight=1)
            ).add_to(fg)
            regioes.setdefault(rid, {})[tipo] = {'fg': fg, 'count': len(gdf)}
            print(f"  '{nome}' -> {rid} / {tipo} ({len(gdf)} feições)")

        except Exception as e:
            print(f"  Erro ao processar {shp_file.name}: {e}")

    # Ordem de sobreposição: hidrografia -> estado -> polígonos -> linhas -> pontos
    if 'hidrografia' in contexto:
        contexto['hidrografia']['fg'].add_to(m)
    if 'estado' in contexto:
        contexto['estado']['fg'].add_to(m)

    def rid_key(rid):
        mm = re.match(r'R(\d+)$', rid)
        return (0, int(mm.group(1))) if mm else (1, rid)
    ordem_regioes = sorted(regioes, key=rid_key)

    for tipo in TIPO_ORDEM:
        for rid in ordem_regioes:
            item = regioes[rid].get(tipo)
            if not item:
                continue
            if tipo == 'trechos':
                for s in item['situacoes']:
                    s['fg'].add_to(m)
            else:
                item['fg'].add_to(m)

    if bounds:
        m.fit_bounds([[min(b[0][0] for b in bounds), min(b[0][1] for b in bounds)],
                      [max(b[1][0] for b in bounds), max(b[1][1] for b in bounds)]])

    # 3. Ferramentas de navegação
    Fullscreen(position='topright', title='Tela cheia',
               title_cancel='Sair da tela cheia', force_separate_button=True).add_to(m)
    MousePosition(position='bottomright', separator=' | ', prefix='Coordenadas:',
                  num_digits=6, lat_first=True).add_to(m)

    # 4. Painel
    def fmt(n):
        return '{:,}'.format(n).replace(',', '.')

    def nome_regiao(rid):
        mm = re.match(r'R(\d+)$', rid)
        return f'Região {int(mm.group(1))}' if mm else rid.title()

    regioes_info = []
    for rid in ordem_regioes:
        itens = []
        for tipo in TIPO_ORDEM:
            item = regioes[rid].get(tipo)
            if not item:
                continue
            label, cor, forma = TIPO_META[tipo]
            if tipo == 'trechos':
                itens.append({
                    'nome': label, 'cor': cor, 'forma': forma,
                    'count': fmt(item['count']), 'grupo': f'{rid}-trechos',
                    'situacoes': [{'codigo': s['codigo'], 'desc': s['desc'],
                                   'layer': s['fg'], 'cor': s['cor'],
                                   'count': fmt(s['count']), 'sres': s['sres']}
                                  for s in item['situacoes']],
                })
            else:
                itens.append({'nome': label, 'layer': item['fg'], 'cor': cor,
                              'forma': forma, 'count': fmt(item['count']),
                              'situacoes': None})
        if itens:
            regioes_info.append({'id': rid, 'nome': nome_regiao(rid), 'itens': itens})

    contexto_info = []
    if 'estado' in contexto:
        contexto_info.append({'nome': 'Limite do Estado', 'layer': contexto['estado']['fg'],
                              'cor': COR_ESTADO, 'forma': 'contorno', 'ativo': True,
                              'count': fmt(contexto['estado']['count'])})
    if 'hidrografia' in contexto:
        contexto_info.append({'nome': 'Hidrografia', 'layer': contexto['hidrografia']['fg'],
                              'cor': COR_HIDRO, 'forma': 'linha', 'ativo': False,
                              'count': fmt(contexto['hidrografia']['count'])})

    logo_uri = None
    logo_path = base_dir / 'logo' / 'LOGO RTA.png'
    if logo_path.exists():
        logo_uri = 'data:image/png;base64,' + base64.b64encode(logo_path.read_bytes()).decode('ascii')

    # Filtros globais por situação (valem para todas as regiões)
    filtros_situacao = []
    for cod in ordenar_situacoes(total_situacao.keys()):
        desc, cor = info_situacao(cod)
        filtros_situacao.append({'codigo': cod, 'desc': desc, 'cor': cor,
                                 'count': fmt(total_situacao[cod])})

    m.add_child(PainelControle(
        basemaps=[{'nome': 'Padrão', 'layer': tl_padrao, 'icone': 'mapa'},
                  {'nome': 'Satélite', 'layer': tl_satelite, 'icone': 'satelite'},
                  {'nome': 'Escuro', 'layer': tl_escuro, 'icone': 'lua'}],
        default_base=tl_satelite,
        regioes=regioes_info,
        contexto=contexto_info,
        filtros_situacao=filtros_situacao,
        subtitulo='WebGIS · Inventário Rodoviário — Tocantins',
        logo=logo_uri,
    ))

    m.save(output_file)
    print(f"\nMapa salvo em: {output_file}")
    print(f"Regiões: {ordem_regioes}")
    print(f"Situação (total): {dict(sorted(total_situacao.items(), key=lambda kv: -kv[1]))}")


if __name__ == "__main__":
    create_webgis()
