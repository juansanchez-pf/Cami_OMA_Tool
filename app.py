import streamlit as st
import pdfplumber
import pandas as pd
import re
import logging
import os
import io
import json
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from datetime import datetime
from urllib.parse import urlencode

# 1. Page Config always at the very top
st.set_page_config(page_title="V4.3 Master Pre-Audit", layout="wide")

# --- 🚨 GOOGLE OAUTH & DRIVE CONFIGURATION 🚨 ---
GDRIVE_DB_FOLDER_ID = "19FbVppIQHI37Gs3aX15DmLdRlfkhVojD"
GDRIVE_SEARCH_DRIVE_IDS = [
    "1NEkV3QHuPMKj20cOhffXyInTHZisS2hW",
    "8pvMv5B3tNqkOV6tMwiDHHl9_GYv9_9",
    "1liTp0NXeu3RwNa4eaJzzfwNPXnx_J7Vk"
]
SCOPES = ['https://www.googleapis.com/auth/drive']
REDIRECT_URI = "https://cami-oma-tool-257372633450.us-central1.run.app"

# Configure Logging
logging.getLogger("pdfminer").setLevel(logging.ERROR)


# 2. Funciones de Autenticación OAuth Web
def get_client_config():
    secret_string = os.environ.get('CLIENT_SECRET_JSON')
    if not secret_string:
        st.error("❌ No se encontró CLIENT_SECRET_JSON.")
        st.stop()
    return json.loads(secret_string)


def get_flow():
    client_config = get_client_config()
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    # Deshabilitar PKCE ANTES de cualquier operación
    flow.oauth2session.code_challenge_method = None
    return flow


def get_auth_url():
    """
    Construye la URL de autorización manualmente, garantizando
    que NO se incluya code_challenge ni code_challenge_method.
    """
    client_config = get_client_config()
    client_id = client_config['web']['client_id']

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        # ✅ Sin code_challenge ni code_challenge_method
    }
    return "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)


def oauth_login_gate():
    # PASO A: Manejar el regreso de Google con el código
    if 'code' in st.query_params:
        try:
            flow = get_flow()
            flow.fetch_token(
                code=st.query_params['code'],
                code_verifier=None  # Explícito: sin verifier
            )
            st.session_state['creds'] = flow.credentials
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"❌ Error al validar acceso: {e}")
            if st.button("🔄 Reintentar"):
                st.query_params.clear()
                st.session_state.clear()
                st.rerun()
            st.stop()

    # PASO B: Mostrar botón de Login si no hay credenciales
    if 'creds' not in st.session_state:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.write("")
            with st.container(border=True):
                st.markdown(
                    "<h3 style='text-align: center;'>🔒 OMA Tool Login</h3>",
                    unsafe_allow_html=True
                )
                try:
                    # ✅ URL construida manualmente, sin PKCE garantizado
                    auth_url = get_auth_url()
                    st.markdown(
                        f'<div style="text-align: center;">'
                        f'<a href="{auth_url}" target="_self">'
                        f'<button style="background-color:#4285F4; color:white; '
                        f'padding:10px 20px; border:none; border-radius:5px; '
                        f'cursor:pointer; width:100%; font-weight:bold;">'
                        f'Log in con Google Drive</button></a></div>',
                        unsafe_allow_html=True
                    )
                except Exception as e:
                    st.error(f"❌ Error al generar URL: {e}")
        st.stop()


# Ejecutar la puerta de seguridad
oauth_login_gate()


# --- Google Drive API Setup ---
def get_gdrive_service():
    return build('drive', 'v3', credentials=st.session_state['creds'])

def download_from_gdrive(file_id):
    service = get_gdrive_service()
    request = service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
    file_stream.seek(0)
    return file_stream

def upload_csv_to_gdrive(df, filename):
    """Sube y sobrescribe los CSV en tu única carpeta de base de datos."""
    service = get_gdrive_service()
    
    query = f"'{GDRIVE_DB_FOLDER_ID}' in parents and name='{filename}' and trashed=false"
    results = service.files().list(
        q=query, 
        spaces='drive', 
        corpora='allDrives', 
        fields="files(id, name)", 
        supportsAllDrives=True, 
        includeItemsFromAllDrives=True
    ).execute()
    
    items = results.get('files', [])
    
    csv_buffer = io.BytesIO()
    df.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)
    
    media = MediaIoBaseUpload(csv_buffer, mimetype='text/csv', resumable=False)
    
    if items:
        file_id = items[0]['id']
        service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()
    else:
        file_metadata = {'name': filename, 'parents': [GDRIVE_DB_FOLDER_ID]}
        service.files().create(body=file_metadata, media_body=media, supportsAllDrives=True).execute()

@st.cache_data(show_spinner=False, ttl=60)
def load_db_from_gdrive():
    """Descarga los 3 CSVs desde la carpeta unificada."""
    service = get_gdrive_service()
    db = {"opps": pd.DataFrame(), "subs": pd.DataFrame(), "accs": pd.DataFrame()}
    
    query = f"'{GDRIVE_DB_FOLDER_ID}' in parents and trashed=false"
    try:
        results = service.files().list(
            q=query, 
            spaces='drive', 
            corpora='allDrives', 
            fields="files(id, name)", 
            supportsAllDrives=True, 
            includeItemsFromAllDrives=True
        ).execute()
        
        items = results.get('files', [])
        
        for item in items:
            name = item['name']
            file_id = item['id']
            if name == "rep_opportunities.csv":
                db["opps"] = pd.read_csv(download_from_gdrive(file_id), dtype=str)
            elif name == "rep_subscriptions.csv":
                db["subs"] = pd.read_csv(download_from_gdrive(file_id), dtype=str)
            elif name == "rep_accounts.csv":
                db["accs"] = pd.read_csv(download_from_gdrive(file_id), dtype=str)
    except Exception as e:
        logging.error(f"Error loading DB from Drive: {e}")
        
    return db

@st.cache_data(show_spinner=False)
def search_gdrive(account_name, prior_id=""):
    service = get_gdrive_service()
    found_files = []
    
    # 1. Limpiamos el nombre para la búsqueda
    clean_words = [w for w in re.split(r'[^a-zA-Z0-9]', account_name) if w.strip() and w.lower() not in ['llc', 'inc', 'corp', 'ltd', 'co']][:2]
    name_query = " and ".join([f"name contains '{w}'" for w in clean_words])
    
    if prior_id:
        name_query = f"({name_query} or name contains '{prior_id}')"

    try:
        # 2. Iteramos sobre tus IDs de carpetas
        for folder_id in GDRIVE_SEARCH_DRIVE_IDS:
            # CAMBIO CLAVE: Usamos '{folder_id} in parents' en lugar de driveId
            # Esto busca directamente dentro de la carpeta que definiste
            final_query = f"'{folder_id}' in parents and {name_query} and mimeType = 'application/pdf' and trashed = false"
            
            results = service.files().list(
                q=final_query,
                spaces='drive',
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id, name)"
            ).execute()
            
            for file in results.get('files', []):
                found_files.append({"name": file['name'], "id": file['id']})
                
    except Exception as e:
        st.error(f"Error buscando en Drive: {e}")
        
    return found_files

def identify_and_save_files(uploaded_files):
    """Enruta y sobrescribe los CSV en la carpeta única de Google Drive."""
    for f in uploaded_files:
        try:
            try:
                f.seek(0); df = pd.read_csv(f, encoding='utf-8', dtype=str)
            except:
                f.seek(0); df = pd.read_csv(f, encoding='ISO-8859-1', dtype=str)
                
            cols = [c.lower() for c in df.columns]
            
            if 'renewed contract' in cols or 'commission date' in cols: 
                upload_csv_to_gdrive(df, "rep_opportunities.csv")
                st.success(f"✅ Éxito: {f.name} guardado como rep_opportunities.csv")
                
            elif 'sub qty' in cols or 'contract name' in cols: 
                upload_csv_to_gdrive(df, "rep_subscriptions.csv")
                st.success(f"✅ Éxito: {f.name} guardado como rep_subscriptions.csv")
                
            elif 'account id 18 characters' in cols: 
                upload_csv_to_gdrive(df, "rep_accounts.csv")
                st.success(f"✅ Éxito: {f.name} guardado como rep_accounts.csv")
            else:
                st.warning(f"⚠️ Omitido {f.name}: Formato de columnas no reconocido.")
                
        except Exception as e:
            st.error(f"❌ Error subiendo {f.name}: {str(e)}")
            
    load_db_from_gdrive.clear()

# --- Helper Functions ---
def normalize_name(name):
    return re.sub(r'[^a-z0-9]', '', str(name).lower())

def normalize_account_name(name):
    if pd.isna(name) or not isinstance(name, str): return ""
    name = re.sub(r'(?i)\b(llc|inc|corp|ltd|co|limited|ps)\b', '', name)
    return re.sub(r'[^a-z0-9]', '', name.lower())

def parse_amount(val_str):
    if not val_str or pd.isna(val_str) or str(val_str).lower() == 'nan': return 0.0
    clean_str = re.sub(r'[,\s]', '', str(val_str))
    if clean_str.count('.') > 1:
        parts = clean_str.split('.')
        clean_str = "".join(parts[:-1]) + "." + parts[-1]
    try: return float(clean_str)
    except ValueError: return 0.0

def get_msa_type(comment_str):
    c_lower = comment_str.lower()
    if "agreed to by the parties" in c_lower or "between customer and coupa" in c_lower:
        return "Signed"
    elif "www.coupa.com" in c_lower or "online" in c_lower:
        return "Online"
    return "Unknown"

def get_product_family(products_list):
    text = " ".join([p.get("name", "").lower() for p in products_list])
    if any(x in text for x in ["supply chain", "llamasoft"]): return "Supply Chain"
    if any(x in text for x in ["treasury", "bellin"]): return "Treasury"
    return "BSM"

def get_product_family_from_df(df, col_name):
    if not col_name or col_name not in df.columns: return "BSM"
    text = " ".join(df[col_name].astype(str).str.lower().tolist())
    if any(x in text for x in ["supply chain", "llamasoft"]): return "Supply Chain"
    if any(x in text for x in ["treasury", "bellin"]): return "Treasury"
    return "BSM"

# --- Extraction Functions ---
def extract_pdf_account_name(pdf_text):
    match = re.search(r'Customer Account Name:[\s\n]*([A-Za-z0-9\s,.-]+)', pdf_text, re.IGNORECASE)
    if match: return match.group(1).split('\n')[0].strip()
    return None

@st.cache_data
def extract_master_data(pdf_file):
    data = {"text": "", "fees": {}, "products": [], "yearly_schedule": {}, "msa_comment": "", "dates": {}, "billing_info": {}, "coupa_order_id": ""}
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            data["text"] += page.extract_text() + "\n"
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    if row and len(row) >= 3:
                        row_str = str(row)
                        if "USD" in row_str or "Included" in row_str:
                            raw_names_text = str(row[0])
                            raw_names_text = re.sub(r'\s*-\s*\n\s*', ' - ', raw_names_text)
                            raw_names = [n.strip() for n in raw_names_text.split('\n') if n.strip() and n.strip() != "Product Name"]
                            
                            raw_qtys = [q.strip() for q in str(row[2]).split('\n') if q.strip() and q.strip() != "Qty."]
                            
                            raw_totals = []
                            if len(row) > 3 and row[3]:
                                raw_totals = [t.strip() for t in str(row[3]).split('\n') if t.strip() and t.strip() not in ["Total Subscription Fee", "Total Fee"]]
                            
                            desc_str = str(row[1]) if len(row) > 1 else ""
                            
                            for i, name in enumerate(raw_names):
                                qty_str = raw_qtys[i] if i < len(raw_qtys) else (raw_qtys[-1] if raw_qtys else "1")
                                total_str = raw_totals[i] if i < len(raw_totals) else "0"
                                data["products"].append({
                                    "name": name, 
                                    "qty": parse_amount(re.sub(r'[^\d.]', '', qty_str)), 
                                    "desc": desc_str,
                                    "line_total": parse_amount(re.sub(r'[^\d.]', '', total_str))
                                })

    clean_text = re.sub(r'\s+', ' ', data["text"].replace('\n', ' '))
    data["account_name"] = extract_pdf_account_name(data["text"])

    order_id_match = re.search(r"Coupa Order ID(?:\(s\))?\s*:\s*([A-Za-z0-9]+)", data["text"], re.IGNORECASE)
    if order_id_match:
        data["coupa_order_id"] = order_id_match.group(1).strip()

    start_match = re.search(r"Subscription Start Date:\s*(.*?)\n", data["text"], re.IGNORECASE)
    end_match = re.search(r"Subscription End Date:\s*(.*?)\n", data["text"], re.IGNORECASE)
    data["dates"] = {
        "start": start_match.group(1).strip() if start_match else None,
        "end": end_match.group(1).strip() if end_match else None
    }

    msa_match = re.search(r"((?:The Coupa subscriptions ordered above|The subscriptions in this Order Form|This Order Form is).*?governed by.*?(?:Privacy Terms[\"']?\.?|subprocessors\.?|master-subscription-agreement/\.?))", clean_text, re.IGNORECASE)
    data["msa_comment"] = msa_match.group(1).strip() if msa_match else ""

    yearly_fees = re.findall(r"Total Year (\d+)(?:\s*Prorated)?\s*Fee:\s*USD\s*([\d,.]+)", data["text"], re.IGNORECASE)
    data["yearly_schedule"] = {int(y): parse_amount(v) for y, v in yearly_fees}
    
    address_block = re.search(r"Billing\s+Info.*?(?=Accounts Payable|Signature|$)", data["text"], re.DOTALL | re.IGNORECASE)
    has_address_data = False
    if address_block:
        block_text = address_block.group(0)
        has_address_data = bool(re.search(r"Entity Name:\s*[a-zA-Z0-9]", block_text, re.IGNORECASE) or 
                                re.search(r"Address:\s*[a-zA-Z0-9]", block_text, re.IGNORECASE))
                                
    ap_contact_match = re.search(r"Accounts Payable Contact:\s*(.*?)(?=Accounts Payable Email|Signature|$|\n)", data["text"], re.IGNORECASE)
    ap_email_match = re.search(r"Accounts Payable Email:\s*(.*?)(?=Accounts Payable Contact|Signature|$|\n)", data["text"], re.IGNORECASE)

    data["billing_info"] = {
        "ap_contact": ap_contact_match.group(1).strip() if ap_contact_match else None,
        "ap_email": ap_email_match.group(1).strip() if ap_email_match else None,
        "has_billing_content": has_address_data,
        "has_shipping_content": has_address_data
    }

    total_match = re.search(r"Total Fee:\s*USD\s*([\d,.]+)", data["text"], re.IGNORECASE)
    if total_match: data["fees"]["total"] = parse_amount(total_match.group(1))
    sub_match = re.search(r"Total Subscription Fees?[^\d]*([\d,.]+)", data["text"], re.IGNORECASE)
    if sub_match: data["fees"]["total_subscription"] = parse_amount(sub_match.group(1))

    return data

def get_best_col(cols, exact_list, partial_list):
    if cols is None or len(cols) == 0: return None
    cols_lower = [str(c).strip().lower() for c in cols]
    for ex in exact_list:
        if ex.lower() in cols_lower:
            return cols[cols_lower.index(ex.lower())]
    candidates = []
    for par in partial_list:
        for i, c in enumerate(cols_lower):
            if par.lower() in c:
                candidates.append(cols[i])
    if candidates:
        candidates.sort(key=len)
        return candidates[0]
    return None

def run_v6_storytelling_engine(pdf_name, pdf_start_date, input_opp_id="", opp_type="Renewal", of_products=[]):
    try:
        db = load_db_from_gdrive()
        df_opp = db.get("opps", pd.DataFrame())
        df_sub = db.get("subs", pd.DataFrame())
        df_acc = db.get("accs", pd.DataFrame())
        
        if df_opp.empty or df_sub.empty:
            return {"acc_id": "", "prev_opp_id": "", "renewed_contract_id": "", "subscriptions": [], "opp_history": [], "revenue_class": "Unknown", "account_type": "Unknown", "active_contracts": []}

        df_opp.columns = df_opp.columns.str.strip()
        df_sub.columns = df_sub.columns.str.strip()
        if not df_acc.empty: df_acc.columns = df_acc.columns.str.strip()

        opp_id_col_opp = get_best_col(df_opp.columns, ['Opportunity ID'], ['opportunity id'])
        renewed_col = get_best_col(df_opp.columns, ['Renewed Contract'], ['renewed contract'])
        acc_id_col_opp = get_best_col(df_opp.columns, ['Account ID 18 Characters', 'Account ID'], ['account id'])
        stage_col = get_best_col(df_opp.columns, ['Stage'], ['stage'])
        type_col = get_best_col(df_opp.columns, ['Type'], ['type'])

        contract_col_sub = get_best_col(df_sub.columns, ['Contract Number', 'Contract Name'], ['contract'])
        prod_name_col_sub = get_best_col(df_sub.columns, ['Product Name', 'Product'], ['product name', 'product'])
        qty_col_sub = get_best_col(df_sub.columns, ['Sub Qty', 'Quantity', 'Qty'], ['qty', 'quantity'])
        opp_id_col_sub = get_best_col(df_sub.columns, ['Opportunity: Opportunity ID', 'Opportunity ID'], ['opportunity id'])
        start_date_col_sub = get_best_col(df_sub.columns, ['Contract Start Date', 'Start Date'], ['start date'])
        end_date_col_sub = get_best_col(df_sub.columns, ['Contract End Date', 'End Date'], ['end date'])
        status_col_sub = get_best_col(df_sub.columns, ['Status', 'Active'], ['status', 'active'])
        acc_id_col_sub = get_best_col(df_sub.columns, ['Account Name: Account ID 18 Characters', 'Account ID'], ['account id'])
        
        current_opp = None; acc_id = ""; renewed_contract_id = ""; prev_opp_id = ""; cust_story_records = []; cust_subs = []
        rev_class = "Unknown"
        acc_type = "Unknown"
        active_contracts = []
        
        if input_opp_id:
            if opp_id_col_opp:
                match = df_opp[df_opp[opp_id_col_opp].fillna('').astype(str).str.strip() == str(input_opp_id).strip()]
                if not match.empty: 
                    current_opp = match.iloc[0]
            
            if current_opp is None:
                for col in df_opp.columns:
                    if 'id' in col.lower() and 'opp' in col.lower():
                        match = df_opp[df_opp[col].fillna('').astype(str).str.strip() == str(input_opp_id).strip()]
                        if not match.empty: 
                            current_opp = match.iloc[0]
                            break
        
        if current_opp is not None and acc_id_col_opp:
            acc_id = str(current_opp.get(acc_id_col_opp, '')).strip()
            
        if not acc_id and pdf_name and not df_acc.empty:
            acc_name_col = get_best_col(df_acc.columns, ['Account Name'], ['name'])
            acc_id_col_acc = get_best_col(df_acc.columns, ['Account ID 18 Characters', 'Account ID'], ['account id'])
            if acc_name_col and acc_id_col_acc:
                acc_match = df_acc[df_acc[acc_name_col].astype(str).str.strip().str.lower() == pdf_name.strip().lower()]
                if not acc_match.empty:
                    acc_id = str(acc_match.iloc[0][acc_id_col_acc]).strip()
        
        if acc_id:
            if not df_acc.empty:
                acc_id_col_acc = get_best_col(df_acc.columns, ['Account ID 18 Characters', 'Account ID'], ['account id'])
                if acc_id_col_acc:
                    acc_match = df_acc[df_acc[acc_id_col_acc].astype(str).str.strip() == acc_id]
                    if not acc_match.empty:
                        rev_col = get_best_col(df_acc.columns, ['Revenue Classification', 'Revenue Class'], ['revenue'])
                        if rev_col and rev_col != df_acc.columns[0]:
                            rev_class = str(acc_match.iloc[0][rev_col]).strip().upper()
                        elif len(df_acc.columns) >= 6:
                            rev_class = str(acc_match.iloc[0, 5]).strip().upper()
                            
                        acc_type_col = get_best_col(df_acc.columns, ['Account Type'], ['type'])
                        if acc_type_col:
                            acc_type = str(acc_match.iloc[0][acc_type_col]).strip()

            if acc_id_col_sub and status_col_sub and contract_col_sub:
                acc_subs = df_sub[df_sub[acc_id_col_sub].astype(str).str.strip() == acc_id]
                invalid_statuses = ['expired', 'cancelled', 'canceled', 'false', 'no', 'nan', 'none', '']
                active_mask = ~acc_subs[status_col_sub].astype(str).str.strip().str.lower().isin(invalid_statuses)
                active_contracts = acc_subs[active_mask][contract_col_sub].astype(str).str.strip().unique().tolist()
                active_contracts = [c for c in active_contracts if c.lower() not in ['nan', 'none', '']]
            
            if opp_type == "Renewal" and current_opp is not None and renewed_col:
                raw_renewed = str(current_opp.get(renewed_col, '')).replace('.0', '').strip()
                if raw_renewed and raw_renewed.lower() not in ['nan', 'none', '']: 
                    renewed_contract_id = raw_renewed.zfill(8)

            elif opp_type == "Add-On" and acc_id_col_opp:
                cust_story_temp = df_opp[df_opp[acc_id_col_opp].astype(str).str.strip() == acc_id].copy()
                of_family = get_product_family(of_products)
                best_match_contract = None
                
                invalid_stages = ['closed lost', 'queued for loss', 'cancelled', 'duplicate', 'no decision']
                if stage_col:
                    valid_opps = cust_story_temp[~cust_story_temp[stage_col].astype(str).str.lower().isin(invalid_stages)].copy()
                else:
                    valid_opps = cust_story_temp.copy()
                
                if type_col:
                    renewals = valid_opps[valid_opps[type_col].astype(str).str.contains('Renewal', case=False, na=False)]
                else:
                    renewals = pd.DataFrame()
                
                if not renewals.empty and renewed_col and contract_col_sub:
                    date_cols_to_check = ['Created Date', 'Close Date', 'Commission Date']
                    sort_col = get_best_col(renewals.columns, date_cols_to_check, ['date'])
                    if sort_col:
                         renewals[sort_col] = pd.to_datetime(renewals[sort_col], errors='coerce')
                         renewals = renewals.sort_values(by=sort_col, ascending=False)

                    for _, ren in renewals.iterrows():
                        candidate_contract = str(ren.get(renewed_col, '')).replace('.0', '').strip().zfill(8)
                        if candidate_contract and candidate_contract.lower() not in ['nan', 'none', '00000nan', '']:
                            temp_subs = df_sub[df_sub[contract_col_sub].astype(str).fillna('').str.replace('.0', '', regex=False).str.strip().str.zfill(8) == candidate_contract]
                            if not temp_subs.empty:
                                if get_product_family_from_df(temp_subs, prod_name_col_sub) == of_family:
                                    best_match_contract = candidate_contract
                                    break
                    
                    if not best_match_contract:
                        latest_ren = renewals.iloc[0]
                        best_match_contract = str(latest_ren.get(renewed_col, '')).replace('.0', '').strip()

                if (not best_match_contract or best_match_contract.lower() in ['nan', 'none', '00000nan', '']) and opp_id_col_opp and opp_id_col_sub and contract_col_sub:
                    account_opp_ids = valid_opps[opp_id_col_opp].dropna().astype(str).str.strip().tolist()
                    if account_opp_ids:
                        acc_subs = df_sub[df_sub[opp_id_col_sub].astype(str).str.strip().isin(account_opp_ids)].copy()
                        if not acc_subs.empty:
                            contracts = acc_subs[contract_col_sub].fillna('').astype(str).str.replace('.0', '', regex=False).str.strip().unique()
                            contracts = [c for c in contracts if c.lower() not in ['nan', 'none', '']]
                            for c_num in contracts:
                                if c_num:
                                    temp_subs = acc_subs[acc_subs[contract_col_sub].astype(str).str.contains(c_num, na=False)]
                                    if get_product_family_from_df(temp_subs, prod_name_col_sub) == of_family:
                                        best_match_contract = c_num.zfill(8)
                                        break
                            
                            if not best_match_contract and len(contracts) > 0:
                                best_match_contract = contracts[0].zfill(8)

                if best_match_contract and best_match_contract.lower() not in ['nan', 'none', '']:
                    renewed_contract_id = best_match_contract.zfill(8)
            
            if acc_id_col_opp:
                cust_story_records = df_opp[df_opp[acc_id_col_opp].astype(str).str.strip() == acc_id].to_dict('records')
        
        if renewed_contract_id and contract_col_sub:
            df_sub['Clean_Contract'] = df_sub[contract_col_sub].astype(str).fillna('').str.replace('.0', '', regex=False).str.strip().str.zfill(8)
            subs_match = df_sub[df_sub['Clean_Contract'] == renewed_contract_id]
            
            if subs_match.empty and acc_id and opp_type == "Renewal" and acc_id_col_opp and opp_id_col_sub:
                account_opps = df_opp[df_opp[acc_id_col_opp].astype(str).str.strip() == acc_id]
                if stage_col:
                    valid_account_opps = account_opps[~account_opps[stage_col].astype(str).str.lower().isin(['closed lost', 'queued for loss', 'cancelled'])]
                else:
                    valid_account_opps = account_opps
                acc_opp_ids = valid_account_opps[opp_id_col_opp].dropna().astype(str).str.strip().tolist()

                if acc_opp_ids:
                    acc_subs = df_sub[df_sub[opp_id_col_sub].astype(str).str.strip().isin(acc_opp_ids)].copy()
                    if not acc_subs.empty:
                        contracts = acc_subs[contract_col_sub].astype(str).fillna('').str.replace('.0', '', regex=False).str.strip().unique()
                        contracts = [c for c in contracts if c.lower() not in ['nan', 'none', '']]
                        if contracts:
                            fallback_contract = contracts[0].zfill(8)
                            renewed_contract_id = fallback_contract
                            subs_match = acc_subs[acc_subs['Clean_Contract'] == fallback_contract]
            
            if not subs_match.empty:
                prev_opp_id = str(subs_match.iloc[0].get(opp_id_col_sub, '')).replace('.0', '').strip()
                for _, row in subs_match.iterrows():
                    cust_subs.append({
                        "Product Name": row.get(prod_name_col_sub, 'Unknown') if prod_name_col_sub else 'Unknown', 
                        "Quantity": parse_amount(row.get(qty_col_sub, '0')) if qty_col_sub else 0.0, 
                        "Contract Start Date": row.get(start_date_col_sub, '') if start_date_col_sub else '', 
                        "Contract End Date": row.get(end_date_col_sub, '') if end_date_col_sub else ''
                    })
                    
        return {
            "acc_id": acc_id, 
            "prev_opp_id": prev_opp_id, 
            "renewed_contract_id": renewed_contract_id, 
            "subscriptions": cust_subs, 
            "opp_history": cust_story_records, 
            "revenue_class": rev_class,
            "account_type": acc_type,
            "active_contracts": active_contracts
        }
    except Exception as e: return {"error": f"Database Error: {str(e)}"}

# --- Streamlit UI ---

st.title("🛡️ FinOps V4.3: The Storytelling Pre-Audit")

if 'run_audit' not in st.session_state: st.session_state.run_audit = False
if 'curr_data' not in st.session_state: st.session_state.curr_data = None
if 'prior_data' not in st.session_state: st.session_state.prior_data = None
if 'story' not in st.session_state: st.session_state.story = None
if 'manual_opp_id' not in st.session_state: st.session_state.manual_opp_id = ""

with st.sidebar:
    st.header("🗄️ Cloud Database Manager")
    
    if os.environ.get('CLIENT_SECRET_JSON'):
        st.success("✅ Securely Connected to Google Drive")
    else:
        st.error("❌ ERROR: El secreto CLIENT_SECRET_JSON no está llegando.")
        
    with st.expander("📂 Update Database"):
        files = st.file_uploader("Upload CSVs (Bulk)", type="csv", accept_multiple_files=True)
        if files and st.button("Sync to Google Drive"):
            with st.spinner("Uploading and updating database in Cloud..."):
                identify_and_save_files(files)
    st.divider()
    opp_type = st.selectbox("Current Opportunity Type:", ["Renewal", "Add-On", "New Business"])

col1, col2 = st.columns(2)
with col1:
    st.header("Step 1: Current Order Form")
    if 'uploader_key' not in st.session_state: st.session_state.uploader_key = 0
    curr_f = st.file_uploader("Upload Current OF PDF", type="pdf", key=f"of_uploader_{st.session_state.uploader_key}")
    if curr_f:
        st.session_state.curr_data = extract_master_data(curr_f)
        c_data = st.session_state.curr_data
        st.success(f"Detected Account: {c_data['account_name']}")
        
        display_id = st.session_state.manual_opp_id if st.session_state.manual_opp_id else c_data.get("coupa_order_id", "")
        user_opp_id = st.text_input("Current Opportunity ID:", value=display_id)
        st.session_state.manual_opp_id = user_opp_id
        
        if st.button("🔍 Map Customer Journey"):
            with st.spinner("Querying Cloud Database..."):
                st.session_state.story = run_v6_storytelling_engine(c_data['account_name'], c_data['dates']['start'], user_opp_id, opp_type, c_data.get('products', []))
        
        if st.session_state.story and st.session_state.story.get('opp_history'):
            st.divider()
            st.subheader("📜 Customer Opportunity Story")
            df_hist = pd.DataFrame(st.session_state.story['opp_history']).loc[:, ~pd.DataFrame(st.session_state.story['opp_history']).columns.duplicated()]
            if 'Stage' in df_hist.columns:
                df_hist = df_hist[~df_hist['Stage'].astype(str).str.lower().isin(['closed lost', 'queued for loss'])]
            if 'Commission Date' in df_hist.columns:
                df_hist['Commission Date'] = pd.to_datetime(df_hist['Commission Date'], errors='coerce')
                df_hist = df_hist.sort_values(by='Commission Date', ascending=True)
                df_hist['Commission Date'] = df_hist['Commission Date'].dt.strftime('%Y-%m-%d')
            target_cols = ['Opportunity Name', 'Type', 'Opportunity ID', 'Commission Date', 'Stage', 'Amount (ACV)', 'Renewed Contract']
            st.dataframe(df_hist[[c for c in target_cols if c in df_hist.columns]], use_container_width=True, hide_index=True)

if st.session_state.story:
    story = st.session_state.story; c_data = st.session_state.curr_data
    with col2:
        st.header("Step 2: Analysis Baseline")
        st.info(f"Account ID: {story.get('acc_id', 'Unknown')} | Revenue Class: {story.get('revenue_class', 'Unknown')}")
        if opp_type == "Renewal":
            st.subheader("🔁 Renewal Continuity")
            st.write(f"Renewed Contract: {story.get('renewed_contract_id', 'Unknown')}")
            st.write(f"Previous Deal ID: {story.get('prev_opp_id', 'Unknown')}")
            if story.get('prev_opp_id'):
                with st.spinner("Searching Global Drive..."):
                    found = search_gdrive(c_data['account_name'], story.get('prev_opp_id', ''))
                if found:
                    sel = st.selectbox("Select Prior OF from Drive:", found, format_func=lambda x: x['name'])
                    if sel: 
                        with st.spinner("Downloading prior OF from Google Drive..."):
                            file_stream = download_from_gdrive(sel['id'])
                            st.session_state.prior_data = extract_master_data(file_stream)
                else: st.warning("No matching prior OF found in Drive.")
        elif opp_type == "Add-On":
            st.subheader("➕ Add-On Continuity")
            st.write(f"Target Base Contract: {story.get('renewed_contract_id', 'Unknown')}")
            st.write(f"Previous Deal ID: {story.get('prev_opp_id', 'Unknown')}")
        elif opp_type == "New Business":
            st.subheader("🏢 Account Validation (New Business)")
            acc_type = story.get('account_type', 'Unknown')
            if acc_type.lower() in ['prospect', 'cancelled customer']:
                st.success(f"✅ Account Type Valid: {acc_type}")
            else:
                st.error(f"❌ Account Type Invalid: Found {acc_type}. Expected 'Prospect' or 'Cancelled Customer'.")
                
            has_prior_nb = any(o.get('Type', '').lower() == 'new business' and o.get('Stage', '').lower() == 'closed won' for o in story.get('opp_history', []))
            if has_prior_nb:
                st.warning("⚠️ Prior New Business found. Validating contract statuses...")
                active_contracts = story.get('active_contracts', [])
                if active_contracts:
                    st.error(f"❌ Active Contracts Found! Account has prior Closed Won NB opps and currently active contracts: {', '.join(active_contracts)}. Please verify if this should be a Renewal or Add-On.")
                else:
                    st.success("✅ Prior New Business found, but all associated contracts are expired or cancelled.")

st.divider()
col_exec, col_clear = st.columns(2)
with col_exec:
    if st.button("🚀 Execute Global Pre-Audit", use_container_width=True, type="primary"): st.session_state.run_audit = True

with col_clear:
    if st.button("🧹 Clear Board (Delete Current OF & ID)", use_container_width=True):
        for key in ['run_audit', 'curr_data', 'prior_data', 'story', 'manual_opp_id']:
            if key in st.session_state: del st.session_state[key]
        st.session_state.uploader_key += 1
        st.rerun()

if st.session_state.run_audit and st.session_state.curr_data and st.session_state.story:
    c_data = st.session_state.curr_data; story = st.session_state.story
    st.header("🚨 Pre-Audit Findings")
    # 1. MSA Governance
    st.subheader("⚖️ MSA Governance")
    curr_msa_type = get_msa_type(c_data["msa_comment"])
    st.write(f"Current MSA Type: {curr_msa_type}")
    if opp_type == "New Business":
        if curr_msa_type == "Signed":
            st.warning("⚠️ Pre-Auditor Alert: The Signed MSA is vital to the OF signature. If this has not been processed, ensure you request it or send it for signature immediately.")
        elif curr_msa_type == "Online":
            st.success("✅ Online MSA detected (No additional documents required).")
            
    elif opp_type == "Renewal" and st.session_state.prior_data:
        p_data = st.session_state.prior_data
        
        c_comment = c_data["msa_comment"].strip()
        p_comment = p_data["msa_comment"].strip()
        
        msa_link_str = "www.coupa.com/master-subscription-agreement"
        c_has_link = msa_link_str in c_comment.lower()
        p_has_link = msa_link_str in p_comment.lower()
        
        is_match = False
        if c_has_link and p_has_link:
            st.success("✅ MSA Continuity: Both documents use the Online MSA (link verified).")
            is_match = True
        elif c_has_link != p_has_link:
            st.error("❌ MSA Agreement Change Detected: One document uses the Online MSA link while the other does not. Verify if the customer was moved to a new agreement.")
        elif c_comment != p_comment:
            st.error("❌ MSA Comment Mismatch: The signed MSA comments do not match. Verify if the customer was moved to a new agreement.")
        else:
            st.success("✅ MSA Continuity: Comment matches the prior transaction.")
            is_match = True

        col_m1, col_m2 = st.columns(2)
        col_m1.info(f"Prior Comment OF:\n\n{p_comment}")
        if is_match:
            col_m2.success(f"Current Comment OF:\n\n{c_comment}")
        else:
            col_m2.warning(f"Current Comment OF:\n\n{c_comment}")

    # 2. Add-On Date Validations
    if opp_type == "Add-On" and story.get('subscriptions'):
        st.subheader("⏱️ Add-On Date Validations")
        c_start = story['subscriptions'][0].get("Contract Start Date")
        c_end = story['subscriptions'][0].get("Contract End Date")
        of_start = c_data['dates']['start']
        of_end = c_data['dates']['end']
        
        col_t1, col_t2 = st.columns(2)
        with col_t1:
            try:
                if pd.to_datetime(of_start) < pd.to_datetime(c_start): 
                    st.error(f"❌ Invalid Start: OF Start Date ({of_start}) is BEFORE the Contract Start Date ({c_start})")
                else: 
                    st.success(f"✅ Valid Start Date ({of_start})")
            except Exception: pass
        with col_t2:
            try:
                if pd.to_datetime(of_end) != pd.to_datetime(c_end): 
                    st.error(f"❌ Coterminus Error: OF End Date ({of_end}) does not match Contract End Date ({c_end})")
                else: 
                    st.success(f"✅ Coterminus validated ({of_end})")
            except Exception: pass

    # 3. Fees & Yearly Schedule Check
    st.subheader("💰 Fee Structure & Yearly Schedule Check")
    total_fee = c_data["fees"].get("total", 0.0)
    sum_lines = sum([p.get('line_total', 0.0) for p in c_data['products']])
        
    if c_data.get("yearly_schedule"):
        schedule_data = []
        years = sorted(c_data["yearly_schedule"].keys())
        yearly_sum = 0.0
        
        annual_acv = sum_lines
        expected_prorated = sum_lines
        if opp_type == "Add-On":
            try:
                start_dt = pd.to_datetime(c_data['dates']['start'])
                end_dt = pd.to_datetime(c_data['dates']['end'])
                
                target_month = end_dt.month
                target_day = end_dt.day
                
                try:
                    year1_end = pd.Timestamp(year=start_dt.year, month=target_month, day=target_day)
                except ValueError: 
                    year1_end = pd.Timestamp(year=start_dt.year, month=target_month, day=28)
                    
                if year1_end < start_dt:
                    try:
                        year1_end = pd.Timestamp(year=start_dt.year + 1, month=target_month, day=target_day)
                    except ValueError:
                        year1_end = pd.Timestamp(year=start_dt.year + 1, month=target_month, day=28)
                
                if year1_end > end_dt:
                    year1_end = end_dt

                months_diff = (year1_end.year - start_dt.year) * 12 + (year1_end.month - start_dt.month)
                if year1_end.day < start_dt.day - 1:
                    months_diff -= 1
                    
                advanced_dt = start_dt + pd.DateOffset(months=months_diff)
                remaining_days = (year1_end - advanced_dt).days + 1
                if remaining_days < 0: remaining_days = 0
                
                term_multiplier = (months_diff / 12.0) + (remaining_days / 365.0)
                annual_acv = c_data["yearly_schedule"].get(2, sum_lines)
                expected_prorated = annual_acv * term_multiplier
            except Exception:
                pass
        
        for i, yr in enumerate(years):
            current_fee = c_data["yearly_schedule"][yr]
            yearly_sum += current_fee
            
            if opp_type == "Add-On":
                if i == 0:
                    if abs(current_fee - expected_prorated) <= (annual_acv * 0.02):
                        check_str = "✅ Valid Prorated Fee"
                    else:
                        check_str = f"❌ Expected ~${expected_prorated:,.2f}"
                else:
                    check_str = "Annual ACV"
                schedule_data.append({"Year": f"Year {yr}", "Annual Fee": f"${current_fee:,.2f}", "Proration Check": check_str})
                
            else:
                uplift_str = "Base"
                yoy_inc_str = "-"
                if i > 0:
                    prev_fee = c_data["yearly_schedule"][years[i-1]]
                    if prev_fee > 0:
                        diff = current_fee - prev_fee
                        yoy_inc_str = f"${diff:,.2f}"
                        uplift_val = (diff / prev_fee) * 100
                        if uplift_val > 0.1: uplift_str = f"📈 {uplift_val:.1f}% Uplift"
                        elif uplift_val < -0.1: uplift_str = f"📉 {uplift_val:.1f}% Reduction"
                        else: uplift_str = "Flat"
                schedule_data.append({"Year": f"Year {yr}", "Annual Fee": f"${current_fee:,.2f}", "YoY Increase": yoy_inc_str, "YoY Uplift": uplift_str})
        
        st.table(pd.DataFrame(schedule_data))
        
        col_t1, col_t2 = st.columns(2)
        col_t1.metric("Stated OF Total Fee", f"${total_fee:,.2f}")
        col_t2.metric("Calculated Sum of Line Items", f"${sum_lines:,.2f}")
        
        if total_fee > 0:
            if abs(yearly_sum - total_fee) > 1.0: 
                st.error(f"❌ Fee Mismatch: The summation of the Yearly Schedule (${yearly_sum:,.2f}) does not match the Stated OF Total Fee (${total_fee:,.2f}).")
            else:
                st.success(f"✅ Fee Match: The summation of the Yearly Schedule matches the Stated OF Total Fee (${total_fee:,.2f}).")

    # 4. Product Comparison
    st.subheader(f"📦 Subscription Comparison ({opp_type})")
    net_new_product_added = False
    sfdc_prods = {}
    for s in story.get('subscriptions', []):
        nk = normalize_name(s['Product Name'])
        if nk not in sfdc_prods: 
            sfdc_prods[nk] = {
                "name": s['Product Name'], 
                "qty": 0.0,
                "start_date": s.get('Contract Start Date', ''),
                "end_date": s.get('Contract End Date', '')
            }
        sfdc_prods[nk]["qty"] += s['Quantity']
        
    of_prods = {normalize_name(p['name']): p for p in c_data['products']}
    comp = []
    term_years = max(1, round((pd.to_datetime(c_data['dates']['end']) - pd.to_datetime(c_data['dates']['start'])).days / 365.25)) if c_data['dates']['start'] else 1

    if opp_type == "Add-On":
        display_keys = sorted(of_prods.keys())
    else:
        display_keys = sorted(set(sfdc_prods.keys()) | set(of_prods.keys()))

    for k in display_keys:
        sqty = sfdc_prods.get(k, {}).get('qty', 0)
        oqty = of_prods.get(k, {}).get('qty', 0)
        if sqty == 0 and oqty > 0:
            if "support" not in k and "platform" not in k and "learningpass" not in k:
                net_new_product_added = True

    rev_class = story.get('revenue_class', 'UNKNOWN')
    expected_lp_qty = 0
    has_lp_in_of = any("learningpass" in k for k in of_prods.keys())
    if has_lp_in_of:
        if (opp_type == "Add-On" and net_new_product_added) or opp_type == "New Business" or opp_type == "Renewal":
            if "ENT" in rev_class:
                expected_lp_qty = 4
            elif "CORP" in rev_class or "MM" in rev_class:
                expected_lp_qty = 2

    sfdc_supp = [v['name'] for k, v in sfdc_prods.items() if 'support' in k]
    of_supp = [v['name'] for k, v in of_prods.items() if 'support' in k]
    sfdc_plat = [v['name'] for k, v in sfdc_prods.items() if 'platform' in k]
    of_plat = [v['name'] for k, v in of_prods.items() if 'platform' in k]

    sfdc_supp_name = sfdc_supp[0] if sfdc_supp else "None"
    of_supp_name = of_supp[0] if of_supp else "None"
    sfdc_plat_name = sfdc_plat[0] if sfdc_plat else "None"
    of_plat_name = of_plat[0] if of_plat else "None"

    for k in display_keys:
        s, o = sfdc_prods.get(k), of_prods.get(k)
        name = o['name'] if o else s['name']
        sqty, oqty = (s['qty'] if s else 0), (o['qty'] if o else 0)
        start_dt = s['start_date'] if s else ''
        end_dt = s['end_date'] if s else ''
        
        is_total_term_prod = any(x in k for x in ["nextgenai", "paypayment"])
        exp = sqty * term_years if (is_total_term_prod and opp_type == "Renewal") else sqty
        
        status = "🟢 Maintained"
        
        if opp_type == "New Business":
            if "learningpass" in k and has_lp_in_of:
                if int(oqty) != expected_lp_qty:
                    status = f"❌ Error (Expected {expected_lp_qty} for {rev_class})"
                else:
                    status = f"✅ Valid Entitlement ({expected_lp_qty})"
            else:
                status = "-"
                
            comp.append({
                "Product": name, 
                "OF Qty": int(oqty), 
                "Status": status
            })

        elif opp_type == "Add-On":
            if "learningpass" in k and oqty > 0:
                if not net_new_product_added:
                    status = f"❌ Invalid (No New Products Added)"
                elif int(oqty) != expected_lp_qty:
                    status = f"❌ Error (Expected {expected_lp_qty} for {rev_class})"
                else:
                    status = f"✅ Valid Entitlement ({expected_lp_qty})"
            elif sqty == 0:
                status = "🔵 Brand New Product (Added Module)"
            elif oqty > 0:
                status = f"📈 Qty Increase (Base: {int(sqty)})"
            else:
                status = "🟢 Maintained"

            if "support" in k and of_supp:
                status = "❌ Tier Changed" if normalize_name(sfdc_supp_name) != normalize_name(of_supp_name) else "🟢 Tier Maintained"
            elif "platform" in k and of_plat:
                status = "❌ Tier Changed" if normalize_name(sfdc_plat_name) != normalize_name(of_plat_name) else "🟢 Tier Maintained"
                
            comp.append({
                "Product": name, 
                "SFDC Base": int(sqty), 
                "OF Qty": int(oqty), 
                "Contract Start Date": start_dt,
                "Contract End Date": end_dt,
                "Status": status
            })

        else:
            if "learningpass" in k and has_lp_in_of:
                if int(oqty) != expected_lp_qty:
                    status = f"❌ Error (Expected {expected_lp_qty} for {rev_class})"
                else:
                    status = f"✅ Valid Entitlement ({expected_lp_qty})"
            elif sqty == 0: 
                status = "🔵 Brand New Product (Added)"
            elif oqty == 0: 
                status = "🔴 Dropped"
            elif is_total_term_prod and opp_type == "Renewal":
                status = "🟢 Maintained" if int(oqty) == int(exp) else f"❌ Error (Expected {int(exp)})"
            elif int(oqty) > int(exp): 
                status = f"🟠 Upsell (+{int(oqty-exp)})"
            elif int(oqty) < int(exp): 
                status = f"🟡 Downsell ({int(oqty-exp)})"

            if "support" in k:
                status = "❌ Tier Changed" if normalize_name(sfdc_supp_name) != normalize_name(of_supp_name) else "🟢 Tier Maintained"
            elif "platform" in k:
                status = "❌ Tier Changed" if normalize_name(sfdc_plat_name) != normalize_name(of_plat_name) else "🟢 Tier Maintained"

            comp.append({
                "Product": name, 
                "SFDC Base": int(sqty), 
                "OF Qty": int(oqty), 
                "Contract Start Date": start_dt,
                "Contract End Date": end_dt,
                "Status": status
            })

    st.dataframe(pd.DataFrame(comp), hide_index=True, use_container_width=True)
            
    if has_lp_in_of:
        if opp_type == "Add-On" and net_new_product_added:
            st.info(f"💡 Learning Pass Entitlement Rule: Customer added Net New Products. Based on Revenue Class {rev_class}, they are entitled to {expected_lp_qty} free Coupa Learning Pass - Standard 12 seats.")
        elif opp_type == "Add-On" and not net_new_product_added:
            st.info(f"💡 Learning Pass Entitlement Rule: Customer ONLY expanded existing users. They are NOT entitled to free Learning Pass seats.")
        elif opp_type == "New Business" or opp_type == "Renewal":
            st.info(f"💡 Learning Pass Entitlement Rule: Based on Revenue Class {rev_class}, they are entitled to {expected_lp_qty} free Coupa Learning Pass - Standard 12 seats.")

    # 5. Billing, Shipping & Accounts Payable Audit
    st.subheader("🧾 Billing, Shipping & Accounts Payable Audit")
    b = c_data["billing_info"]
    col_b1, col_b2 = st.columns(2)
    with col_b1:
        if b['ap_contact']: st.success(f"✅ Accounts Payable Contact: {b['ap_contact']}")
        else: st.error("❌ Accounts Payable Contact: MISSING from document")
        if b['ap_email']: st.success(f"✅ Accounts Payable Email: {b['ap_email']}")
        else: st.error("❌ Accounts Payable Email: MISSING from document")
    with col_b2:
        if b['has_billing_content']: st.success("✅ Billing Information: Address details detected")
        else: st.error("❌ Billing Information: Block is EMPTY or MISSING")
        if b['has_shipping_content']: st.success("✅ Shipping Information: Address details detected")
        else: st.error("❌ Shipping Information: Block is EMPTY or MISSING")
