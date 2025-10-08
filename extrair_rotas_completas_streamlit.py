import streamlit as st
import requests
import re
import pandas as pd
from io import BytesIO
import base64

# ===========================================================
# 🔐 CONFIGURAÇÃO - Cloud Vision API
# ===========================================================
CLOUD_VISION_API_KEY = "AIzaSyBvn-tuocpPKF02OH_UaTsM5DE8_d6Ddwo"
VISION_URL = f"https://vision.googleapis.com/v1/images:annotate?key={CLOUD_VISION_API_KEY}"

# ===========================================================
# 🧾 Função de OCR com Google Cloud Vision
# ===========================================================
def google_vision_ocr(file_bytes):
    """
    Extrai texto da imagem usando o OCR da Cloud Vision API.
    Retorna o texto OCR ou uma string vazia se ocorrer erro.
    """
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

        # Verificar erro na resposta
        if "error" in data.get("responses", [{}])[0]:
            msg = data["responses"][0]["error"].get("message", "Erro desconhecido.")
            st.error(f"❌ Erro da Vision API: {msg}")
            return ""

        annotations = data["responses"][0].get("fullTextAnnotation")
        if annotations and "text" in annotations:
            return annotations["text"]

        # fallback para textAnnotations
        texts = data["responses"][0].get("textAnnotations")
        if texts and len(texts) > 0:
            return texts[0].get("description", "")

        st.warning("⚠️ Nenhum texto detectado na imagem.")
        return ""

    except Exception as e:
        st.error(f"❌ Falha ao chamar Vision API: {e}")
        return ""

# ===========================================================
# 🧩 Funções auxiliares para extração dos dados
# ===========================================================
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

def extrair_parada_do_bloco(bloco):
    texto = " ".join(bloco)
    # procura padrões de parada
    patterns = [
        r'\bETIQUETA\s*[#\-]?\s*(?:[A-Z\-]{0,6}[-\s]*)?(\d{1,4})\b',
        r'\bPARADA\s*[:\-]?\s*(\d{1,4})\b',
        r'\bP(?:ARADA)?\.?\s*[:#\-]?\s*(\d{1,4})\b',
        r'\bSTOP\s*[:\-]?\s*(\d{1,4})\b'
    ]
    for p in patterns:
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            return m.group(1)
    # tenta linha isolada
    for linha in bloco:
        if re.fullmatch(r'\d{1,4}', linha.strip()):
            return linha.strip()
    return None

def extrair_numero_residencial(linha, parada_num=None):
    numeros = re.findall(r'\b\d{1,5}\b', linha)
    for num in numeros:
        if parada_num and num == str(parada_num):
            continue
        if not re.match(r'\d{8}', num):
            return num
    return None

def processar_blocos(blocos):
    resultados = []
    for bloco in blocos:
        texto = " ".join(bloco)
        parada = extrair_parada_do_bloco(bloco)
        parada_str = f"Parada {parada}" if parada else ""
        cep, logradouro, bairro, cidade, estado, pacotes = "", "", "", "", "", ""

        # CEP
        match_cep = re.search(r'\b(\d{5})[-\s]?(\d{3})\b', texto)
        if match_cep:
            cep_raw = match_cep.group(1) + match_cep.group(2)
            via = consultar_viacep(cep_raw)
            if via:
                cep = via["CEP"]
                logradouro = via["Logradouro"]
                bairro = via["Bairro"]
                cidade = via["Cidade"]
                estado = via["Estado"]

        # Logradouro
        if not logradouro and bloco:
            m = re.match(r'^(Rua|Avenida|Av\.|Travessa|Alameda|Estrada)\b.*', bloco[0], re.IGNORECASE)
            if m:
                logradouro = bloco[0].strip()

        # Número
        numero = extrair_numero_residencial(texto, parada)
        numero = numero or "S/N"

        # Pacotes
        match_pac = re.search(r'(\d+)\s+(pacote|pacotes|unidade|unidades)', texto, re.IGNORECASE)
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
    return pd.DataFrame(resultados)

def ordenar_por_parada(df):
    def extrair_num(p):
        if isinstance(p, str):
            m = re.search(r'Parada\s*(\d+)', p)
            return int(m.group(1)) if m else float('inf')
        return float('inf')
    return df.sort_values(by='Parada', key=lambda col: col.map(extrair_num))

# ===========================================================
# 💻 Interface Streamlit
# ===========================================================
st.title("Extração de Dados OCR - Rotas (Google Cloud Vision)")

uploaded_files = st.file_uploader(
    "Selecione as imagens da rota",
    type=["jpg", "jpeg", "png", "webp", "tiff", "bmp"],
    accept_multiple_files=True
)

if uploaded_files:
    dfs = []
    for img in uploaded_files:
        st.write(f"📸 Processando: {img.name}")
        content = img.read()
        texto = google_vision_ocr(content)
        if not texto:
            continue
        linhas = [l.strip() for l in texto.splitlines() if l.strip()]
        blocos = extrair_blocos(linhas)
        df = processar_blocos(blocos)
        if not df.empty:
            dfs.append(df)

    if dfs:
        df_final = pd.concat(dfs, ignore_index=True)
        df_final = ordenar_por_parada(df_final)
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
