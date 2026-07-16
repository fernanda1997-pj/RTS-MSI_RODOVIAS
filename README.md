# RTS-MSI · WebGIS RODOVIAS — RTA Engenheiros Consultores

WebGIS interativo para inventário rodoviário no estado do **Tocantins**, gerado
com Python (Folium + GeoPandas) a partir de shapefiles das regiões.

## O que o mapa mostra

Painel de controle unificado com a identidade visual da RTA:

- **Mapas de fundo:** Padrão (OSM) · Satélite (Esri) · Escuro (Carto)
- **Camadas por região** — árvore de 4 níveis, cada nível com interruptor:
  ```
  Região 1
    ├ Formato da Região
    ├ Trechos (212)
    │   └ PPS · Pavimentada pista simples (82)
    │        └ 010ETO0522   ← clique voa até o trecho
    └ Pontos SRE (424)
  ```
- **Camadas de contexto:** Limite do Estado · Hidrografia (rios principais)
- **Busca & filtros:** busca por SRE, rodovia ou cidade (sem acento também);
  chips para ligar/desligar cada situação em todas as regiões
- Tela cheia e coordenadas do cursor

### Situação dos trechos (códigos do SRE)

Os trechos são coloridos por situação, com as cores agrupadas por família:

| Família | Códigos |
|---|---|
| 🟢 Duplicadas | `DUP` duplicada · `PDU` duplicada em perímetro urbano |
| 🔵 Pavimentadas simples | `PPS` pavimentada pista simples · `PSU` simples urbana |
| 🟠 Em obras | `EOD` em obras de duplicação · `EOP` em obras de pavimentação |
| 🟤 Não pavimentadas | `IMP` implantada · `LEN` leito natural |
| ⚪ Planejada | `PLA` planejada |

## Estrutura

| Caminho | Descrição |
|---|---|
| `gerar_mapa.py` | Script que lê os shapefiles e gera o mapa |
| `camadas/` | Shapefiles de origem (regiões, trechos, pontos SRE) |
| `logo/` | Logo da RTA usado no painel |
| `mapa_interativo.html` | **Mapa final** (página publicada) |
| `vercel.json` | Configuração de publicação no Vercel |

## Como regenerar o mapa

Requisitos: Python 3.x com `folium` e `geopandas`.

```bash
pip install folium geopandas
python gerar_mapa.py
```

O arquivo `mapa_interativo.html` é recriado com os dados atuais da pasta `camadas/`.

### ⚠️ Hidrografia não está no repositório

Os shapefiles de hidrografia ficaram **fora do Git** por ultrapassarem o limite
do GitHub (`HIDROGRAFIA.dbf` tem 243 MB; o limite é 100 MB por arquivo).

- O **site publicado funciona normalmente** — o `mapa_interativo.html` já embute
  a hidrografia filtrada (ordem ≤ 3, ~4.786 rios, simplificada).
- Para **regenerar o mapa com hidrografia**, mantenha `hidrografia_estado.shp`
  na pasta `camadas/` da sua máquina. Sem ele, o script apenas omite a camada.

O script ignora automaticamente duplicatas e bases brutas
(`esatado_tocantins`, `hidrografia_estado_1`, `HIDROGRAFIA`).

## Publicação

Hospedado no **Vercel** como site estático. O `vercel.json` serve o
`mapa_interativo.html` na raiz do domínio.

---
© RTA Engenheiros Consultores
