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
CLOUD_VISION_API_KEY = os.getenv("CLOUD_VISION_API_KEY", "AIzaSyBvn-tuocpPKF02OH_UaTsM5DE8_d6Ddwo")
VISION_URL = f"https://vision.googleapis.com/v1/images:annotate?key={CLOUD_VISION_API_KEY}"

# -----------------------------
# Função OCR - Google Cloud Vision
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
    Extrai número da parada a partir de etiquetas do tipo:
    - 'Etiqueta ## NX1234_5' ou 'NX1234-5' -> retorna '5'
    aceita variações de espaços, hífens, pontos ou underscores.
    """
    if not texto_bloco:
        return None
    t = texto_bloco.upper()

    # 1) Padrão preferencial: NX <nums> <sep> <PARADA>
    # ex: NX1234_5, NX1234-5, NX1234.5
    pat_nx = re.compile(r'(?:N\s*X|NX)\s*[:\-]?\s*(\d{1,12})\s*[_\-\.\s]+\s*(\d{1,4})', re.IGNORECASE)
    m = pat_nx.search(t)
    if m:
        return m.group(2).lstrip("0") or m.group(2)

    # 2) Padrão com a palavra ETIQUETA antes
    pat_etq_nx = re.compile(
        r'(?:ETIQUETA|ETIQ|ETI)\b[^\d\n]{0,40}(?:N\s*X|NX)?\s*[:\-]?\s*(\d{1,12})\s*[_\-\.\s]+\s*(\d{1,4})',
        re.IGNORECASE
    )
    m2 = pat_etq_nx.search(t)
    if m2:
        return m2.group(2).lstrip("0") or m2.group(2)

    # 3) Caso OCR tenha removido NX
    pat_etq_sep = re.compile(
        r'(?:ETIQUETA|ETIQ|ETI)\b.{0,40}?(\d{1,12})\s*[_\-\.\s]+\s*(\d{1,4})',
        re.IGNORECASE
    )
    m3 = pat_etq_sep.search(t)
    if m3:
        return m3.group(2).lstrip("0") or m3.group(2)

    # 4) fallback: NX\d+[_\-\.\s]\d+
    m4 = re.search(r'NX\d+[_\-\.\s](\d{1,4})', t, re.IGNORECASE)
    if m4:
        return m4.group(1).lstrip("0") or m4.group(1)

    return None


def extrair_parada_por_palavra(texto_bloco):
    if not texto_bloco:
        return None
    t = texto_bloco.upper()
    m = re.search(r'\bPARADA\b[\s:\-]*?(\d{1,4})', t, re.IGNORECASE)
    if m:
        return m.group(1).lstrip("0") or m.group(1)
    m2 = re.search(r'PARADA\s*?(\d{1,4})', t, re.IGNORECASE)
    if m2:
        return m2.group(1).lstrip("0") or m2.group(1)
    m3 = re.search(r'\bP\.?\s*(\d{1,4})\b', t)
    if m3:
        return m3.group(1).lstrip("0") or m3.group(1)
    return None

# -----------------------------
# Outras funções de parsing
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

def extrair_numero_residencial(linha, parada_num=None):
    possiveis = re.findall(r'\b\d{1,5}\b', linha)
    for n in possiveis:
        if parada_num and n == str(parada_num):
            continue
        if re.fullmatch(r'\d{8}', n):
            continue
        if re.search(r'[A-Za-z]', n):
            continue
        return n
    return None

def processar_blocos(blocos, debug=False):
    resultados = []
    for i, bloco in enumerate(blocos):
        texto_bloco = " ".join(bloco)
        parada_num = extrair_parada_via_etiqueta(texto_bloco)
        metodo = "etiqueta"
        if not parada_num:
            parada_num = extrair_parada_por_palavra(texto_bloco)
            metodo = "palavra" if parada_num else None

        parada_str = f"Parada {parada_num}" if parada_num else ""
        cep = ""
        logradouro = ""
        bairro = ""
        cidade = ""
        estado = ""

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

        if not logradouro and bloco:
            primeira = bloco[0].strip()
            if re.match(r'^(Rua|Avenida|Av\.|Travessa|Alameda|Estrada)\b', primeira, re.IGNORECASE):
                logradouro = primeira

        numero = extrair_numero_residencial(texto_bloco, parada_num=parada_num)
        numero = numero or "S/N"

        pacotes = ""
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
            st.write("LINHAS:", bloco)
            st.write("TEXTO:", texto_bloco)
            st.write("parada_num:", parada_num, "metodo:", metodo)
            st.write("logradouro:", logradouro, "numero:", numero, "cep:", cep)
    return pd.DataFrame(resultados)

def ordenar_por_parada(df):
    def extrair_num(parada):
        if isinstance(parada, str) and parada:
            m = re.search(r'Parada\s*(\d+)', parada)
            return int(m.group(1)) if m else float('inf')
        return float('inf')
    if 'Parada' in df.columns:
        return df.sort_values(by='Parada', key=lambda col: col.map(extrair_num))
    return df

# -----------------------------
# Interface Streamlit
# -----------------------------
st.title("Extração de Dados OCR - Rotas (Etiqueta -> Parada)")

debug = st.sidebar.checkbox("Modo debug (mostrar detalhes por bloco)", False)

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
        df_final = ordenar_por_parada(df_final)
        col_order = ['Parada','Address Line','Secondary Address Line','City','State','Zip Code','Total de Pacotes']
        for c in col_order:
            if c not in df_final.columns:
                df_final[c] = ""
        df_final = df_final[col_order]

        st.success("✅ Dados extraídos:")
        st.dataframe(df_final)

        buffer = BytesIO()
        df_final.to_excel(buffer, index=False)
        buffer.seek(0)
        st.download_button(
            "📥 Baixar Excel",
            data=buffer,
            file_name="rotas_extraidas_final.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Nenhum dado extraído das imagens enviadas.")
else:
    st.info("Por favor, envie as imagens das rotas para iniciar a extração.")
