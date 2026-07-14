import folium
from folium.plugins import Fullscreen, MousePosition
from branca.element import MacroElement, Template
import geopandas as gpd
from pathlib import Path
import warnings
import re

warnings.filterwarnings('ignore')  # Ignorar avisos do geopandas

# Identidade visual RTA Engenheiros Consultores
COR_MARCA = '#1E3A72'        # azul-marinho da marca (acento do painel)
COR_MARCA_ESCURO = '#102860'  # tom mais escuro do logo

# Paleta de cores por tipo de camada (usada no mapa E no painel/legenda)
COR_REGIAO = '#1E3A72'   # Formato da Região no azul-marinho da marca
COR_TRECHOS = '#7c3aed'  # Trechos (roxo) - distinto
COR_SRE = '#2563eb'      # Pontos SRE (azul vivo) - distinto do marinho
COR_CRITICO = '#ef2b2b'  # Pontos críticos (vermelho) - alerta

# Metadados por tipo: (rótulo exibido, cor, forma do símbolo na legenda)
TIPO_META = {
    'regiao':   ('Formato da Região', COR_REGIAO,  'poli'),
    'trechos':  ('Trechos',           COR_TRECHOS, 'linha'),
    'sre':      ('Pontos SRE',        COR_SRE,     'ponto'),
    'criticos': ('Pontos críticos',   COR_CRITICO, 'crit'),
}
# Ordem de exibição / sobreposição (polígono embaixo -> pontos em cima)
TIPO_ORDEM = ['regiao', 'trechos', 'sre', 'criticos']


def validar_nomenclatura_sre(gdf, nome_camada):
    """
    Controle de Qualidade: procura colunas relacionadas ao 'SRE' e verifica
    se existem registros vazios ou fora do padrão, alertando no console.
    """
    colunas_sre = [c for c in gdf.columns if 'SRE' in str(c).upper()]
    if not colunas_sre:
        return

    for col in colunas_sre:
        serie = gdf[col]
        total = len(serie)

        vazios = serie.isna() | serie.astype(str).str.strip().isin(['', 'None', 'nan'])
        n_vazios = int(vazios.sum())
        if n_vazios > 0:
            print(f"  [QUALIDADE] '{nome_camada}' -> coluna '{col}': "
                  f"{n_vazios} de {total} registro(s) com SRE VAZIO.")

        preenchidos = serie[~vazios]
        duplicados = preenchidos[preenchidos.duplicated(keep=False)]
        if len(duplicados) > 0:
            valores_dup = sorted(set(duplicados.astype(str)))
            print(f"  [QUALIDADE] '{nome_camada}' -> coluna '{col}': "
                  f"SRE DUPLICADO(S): {', '.join(valores_dup)}")

        if n_vazios == 0 and len(duplicados) == 0:
            print(f"  [QUALIDADE] '{nome_camada}' -> coluna '{col}': OK "
                  f"({total} registros, sem vazios nem duplicidades).")


def detectar_tipo(low):
    """Identifica o tipo da camada a partir do nome do arquivo (minúsculo)."""
    if 'pontos críticos' in low or 'pontos criticos' in low:
        return 'criticos'
    if 'pontos_sre' in low:
        return 'sre'
    if 'trechos' in low:
        return 'trechos'
    if 'regi' in low:
        return 'regiao'
    return None


class PainelControle(MacroElement):
    """
    Painel de controle unificado (estilo "Geoportal PRO"), à esquerda.
    Camadas em árvore POR REGIÃO: cada região expande e mostra Formato da
    Região, Trechos e Pontos SRE, cada um com interruptor, cor e contagem.
    """

    def __init__(self, basemaps, default_base, regioes, titulo, subtitulo, logo=None):
        super().__init__()
        self._name = 'PainelControle'
        self.basemaps = basemaps          # lista de (nome, tile_layer)
        self.default_base = default_base  # tile_layer padrão de abertura
        self.regioes = regioes            # lista de dict(id, nome, itens[...])
        self.titulo = titulo
        self.subtitulo = subtitulo
        self.logo = logo                  # data URI (base64) do logo, ou None
        self._template = Template(u"""
        {% macro html(this, kwargs) %}
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
            #gp-painel { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                width: 264px; max-height: 84vh; overflow-y: auto;
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
            #gp-painel .gp-logo { width:36px; height:36px; flex:0 0 auto; border-radius:10px; background:#1E3A72; display:flex; align-items:center; justify-content:center; box-shadow:0 3px 8px rgba(30,58,114,0.30); }
            #gp-painel .gp-sub { display:flex; align-items:center; gap:6px; font-size:10.5px; font-weight:500; letter-spacing:0.02em; color:#64748b; }
            #gp-painel .gp-sub::before { content:''; width:6px; height:6px; border-radius:50%; background:#1E3A72; flex:0 0 auto; }

            #gp-painel .gp-sec { padding:13px 0; border-bottom:1px solid rgba(0,0,0,0.06); }
            #gp-painel .gp-sec:last-child { border-bottom:none; padding-bottom:2px; }
            #gp-painel .gp-sec-h { display:flex; align-items:center; gap:7px; font-size:10.5px; font-weight:700; letter-spacing:0.07em; text-transform:uppercase; color:#64748b; margin-bottom:9px; }
            #gp-painel .gp-sec-h svg { width:14px; height:14px; flex:0 0 auto; color:#1E3A72; }

            #gp-painel .gp-radio { display:flex; align-items:center; gap:9px; padding:5px 6px; margin:1px -6px; border-radius:8px; cursor:pointer; font-size:13px; color:#334155; transition:background .15s; }
            #gp-painel .gp-radio:hover { background:rgba(15,23,42,0.05); }
            #gp-painel .gp-radio input { display:none; }
            #gp-painel .gp-dot { width:16px; height:16px; flex:0 0 auto; border-radius:50%; border:2px solid #cbd5e1; position:relative; transition:border-color .15s; }
            #gp-painel .gp-radio input:checked + .gp-dot { border-color:#1E3A72; }
            #gp-painel .gp-radio input:checked + .gp-dot::after { content:''; position:absolute; inset:3px; border-radius:50%; background:#1E3A72; }
            #gp-painel .gp-radio input:checked ~ .gp-rlbl { color:#0f172a; font-weight:600; }

            #gp-painel .gp-reg { border-radius:8px; }
            #gp-painel .gp-reg-head { display:flex; align-items:center; gap:8px; padding:7px 6px; margin:1px -6px; border-radius:8px; cursor:pointer; transition:background .15s; }
            #gp-painel .gp-reg-head:hover { background:rgba(15,23,42,0.05); }
            #gp-painel .gp-chev { flex:0 0 auto; width:11px; color:#94a3b8; transition:transform .2s; }
            #gp-painel .gp-reg.open .gp-chev { transform:rotate(90deg); }
            #gp-painel .gp-reg-nome { flex:1; font-size:13px; font-weight:600; color:#0f172a; }
            #gp-painel .gp-reg-body { max-height:0; overflow:hidden; transition:max-height .25s ease; padding-left:16px; }
            #gp-painel .gp-reg.open .gp-reg-body { max-height:260px; }
            #gp-painel .gp-sub { display:flex; align-items:center; gap:9px; padding:5px 6px; margin:1px -6px; border-radius:7px; }
            #gp-painel .gp-sub:hover { background:rgba(15,23,42,0.04); }
            #gp-painel .gp-sub-lbl { flex:1; font-size:12.5px; color:#475569; }

            #gp-painel .gp-switch { position:relative; display:inline-block; width:34px; height:20px; flex:0 0 auto; }
            #gp-painel .gp-switch input { opacity:0; width:0; height:0; }
            #gp-painel .gp-slider { position:absolute; inset:0; background:#cbd5e1; border-radius:20px; transition:.2s; cursor:pointer; }
            #gp-painel .gp-slider::before { content:''; position:absolute; width:16px; height:16px; left:2px; top:2px; background:#fff; border-radius:50%; transition:.2s; box-shadow:0 1px 3px rgba(0,0,0,0.3); }
            #gp-painel .gp-switch input:checked + .gp-slider { background:#1E3A72; }
            #gp-painel .gp-switch input:checked + .gp-slider::before { transform:translateX(14px); }
            #gp-painel .gp-switch.sm { width:30px; height:18px; }
            #gp-painel .gp-switch.sm .gp-slider::before { width:14px; height:14px; }
            #gp-painel .gp-switch.sm input:checked + .gp-slider::before { transform:translateX(12px); }

            #gp-painel .gp-cnt { font-variant-numeric:tabular-nums; font-weight:700; color:#0f172a; font-size:11px; background:rgba(15,23,42,0.06); padding:1px 8px; border-radius:20px; }

            #gp-painel .sw { flex:0 0 auto; display:inline-block; }
            #gp-painel .sw.ponto, #gp-painel .sw.crit { width:14px; height:14px; border-radius:50%; background:var(--c); border:2px solid #fff; box-shadow:0 0 0 1px rgba(0,0,0,0.18); }
            #gp-painel .sw.crit { box-shadow:0 0 0 1px rgba(0,0,0,0.12), 0 0 0 4px color-mix(in srgb, var(--c) 22%, transparent); }
            #gp-painel .sw.linha { width:20px; height:5px; border-radius:3px; background:var(--c); }
            #gp-painel .sw.poli { width:16px; height:16px; border-radius:4px; border:2px solid var(--c); background:color-mix(in srgb, var(--c) 30%, transparent); }

            /* Acessibilidade: foco visível por teclado */
            #gp-painel input:focus-visible + .gp-dot,
            #gp-painel input:focus-visible + .gp-slider { outline:2px solid #1E3A72; outline-offset:2px; }
            #gp-painel .gp-reg-head:focus-visible { outline:2px solid #1E3A72; outline-offset:-2px; }
        </style>

        <div id="gp-painel">
            <div class="gp-header">
                {% if this.logo %}
                <img class="gp-logo-img" src="{{ this.logo }}" alt="RTA Engenheiros Consultores">
                {% else %}
                <div class="gp-logo"><svg viewBox="0 0 24 24" width="20" height="20" fill="none"><path d="M9 3L3 5.5v15L9 18l6 2.5 6-2.5v-15L15 5.5 9 3z" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg></div>
                {% endif %}
                <div class="gp-sub">{{ this.subtitulo }}</div>
            </div>

            <div class="gp-sec">
                <div class="gp-sec-h">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M12 2l9 5-9 5-9-5 9-5zM3 12l9 5 9-5M3 17l9 5 9-5" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>
                    Mapas de Fundo
                </div>
                {% for nome, layer in this.basemaps %}
                <label class="gp-radio">
                    <input type="radio" name="gp-base" value="{{ layer.get_name() }}" {% if layer.get_name() == this.default_base.get_name() %}checked{% endif %}>
                    <span class="gp-dot"></span>
                    <span class="gp-rlbl">{{ nome }}</span>
                </label>
                {% endfor %}
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
                        <div class="gp-sub">
                            <label class="gp-switch sm" onclick="event.stopPropagation()">
                                <input type="checkbox" data-regiao="{{ reg.id }}" data-camada="{{ it.layer.get_name() }}" checked>
                                <span class="gp-slider"></span>
                            </label>
                            <span class="sw {{ it.forma }}" style="--c: {{ it.cor }}"></span>
                            <span class="gp-sub-lbl">{{ it.nome }}</span>
                            <span class="gp-cnt">{{ it.count }}</span>
                        </div>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
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

                // --- Accordion das regiões ---
                document.querySelectorAll('#gp-painel .gp-reg-head').forEach(function(h){
                    h.addEventListener('click', function(){ h.parentNode.classList.toggle('open'); });
                });

                // --- Mapas de fundo ---
                var bases = { {% for nome, layer in this.basemaps %}"{{ layer.get_name() }}": {{ layer.get_name() }}{% if not loop.last %}, {% endif %}{% endfor %} };
                var padrao = {{ this.default_base.get_name() }};
                Object.keys(bases).forEach(function(k){ if (map.hasLayer(bases[k])) map.removeLayer(bases[k]); });
                map.addLayer(padrao);
                document.querySelectorAll('input[name="gp-base"]').forEach(function(r){
                    r.addEventListener('change', function(){
                        Object.keys(bases).forEach(function(k){ if (map.hasLayer(bases[k])) map.removeLayer(bases[k]); });
                        map.addLayer(bases[this.value]);
                    });
                });

                // --- Camadas (referências) ---
                var camadas = { {% for reg in this.regioes %}{% for it in reg.itens %}"{{ it.layer.get_name() }}": {{ it.layer.get_name() }}, {% endfor %}{% endfor %} };

                // Toggle individual (Formato/Trechos/SRE dentro de cada região)
                document.querySelectorAll('#gp-painel input[data-camada]').forEach(function(t){
                    t.addEventListener('change', function(){
                        var lyr = camadas[this.getAttribute('data-camada')];
                        if (this.checked) { map.addLayer(lyr); } else { map.removeLayer(lyr); }
                        var rid = this.getAttribute('data-regiao');
                        var filhos = document.querySelectorAll('#gp-painel input[data-camada][data-regiao="'+rid+'"]');
                        var master = document.querySelector('#gp-painel input[data-regiao="'+rid+'"]:not([data-camada])');
                        var algum = Array.prototype.some.call(filhos, function(c){ return c.checked; });
                        if (master) master.checked = algum;
                    });
                });

                // Toggle mestre da região (liga/desliga tudo da região)
                document.querySelectorAll('#gp-painel input[data-regiao]:not([data-camada])').forEach(function(mst){
                    mst.addEventListener('change', function(){
                        var rid = this.getAttribute('data-regiao');
                        var on = this.checked;
                        document.querySelectorAll('#gp-painel input[data-camada][data-regiao="'+rid+'"]').forEach(function(c){
                            c.checked = on;
                            var lyr = camadas[c.getAttribute('data-camada')];
                            if (on) { map.addLayer(lyr); } else { map.removeLayer(lyr); }
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
    tl_rua = folium.TileLayer(
        tiles='https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
        attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
        name='Rua', control=False, max_zoom=21, max_native_zoom=19)
    tl_satelite = folium.TileLayer(
        tiles='https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr='Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
        name='Satélite', control=False, max_zoom=21, max_native_zoom=17)
    for tl in (tl_padrao, tl_rua, tl_satelite):
        tl.add_to(m)

    # 2. Ler shapefiles, organizando por REGIÃO -> TIPO
    #    regioes[rid][tipo] = {'fg': FeatureGroup, 'count': n}
    regioes = {}
    bounds = []

    for shp_file in sorted(camadas_dir.glob('*.shp')):
        nome = shp_file.stem
        low = nome.lower()

        tipo = detectar_tipo(low)
        if tipo is None:
            print(f"Aviso: tipo não reconhecido para '{nome}'. Ignorada.")
            continue

        # Identifica a região pelo prefixo (R1, R2, ..., R11, R13). Sem
        # prefixo -> 'OUTROS' (ex.: pontos críticos sem código de região).
        mm = re.match(r'^(R\d+)_', nome.upper())
        rid = mm.group(1) if mm else 'OUTROS'

        # Trava anti-duplicação: um tipo por região só entra uma vez
        if rid in regioes and tipo in regioes[rid]:
            print(f"Ignorando duplicata: {shp_file.name} ({rid}/{tipo})")
            continue

        print(f"Lendo camada: {shp_file.name}")
        try:
            gdf = gpd.read_file(shp_file)

            if gdf.crs and gdf.crs.to_string() != 'EPSG:4326':
                print(f"  Convertendo {shp_file.name} para EPSG:4326...")
                gdf = gdf.to_crs(epsg=4326)
            elif not gdf.crs:
                print(f"  Aviso: {shp_file.name} sem CRS. Assumindo EPSG:4326.")
                gdf.set_crs(epsg=4326, inplace=True)

            minx, miny, maxx, maxy = gdf.total_bounds
            bounds.append([[miny, minx], [maxy, maxx]])

            validar_nomenclatura_sre(gdf, nome)

            cols = [c for c in gdf.columns if c.lower() != 'geometry']
            popup = folium.GeoJsonPopup(fields=cols, aliases=cols, localize=True) if cols else None

            _, cor, _ = TIPO_META[tipo]
            style_function = lambda x, color=cor: {
                'color': color, 'fillColor': color, 'weight': 3, 'fillOpacity': 0.35
            }

            fg = folium.FeatureGroup(name=f'{rid}_{tipo}', show=True, control=False)
            folium.GeoJson(
                data=gdf, name=nome, popup=popup, style_function=style_function,
                marker=folium.CircleMarker(radius=6, color=cor, fill_color=cor,
                                           fill_opacity=0.9, weight=1)
            ).add_to(fg)

            regioes.setdefault(rid, {})[tipo] = {'fg': fg, 'count': len(gdf)}
            print(f"  '{nome}' -> {rid} / {tipo} ({len(gdf)} feições)")

        except Exception as e:
            print(f"  Erro ao processar {shp_file.name}: {e}")

    # Ordena as regiões de forma natural: R1, R2, R3, R11, R12, R13, OUTROS
    def rid_key(rid):
        mm = re.match(r'R(\d+)$', rid)
        return (0, int(mm.group(1))) if mm else (1, rid)
    ordem_regioes = sorted(regioes, key=rid_key)

    # Adiciona os grupos ao mapa na ordem de sobreposição (polígono -> pontos)
    for tipo in TIPO_ORDEM:
        for rid in ordem_regioes:
            item = regioes[rid].get(tipo)
            if item:
                item['fg'].add_to(m)

    # Enquadra todos os dados
    if bounds:
        min_lat = min(b[0][0] for b in bounds)
        min_lon = min(b[0][1] for b in bounds)
        max_lat = max(b[1][0] for b in bounds)
        max_lon = max(b[1][1] for b in bounds)
        m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]])

    # 3. Ferramentas de navegação
    Fullscreen(position='topright', title='Tela cheia',
               title_cancel='Sair da tela cheia', force_separate_button=True).add_to(m)
    MousePosition(position='bottomright', separator=' | ', prefix='Coordenadas:',
                  num_digits=6, lat_first=True).add_to(m)

    # 4. Monta a estrutura do painel (árvore por região)
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
            itens.append({'nome': label, 'layer': item['fg'], 'cor': cor,
                          'forma': forma, 'count': fmt(item['count'])})
        if itens:
            regioes_info.append({'id': rid, 'nome': nome_regiao(rid), 'itens': itens})

    # Logo da marca embutido (base64) para o mapa ficar autossuficiente
    logo_uri = None
    logo_path = base_dir / 'logo' / 'LOGO RTA.png'
    if logo_path.exists():
        import base64
        b64 = base64.b64encode(logo_path.read_bytes()).decode('ascii')
        logo_uri = f'data:image/png;base64,{b64}'
        print(f"Logo carregado: {logo_path.name}")
    else:
        print("Aviso: logo não encontrado; usando ícone padrão.")

    painel = PainelControle(
        basemaps=[('Padrão', tl_padrao), ('Rua', tl_rua), ('Satélite', tl_satelite)],
        default_base=tl_satelite,
        regioes=regioes_info,
        titulo='RTA WebGIS',
        subtitulo='WebGIS · Inventário Rodoviário — Tocantins',
        logo=logo_uri,
    )
    m.add_child(painel)

    m.save(output_file)
    print(f"Mapa salvo com sucesso em: {output_file}")
    print(f"Regiões: {ordem_regioes}")


if __name__ == "__main__":
    create_webgis()
