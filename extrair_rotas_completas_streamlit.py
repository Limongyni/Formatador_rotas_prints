import streamlit as st
import requests
import re
import pandas as pd
from io import BytesIO
import base64
import os

# ----------------------------
# CONFIGURAÇÃO
# ----------------------------
# Use a chave que você forneceu. Para segurança recomenda-se usar variável de ambiente.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyC2vwqj-_8CEZxL-u2FXi8LNvE8tpIC2BE")

# ----------------------------
# Função OCR usando Google Vision (images:annotate)
# ----------------------------
def gemini_vision_image_file(file_bytes, api_key=GEMINI_API_KEY):
    """
    Usa o endpoint images:annotate do Google Vision (funciona com API key).
    Retorna o texto OCR em português.
    """
    try:
        b64 = base64.b64encode(file_bytes).decode("utf-8")
        payload = {
            "requests": [
                {
                    "image": {"content": b64},
                    "features": [{"type": "TEXT_DETECTION", "maxResults": 1}],
                    "imageContext": {"languageHints": ["pt"]}
                }
            ]
        }
        resp = requests.post(
            f"https://vision.googleapis.com/v1/images:annotate?key={api_key}",
            json=payload,
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        if "responses" in data and len(data["responses"]) > 0:
            first = data["responses"][0]
            if "error" in first:
                st.error(f"❌ Erro OCR (Vision API): {first['error'].get('message')}")
                return ""
            # fullTextAnnotation quando disponível tem texto completo
            text = first.get("fullTextAnnotation", {}).get("text")
            if not text:
                # fallback para textAnnotations[0].description
                text = first.get("textAnnotations", [{}])[0].get("description", "")
            return text or ""
        else:
            st.error("❌ Resposta inesperada da Vision API.")
            return ""
    except Exception as e:
        st.error(f"❌ Falha ao chamar Vision API: {e}")
        return ""

# ----------------------------
# Funções de apoio para extração/parsing
# ----------------------------
def consultar_viacep(cep):
    url = f"https://viacep.com.br/ws/{cep}/json/"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "erro" not in data:
                return {
                    "CEP": f"{cep[:5]}-{cep[5:]}",
                    "Logradouro": data.get("logradouro", ""),
                    "Bairro": data.get("bairro", ""),
                    "Cidade": data.get("localidade", ""),
                    "Estado": "São Paulo" if data.get("uf", "") == "SP" else data.get("uf", "")
                }
    except:
        pass
    return {}

def extrair_blocos(linhas):
    blocos = []
    bloco = []
    for linha in linhas:
        if re.match(r'^(Rua|Avenida|Av\.|Travessa|Alameda|Estrada)\b', linha, re.IGNORECASE):
            if bloco:
                blocos.append(bloco)
            bloco = [linha]
        elif "estou chegando" in linha.lower():
            bloco.append(linha)
            blocos.append(bloco)
            bloco = []
        else:
            bloco.append(linha)
    if bloco:
        blocos.append(bloco)
    return blocos

def extrair_parada_do_bloco(bloco):
    """
    Procura várias formas de indicar a parada dentro do bloco.
    Retorna o número (string) se encontrado, ou None.
    """
    texto = " ".join(bloco)

    # 1) Patterns preferenciais (ETIQUETA / PARADA)
    patterns = [
        r'\bETIQUETA\s*[#\-]?\s*(?:[A-Z\-]{0,6}[-\s]*)?(\d{1,4})\b',   # ETIQUETA AA-123 ou ETIQUETA 123
        r'\bPARADA\s*[:\-]?\s*(\d{1,4})\b',                            # Parada 12
        r'\bP(?:ARADA)?\.?\s*[:#\-]?\s*(\d{1,4})\b',                   # P. 12 ou P 12
        r'\bSTOP\s*[:\-]?\s*(\d{1,4})\b'                              # STOP 12 (por convenção)
    ]
    for p in patterns:
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            return m.group(1)

    # 2) Buscar em cada linha para pegar formatos lineares: "Parada 12", "ETIQUETA ..."
    for linha in bloco:
        m = re.search(r'\bParada\b.*?(\d{1,4})', linha, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r'\bETIQUETA\b.*?(\d{1,4})', linha, re.IGNORECASE)
        if m:
            return m.group(1)

    # 3) Caso extremo: linha que contém apenas um número ou "12 - Nome" no início -> pode ser parada
    for linha in bloco:
        s = linha.strip()
        if re.fullmatch(r'\d{1,4}', s):
            return s
        m = re.match(r'^(\d{1,4})\s*[\-\:\)]', s)
        if m:
            return m.group(1)

    return None

def eh_numero_invalido_para_residencia(numero_str):
    """
    Detecta números que não deveriam ser considerados como número de residência
    (e.g. CEPs longos, códigos de etiqueta que contenham letras etc.)
    """
    # CEP ou sequência longa (8 dígitos) -> inválido para número residencial
    if re.match(r'^\d{8}$', numero_str):
        return True
    # sequências típicas de etiqueta com letras (ex: AB-123) -> inválido
    if re.search(r'[A-Za-z]', numero_str):
        return True
    return False

def extrair_numero_residencial(linha, parada_num=None):
    possiveis_numeros = re.findall(r'\b\d{1,5}\b', linha)
    for num in possiveis_numeros:
        if parada_num and num == str(parada_num):
            continue
        if eh_numero_invalido_para_residencia(num):
            continue
        # se passou nas checagens, consideramos número residencial
        return num
    return None

def processar_blocos(blocos):
    resultados = []

    for bloco in blocos:
        texto_bloco = " ".join(bloco)
        parada = ""
        cep = ""
        logradouro = ""
        numero = ""
        bairro = ""
        cidade = ""
        estado = ""
        pacotes = ""

        # EXTRAI A PARADA (mais robusto)
        parada_num = extrair_parada_do_bloco(bloco)
        if parada_num:
            parada = f"Parada {parada_num}"

        # EXTRAI CEP (formato 8 ou 5-3)
        match_cep = re.search(r'\b(\d{5})[-\s]?(\d{3})\b', texto_bloco)
        if match_cep:
            cep_raw = match_cep.group(1) + match_cep.group(2)
            via = consultar_viacep(cep_raw)
            if via:
                cep = via["CEP"]
                logradouro = via["Logradouro"]
                bairro = via["Bairro"]
                cidade = via["Cidade"]
                estado = via["Estado"]

        # Se não houver logradouro definido via viacep, tentar pegar pela primeira linha do bloco
        if not logradouro and bloco:
            primeira_linha = bloco[0]
            match_log_num = re.match(r'^(Rua|Avenida|Av\.|Travessa|Alameda|Estrada)\b.*', primeira_linha, re.IGNORECASE)
            if match_log_num:
                logradouro = primeira_linha.strip()

        # NUMERO RESIDENCIAL
        numero = extrair_numero_residencial(texto_bloco, parada_num=parada_num)
        if not numero:
            numero = "S/N"

        # PACOTES
        match_pac = re.search(r'(\d+)\s+(pacote|pacotes|unidade|unidades)', texto_bloco, re.IGNORECASE)
        if match_pac:
            qtd = match_pac.group(1)
            pacotes = "1 pacote" if qtd == "1" else f"{qtd} pacotes"

        # Incluir o bloco se ao menos existir parada ou cep (antes: era ambos)
        if not parada and not cep:
            # ignora blocos que não têm referência de parada nem CEP
            continue

        resultados.append({
            "Parada": parada,
            "Address Line": f"{logradouro} {numero}".strip(),
            "Secondary Address Line": bairro,
            "City": cidade,
            "State": estado,
            "Zip Code": cep,
            "Total de Pacotes": pacotes
        })

    return pd.DataFrame(resultados)

def ordenar_por_parada(df):
    def extrair_num(parada):
        if isinstance(parada, str):
            match = re.search(r'Parada\s*(\d+)', parada)
            return int(match.group(1)) if match else float('inf')
        return float('inf')
    # se coluna Parada não existir, retorna como está
    if 'Parada' not in df.columns:
        return df
    return df.sort_values(by='Parada', key=lambda col: col.map(extrair_num))

# ----------------------------
# INTERFACE STREAMLIT
# ----------------------------
st.title("Extração de Dados OCR - Rotas (Gemini / Vision)")

uploaded_files = st.file_uploader(
    "Selecione as imagens da rota",
    type=["jpg", "jpeg", "png", "webp", "tiff", "bmp"],
    accept_multiple_files=True
)

if uploaded_files:
    df_geral = []

    for uploaded_file in uploaded_files:
        st.write(f"📷 Processando: {uploaded_file.name}")
        bytes_imagem = uploaded_file.read()
        # chama o OCR (substituição do OCR.space)
        texto = gemini_vision_image_file(bytes_imagem)
        if not texto:
            st.warning(f"⚠️ Nenhum texto extraído de {uploaded_file.name}. Pulando.")
            continue

        linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
        blocos = extrair_blocos(linhas)
        df = processar_blocos(blocos)
        if not df.empty:
            df_geral.append(df)

    if df_geral:
        df_final = pd.concat(df_geral, ignore_index=True)
        df_final = ordenar_por_parada(df_final)

        st.success("✅ Dados extraídos:")
        st.dataframe(df_final)

        # Gerar Excel para download
        output = BytesIO()
        df_final.to_excel(output, index=False)
        output.seek(0)

        st.download_button(
            label="📥 Baixar Excel",
            data=output,
            file_name="rotas_extraidas_final.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.info("Nenhum dado extraído das imagens fornecidas.")
else:
    st.info("Por favor, faça upload das imagens para iniciar a extração.")
