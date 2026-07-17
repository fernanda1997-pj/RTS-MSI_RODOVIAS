@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Atualizar WebGIS - RTA

echo.
echo ==========================================================
echo    ATUALIZAR WEBGIS  -  RTA Engenheiros Consultores
echo ==========================================================
echo.
echo  Este assistente vai:
echo    1. Reler os shapefiles e regerar o mapa
echo    2. Mostrar o relatorio de qualidade
echo    3. Abrir o mapa para voce conferir
echo    4. Publicar no site (com a sua confirmacao)
echo.
echo  IMPORTANTE: salve e feche o ArcGIS antes de continuar.
echo.
pause

echo.
echo ==========================================================
echo  [1/4]  Gerando o mapa a partir dos shapefiles...
echo ==========================================================
echo.

python gerar_mapa.py
if errorlevel 1 goto :erro_geracao

echo.
echo ==========================================================
echo  [2/4]  O que mudou
echo ==========================================================
echo.
git status --short
if errorlevel 1 goto :erro_git

echo.
git diff --stat --shortstat 2>nul
echo.
echo  ^>^> Leia acima as linhas [QUALIDADE]: elas apontam SRE
echo     vazio ou duplicado. Vale conferir antes de publicar.
echo.

echo ==========================================================
echo  [3/4]  Conferir antes de publicar
echo ==========================================================
echo.
echo  O mapa ja foi gerado aqui na sua maquina, mas ainda NAO
echo  foi publicado. Voce pode abri-lo para revisar.
echo.
set "VER="
set /p "VER=Abrir o mapa no navegador? (S = sim / Enter = pular): "
if /i "%VER%"=="S" (
    echo.
    echo  Abrindo o mapa... confira e depois volte para esta janela.
    echo.
    echo  Se abrir uma versao antiga, aperte Ctrl+Shift+R no navegador
    echo  para forcar o recarregamento.
    echo.
    start "" "mapa_interativo.html"
    echo  ^>^> Quando terminar de conferir, volte aqui e continue.
    echo.
    pause
)

echo.
echo ==========================================================
echo  [4/4]  Publicar
echo ==========================================================
echo.
echo  Ao publicar, o mapa vai para o GitHub e o Vercel
echo  atualiza o site sozinho em cerca de 1 minuto.
echo.
set "RESP="
set /p "RESP=Publicar agora? (S = sim / qualquer outra tecla = nao): "
if /i not "%RESP%"=="S" goto :cancelado

echo.
set "MSG="
set /p "MSG=Descreva a mudanca (ou so aperte Enter): "
if "%MSG%"=="" set "MSG=Atualiza mapa - %DATE%"

echo.
echo  Enviando...
git add -A
if errorlevel 1 goto :erro_git

git commit -m "%MSG%"
if errorlevel 1 goto :nada_mudou

git push
if errorlevel 1 goto :erro_push

echo.
echo ==========================================================
echo    PUBLICADO COM SUCESSO
echo ==========================================================
echo.
echo  O Vercel esta reconstruindo o site agora.
echo  Em cerca de 1 minuto a versao nova estara no ar.
echo.
echo  Dica: para ver a mudanca no seu navegador, abra o site
echo        e aperte Ctrl + Shift + R (recarregar forcado).
echo.
goto :fim

:erro_geracao
echo.
echo ==========================================================
echo    ERRO AO GERAR O MAPA  -  NADA FOI PUBLICADO
echo ==========================================================
echo.
echo  O site continua com a versao anterior, intacta.
echo.
echo  Causas comuns:
echo    - Shapefile aberto no ArcGIS (feche o programa)
echo    - Nome de arquivo fora do padrao R{numero}_{TIPO}
echo      Ex.: R2_TRECHOS.shp (certo) / Trechos R2.shp (errado)
echo    - Coluna renomeada (SRE, SITUACAO, RODOVIA, CIDADE_SED)
echo.
echo  Leia a mensagem de erro acima e corrija antes de tentar
echo  de novo. Se nao entender, mande o texto do erro.
echo.
goto :fim

:nada_mudou
echo.
echo  Nada novo para publicar - o mapa ja estava atualizado.
echo.
goto :fim

:erro_push
echo.
echo  ERRO ao enviar para o GitHub.
echo.
echo  O mapa foi gerado e salvo aqui, mas nao subiu.
echo  Verifique sua conexao com a internet e rode de novo.
echo.
goto :fim

:erro_git
echo.
echo  ERRO no Git. Verifique se a pasta ainda e um repositorio.
echo.
goto :fim

:cancelado
echo.
echo  Cancelado. O mapa foi regerado na sua maquina, mas NAO
echo  foi publicado. O site continua com a versao anterior.
echo.
echo  Voce pode abrir o mapa_interativo.html para conferir e
echo  rodar este assistente de novo quando quiser publicar.
echo.

:fim
echo.
pause
