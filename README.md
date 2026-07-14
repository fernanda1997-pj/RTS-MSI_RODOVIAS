# RTS-MSI · WebGIS RODOVIAS — RTA Engenheiros Consultores

WebGIS interativo para inventário rodoviário no estado do **Tocantins**, gerado
com Python (Folium + GeoPandas) a partir de shapefiles das regiões.

## O que o mapa mostra

- **6 regiões** (R1, R2, R3, R11, R12, R13), cada uma com:
  - **Formato da Região** (polígono)
  - **Trechos** (eixos rodoviários)
  - **Pontos SRE** (código do Sistema Rodoviário Estadual)
- Painel de controle unificado (estilo geoportal) com identidade visual da RTA:
  - Seletor de mapa de fundo: **Satélite / Padrão / Rua**
  - Camadas em árvore **por região**, com interruptores e contagem de feições
  - Legenda temática, tela cheia e coordenadas do cursor

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

## Publicação

Hospedado no **Vercel** como site estático. O `vercel.json` serve o
`mapa_interativo.html` na raiz do domínio.

---
© RTA Engenheiros Consultores
