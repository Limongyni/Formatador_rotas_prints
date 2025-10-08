import streamlit as st
import requests
import re
import pandas as pd
from io import BytesIO
import base64
import os

# -----------------------------
# CONFIGURAÇÃO (Cloud Vision)
# -----------------------------
CLOUD_VISION_API_KEY = os.getenv("CLOUD_VISION_API_KEY", "")
VISION_URL = f"https://vision.googleapis.com/v1/images:annotate?key={CLOUD_VISION_API_KEY}"

# -----------------------------
# OCR - Google Cloud Vision
# -----------------------------
def google_vision_ocr(file_bytes):
    if not CLOUD_VISION_API_KEY:
        st.error("Chave da Cloud Vision não encontrada. Configure CLOUD_VISION_API_KEY.")
        return ""
    try:
        img_b64 = base64.b64encode(file_bytes).decode("utf-8")
        payload = {
            "requests": [
                {
                    "image": {"content": img_b64},
                    "features": [{"type": "TEXT_DETECTION"}],
                    "imageContext": {"languageHints": ["pt"]}
                }
            ]
        }
        resp = requests.post(VISION_URL, json=payload, timeout=60)
        if resp.status_code != 200:
            st.error(f"❌ Erro HTTP {resp.status_code} ao chamar Vision API.")
            st.text(resp.text)
            return ""
        data = resp.json()
        if "error" in data.get("responses", [{}])[0]:
            msg = data["responses"][0]["error"].get("message", "Erro desconhecido.")
            st.error(f"❌ Erro da Vision API: {msg}")
            return ""
        annotations = data["responses"][0].get("fullTextAnnotation")
        if annotations and "text" in annotations:
            return annotations["text"]
        texts = data["responses"][0].get("textAnnotations")
        if texts and len(texts) > 0:
            return texts[0].get("description", "")
        return ""
    except Exception as e:
        st.error(f"❌ Falha ao chamar Vision API: {e}")
        return ""

# -----------------------------
# Regras de extração da PARADA
# -----------------------------
def extrair_parada_via_etiqueta(texto_bloco):
    """
    Extrai número da parada com base em padrões NX + número + separador + parada.
    Aceita múltiplas variações: espaços, hífens, underscores e ordem flexível.
    """
    if not texto_bloco:
        return None
    t = texto_bloco.upper()

    # Padrão 1: ETIQUETA (opcional nº) NX1234_05 / NX1234-05 / NX 1234 05
    padrao_geral = re.compile(
        r'(?:ETIQUETA\s*\d{0,3}\s*)?(?:N\s*X|NX)\s*[-:]?\s*(\d{1,10})\s*[_\-\s\.]?\s*(\d{1,4})\b',
        re.IGNORECASE
    )
    m = padrao_geral.search(t)
    if m:
        return m.group(2).lstrip("0") or m.group(2)

    # Padrão 2: ETIQUETA <num> NX 1234 - 05 (variação invertida)
    padrao_alt = re.compile(
        r'(?:ETIQUETA\s*\d{1,3}\s*)?(?:N\s*X|NX)?\s*[-:]?\s*(\d{1,10})\s*[-_\s\.]\s*(\d{1,4})\b',
        re.IGNORECASE
    )
    m2 = padrao_alt.search(t)
    if m2:
        return m2.group(2).lstrip("0") or m2.group(2)

    return None


def extrair_parada_por_palavra(texto_bloco):
    if not texto_bloco:
        return None
    t = texto_bloco.upper()
    m = re.search(r'\bPARADA\b[\s:\-]*?(\d{1,4})', t)
    if m:
        return m.group(1).lstrip("0") or m.group(1)
    m2 = re.search(r'PARADA\s*(\d{1,4})', t)
    if m2:
        return m2.group(1).lstrip("0") or m2.group(1)
    return None

# -----------------------------
# Extração de endereço e CEP
# -----------------------------
def consultar_viacep(cep):
    try:
        resp = requests.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=10)
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
    except Exception:
        pass
    return {}

def extrair_blocos(linhas):
    blocos, bloco = [], []
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

def extrair_numero_residencial(linha, parada_num=None):
    possiveis = re.findall(r'\b\d{1,5}\b', linha)
    for n in possiveis:
        if parada_num and n == str(parada_num):
            continue
        if re.fullmatch(r'\d{8}', n):
            continue
        return n
    return None

# -----------------------------
# Processamento dos blocos
# -----------------------------
def processar_blocos(blocos, debug=False):
    resultados = []
    for i, bloco in enumerate(blocos):
        texto_bloco = " ".join(bloco)
        parada_num = extrair_parada_via_etiqueta(texto_bloco) or extrair_parada_por_palavra(texto_bloco)
        parada_str = f"Parada {parada_num}" if parada_num else ""

        cep, logradouro, bairro, cidade, estado, numero, pacotes = "", "", "", "", "", "", ""

        match_cep = re.search(r'\b(\d{5})[-\s]?(\d{3})\b', texto_bloco)
        if match_cep:
            cep_raw = match_cep.group(1) + match_cep.group(2)
            via = consultar_viacep(cep_raw)
            if via:
                cep, logradouro, bairro, cidade, estado = via.values()

        if not logradouro and bloco:
            primeira = bloco[0].strip()
            if re.match(r'^(Rua|Avenida|Av\.|Travessa|Alameda|Estrada)\b', primeira, re.IGNORECASE):
                logradouro = primeira

        numero = extrair_numero_residencial(texto_bloco, parada_num) or "S/N"

        match_pac = re.search(r'(\d+)\s+(pacote|pacotes|unidade|unidades)', texto_bloco, re.IGNORECASE)
        if match_pac:
            qtd = match_pac.group(1)
            pacotes = f"{qtd} {'pacote' if qtd == '1' else 'pacotes'}"

        if parada_str or cep:
            resultados.append({
                "Parada": parada_str,
                "Address Line": f"{logradouro} {numero}".strip(),
                "Secondary Address Line": bairro,
                "City": cidade,
                "State": estado,
                "Zip Code": cep,
                "Total de Pacotes": pacotes
            })

        if debug:
            st.write(f"--- Bloco {i} ---")
            st.write("Texto:", texto_bloco)
            st.write("→ Parada:", parada_str)

    return pd.DataFrame(resultados)

# -----------------------------
# Ordenar e validar duplicidades
# -----------------------------
def ordenar_por_parada(df):
    def extrair_num(parada):
        if isinstance(parada, str) and parada:
            m = re.search(r'Parada\s*(\d+)', parada)
            return int(m.group(1)) if m else float('inf')
        return float('inf')
    return df.sort_values(by='Parada', key=lambda col: col.map(extrair_num))

def remover_duplicatas(df):
    cols_chave = ['Address Line', 'Secondary Address Line', 'City', 'Zip Code']
    return df.drop_duplicates(subset=cols_chave, keep='first')

# -----------------------------
# INTERFACE STREAMLIT
# -----------------------------
st.title("📦 Extração OCR de Rotas (Etiqueta → Parada)")

debug = st.sidebar.checkbox("Modo debug (mostrar detalhes por bloco)", False)
uploaded_files = st.file_uploader(
    "Selecione as imagens das rotas",
    type=["jpg", "jpeg", "png", "webp", "tiff", "bmp"],
    accept_multiple_files=True
)

if uploaded_files:
    df_geral = []
    for uploaded_file in uploaded_files:
        st.write(f"📸 Processando: {uploaded_file.name}")
        bytes_imagem = uploaded_file.read()
        texto = google_vision_ocr(bytes_imagem)
        if not texto:
            st.warning(f"⚠️ Nenhum texto extraído de {uploaded_file.name}.")
            continue
        linhas = [l.strip() for l in texto.splitlines() if l.strip()]
        blocos = extrair_blocos(linhas)
        df = processar_blocos(blocos, debug=debug)
        if not df.empty:
            df_geral.append(df)

    if df_geral:
        df_final = pd.concat(df_geral, ignore_index=True)
        df_final = remover_duplicatas(df_final)
        df_final = ordenar_por_parada(df_final)

        col_order = ['Parada','Address Line','Secondary Address Line','City','State','Zip Code','Total de Pacotes']
        for c in col_order:
            if c not in df_final.columns:
                df_final[c] = ""
        df_final = df_final[col_order]

        st.success("✅ Dados extraídos com sucesso!")
        st.dataframe(df_final)

        # ⚠️ Avisar endereços sem parada
        faltando_parada = df_final[df_final['Parada'] == ""]
        if not faltando_parada.empty:
            st.warning("⚠️ Endereços sem parada detectados:")
            st.dataframe(faltando_parada[['Address Line','City','Zip Code']])

        # 📥 Botão de download
        buffer = BytesIO()
        df_final.to_excel(buffer, index=False)
        buffer.seek(0)
        st.download_button(
            "📥 Baixar Excel Final",
            data=buffer,
            file_name="rotas_extraidas_final.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Nenhum dado extraído das imagens enviadas.")
else:
    st.info("Envie as imagens das rotas para iniciar a extração.")
