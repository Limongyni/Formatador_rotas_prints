import streamlit as st
import requests
import re
import pandas as pd
from io import BytesIO

OCR_SPACE_API_KEY = "K85411442288957"

def ocr_space_image_file(file_bytes, api_key=OCR_SPACE_API_KEY):
    response = requests.post(
        'https://api.ocr.space/parse/image',
        files={'filename': ('image.jpg', file_bytes)},
        data={
            'apikey': api_key,
            'language': 'por',
            'isOverlayRequired': False,
            'OCREngine': 2
        }
    )
    result = response.json()
    if result.get('IsErroredOnProcessing'):
        st.error(f"❌ Erro ao processar a imagem: {result.get('ErrorMessage')}")
        return ""
    return result['ParsedResults'][0]['ParsedText']

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
                    "Estado": "São Paulo" if data.get("uf", "") == "SP" else data.get("uf", "")
                }
    except:
        pass
    return {}

def extrair_blocos(linhas):
    blocos = []
    bloco = []
    for linha in linhas:
        if re.match(r'^(Rua|Avenida|Av\.|Travessa|Alameda|Estrada|\d+)\b', linha, re.IGNORECASE):
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

def eh_numero_invalido(numero):
    if re.match(r'(Parada\s*\d{1,3}|ETIQUETA\s+[A-Z\-]*\d+|NX\d+)', numero, re.IGNORECASE):
        return True
    if re.match(r'\d{5}-\d{3}|\d{8}', numero):
        return True
    return False

def extrair_numero_residencial(linha, parada_num=None):
    possiveis_numeros = re.findall(r'\b\d{1,5}\b', linha)
    for num in possiveis_numeros:
        if not eh_numero_invalido(num):
            if parada_num and num == str(parada_num):
                continue
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

        # 1. Captura número da parada no início
        match_inicio = re.match(r'^\s*(\d{1,3})\b', bloco[0])
        if match_inicio:
            parada_num = match_inicio.group(1)
            parada = f"Parada {parada_num}"
        else:
            parada_num = None

        # 2. Fallback: captura parada pelo texto
        if not parada:
            match_parada = re.search(r'ETIQUETA\s+[#\-]?[A-Z]{2,3}[-\s]?(\d{1,4})', texto_bloco, re.IGNORECASE)
            if not match_parada:
                match_parada = re.search(r'Parada\s*(\d{1,4})', texto_bloco, re.IGNORECASE)
            parada_num = match_parada.group(1) if match_parada else None
            if parada_num:
                parada = f"Parada {parada_num}"

        # 3. Captura CEP
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

        # 4. Logradouro
        if not logradouro and bloco:
            primeira_linha = bloco[0]
            match_log_num = re.match(r'^(?:\d{1,3}\s+)?(Rua|Avenida|Av\.|Travessa|Alameda|Estrada)\b.*', primeira_linha, re.IGNORECASE)
            if match_log_num:
                logradouro = primeira_linha.strip()

        # 5. Número residencial
        numero = extrair_numero_residencial(texto_bloco, parada_num=parada_num)
        if not numero:
            numero = "S/N"

        # 6. Pacotes
        match_pac = re.search(r'(\d+)\s+(pacote|pacotes|unidade|unidades)', texto_bloco, re.IGNORECASE)
        if match_pac:
            qtd = match_pac.group(1)
            pacotes = "1 pacote" if qtd == "1" else f"{qtd} pacotes"

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
    return df.sort_values(by='Parada', key=lambda col: col.map(extrair_num))

# === Streamlit interface ===
st.title("Extração de Dados OCR - Rotas")

uploaded_files = st.file_uploader("Selecione as imagens da rota", type=["jpg", "jpeg", "png", "webp", "tiff", "bmp"], accept_multiple_files=True)

if uploaded_files:
    df_geral = []

    for uploaded_file in uploaded_files:
        st.write(f"📷 Processando: {uploaded_file.name}")
        bytes_imagem = uploaded_file.read()
        texto = ocr_space_image_file(bytes_imagem)
        linhas = [linha.strip() for linha in texto.splitlines() if linha.strip()]
        blocos = extrair_blocos(linhas)
        df = processar_blocos(blocos)
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
    st.info("Por favor, faça upload das imagens para iniciar a extração.")
