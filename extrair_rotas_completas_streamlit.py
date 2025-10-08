import streamlit as st
import requests
import re
import pandas as pd
from io import BytesIO

GOOGLE_VISION_API_KEY = "AIzaSyBvn-tuocpPKF02OH_UaTsM5DE8_d6Ddwo"

def google_vision_ocr(image_bytes):
    """Usa Google Cloud Vision API para extrair texto de uma imagem."""
    url = f"https://vision.googleapis.com/v1/images:annotate?key={GOOGLE_VISION_API_KEY}"
    payload = {
        "requests": [{
            "image": {"content": image_bytes.decode("ISO-8859-1")},
            "features": [{"type": "TEXT_DETECTION"}]
        }]
    }
    response = requests.post(url, json=payload)
    if response.status_code != 200:
        st.error(f"❌ Erro HTTP {response.status_code} ao chamar Vision API.")
        return ""
    result = response.json()
    try:
        return result["responses"][0]["fullTextAnnotation"]["text"]
    except KeyError:
        return ""

def consultar_viacep(cep):
    url = f"https://viacep.com.br/ws/{cep}/json/"
    try:
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            if "erro" not in data:
                return {
                    "CEP": f"{cep[:5]}-{cep[5:]}",
                    "Logradouro": data.get("logradouro", ""),
                    "Bairro": data.get("bairro", ""),
                    "Cidade": data.get("localidade", ""),
                    "Estado": data.get("uf", "")
                }
    except:
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

def extrair_numero_residencial(linha):
    nums = re.findall(r'\b\d{1,5}\b', linha)
    for num in nums:
        if not re.match(r'\d{5}-\d{3}|\d{8}', num):
            return num
    return "S/N"

def processar_blocos(blocos):
    resultados = []
    for bloco in blocos:
        texto_bloco = " ".join(bloco)

        # 🔍 Capturar número da parada com regex flexível
        match_parada = re.search(
            r'ETIQUETA(?:\s+\d+)?\s*(?:NX|N\s*X)?\s*[-\s]*\d+\s*[_\-,\s]+\s*(\d{1,3})',
            texto_bloco, re.IGNORECASE
        )
        parada = f"Parada {match_parada.group(1)}" if match_parada else ""

        # 📦 CEP e endereço
        match_cep = re.search(r'\b(\d{5})[-\s]?(\d{3})\b', texto_bloco)
        cep, logradouro, bairro, cidade, estado = "", "", "", "", ""
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
            logradouro = bloco[0].strip()

        numero = extrair_numero_residencial(texto_bloco)
        match_pac = re.search(r'(\d+)\s+(pacote|pacotes|unidade|unidades)', texto_bloco, re.IGNORECASE)
        pacotes = f"{match_pac.group(1)} pacotes" if match_pac else ""

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
            match = re.search(r'(\d+)', parada)
            return int(match.group(1)) if match else float('inf')
        return float('inf')
    return df.sort_values(by='Parada', key=lambda col: col.map(extrair_num))

# === Streamlit Interface ===
st.title("🧾 Extração de Dados OCR - Rotas")

uploaded_files = st.file_uploader("Selecione as imagens", type=["jpg", "jpeg", "png"], accept_multiple_files=True)

if uploaded_files:
    dfs = []
    for img in uploaded_files:
        st.write(f"📸 Processando: {img.name}")
        texto = google_vision_ocr(img.read().encode("ISO-8859-1"))
        linhas = [l.strip() for l in texto.splitlines() if l.strip()]
        blocos = extrair_blocos(linhas)
        df = processar_blocos(blocos)
        dfs.append(df)

    if dfs:
        df_final = pd.concat(dfs, ignore_index=True)
        df_final = ordenar_por_parada(df_final)

        # 🔁 Remover duplicatas
        df_final.drop_duplicates(subset=["Address Line", "Secondary Address Line", "City", "Zip Code"], inplace=True)

        # ⚠️ Mostrar endereços sem parada
        sem_parada = df_final[df_final["Parada"].astype(str).str.strip() == ""]
        if not sem_parada.empty:
            st.warning("⚠️ Os seguintes endereços não tiveram parada captada:")
            st.dataframe(sem_parada[["Address Line", "Secondary Address Line", "City", "Zip Code"]])

        st.success("✅ Dados extraídos:")
        st.dataframe(df_final)

        # Exportar Excel
        output = BytesIO()
        df_final.to_excel(output, index=False)
        output.seek(0)
        st.download_button(
            "📥 Baixar Excel",
            data=output,
            file_name="rotas_extraidas_final.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
else:
    st.info("Por favor, envie imagens para iniciar a extração.")
