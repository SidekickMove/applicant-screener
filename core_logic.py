import os
import re
import pandas as pd
import PyPDF2
import docx2txt
from langdetect import detect
import spacy

import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

nlp = spacy.load("en_core_web_md")


def load_local_fortune500_csv():
    """
    Loads 'fortune500.csv' from the same directory as this file.
    Returns a list of lines (Fortune 500 company names).
    """
    file_path = os.path.join(os.path.dirname(__file__), "fortune500.csv")
    unallowed = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    unallowed.append(line)
    except Exception as e:
        print(f"Error loading 'fortune500.csv': {e}")
    return unallowed


def read_csv_with_fallback(csv_file):
    """
    Attempts to read the CSV file as UTF-8.
    If that fails with a UnicodeDecodeError, fallback to 'latin1'.
    """
    print(f"\n[DEBUG] Attempting to read CSV: {csv_file}")
    try:
        df = pd.read_csv(csv_file, encoding="utf-8")
        print("[DEBUG] Successfully read as UTF-8.")
    except UnicodeDecodeError:
        print("[DEBUG] Retrying with 'latin1' encoding...")
        df = pd.read_csv(csv_file, encoding="latin1")

    print("[DEBUG] Columns before ANY normalization:", list(df.columns))
    return df


def normalize_dataframe(df):
    """
    Unifies DataFrame columns from various CSV formats into a standard form:
      1) Ensures 'id' column if missing.
      2) Renames known columns (Name->name, Email->email, etc).
      3) If a column has "resume" in its name => rename -> 'download'.
      4) Unify any column that is literally 'answers' (case-insensitive) -> 'answers' if not already.
      5) If the CSV has 'Question N' / 'Answer N' columns, merges them into 'answers'.
         (But do NOT drop a literal 'answers' column that the user already has.)
      6) Drops those question/answer columns.
    """

    # Strip each column name to avoid trailing spaces, unify them
    print("[DEBUG] Stripping + normalizing column names in df...")
    old_cols = df.columns.tolist()
    new_cols = [c.strip() for c in old_cols]
    df.columns = new_cols

    # 1) Ensure there's an 'id' column
    if "id" not in df.columns:
        df["id"] = ""

    # 2) Rename map
    rename_map = {
        "Name": "name",
        "Email": "email",
        "Creation time": "created_at",
        "Job title": "job",
        "Experiences": "experience",
    }
    to_rename = {}
    for col in df.columns:
        for old_key, new_key in rename_map.items():
            if col.lower() == old_key.lower():
                to_rename[col] = new_key
    if to_rename:
        print("[DEBUG] Will rename columns based on rename_map:", to_rename)
        df.rename(columns=to_rename, inplace=True, errors="ignore")

    # 3) If there's a column with "resume" in its name => rename -> 'download'
    for col in list(df.columns):
        if "resume" in col.lower() and col.lower() != "download":
            print(f"[DEBUG] Renaming '{col}' -> 'download' (because it has 'resume').")
            df.rename(columns={col: "download"}, inplace=True, errors="ignore")

    # 4) If there's a column EXACTLY 'answers' ignoring case, unify it
    for col in list(df.columns):
        if col.lower() == "answers" and col != "answers":
            print(f"[DEBUG] Renaming '{col}' -> 'answers' (case variation).")
            df.rename(columns={col: "answers"}, inplace=True, errors="ignore")

    print("[DEBUG] Columns after basic rename steps:", list(df.columns))

    # 5) Identify dynamic question/answer columns
    question_cols = []
    answer_cols = []
    for c in df.columns:
        lc = c.lower()
        # Only treat as question/answer if it starts with question/answer + a space or number
        # We also skip if the entire column is exactly 'answers'
        if lc.startswith("question") and lc != "answers":
            question_cols.append(c)
        elif lc.startswith("answer") and lc != "answers":
            answer_cols.append(c)

    def extract_number(cname):
        match = re.search(r"(\d+)", cname)
        return int(match.group(1)) if match else 999

    question_cols.sort(key=extract_number)
    answer_cols.sort(key=extract_number)

    # Make sure we have a literal 'answers' column to store any merged Q&A
    if "answers" not in df.columns:
        df["answers"] = ""

    # Merge them into 'answers'
    for idx, row_data in df.iterrows():
        lines = []
        max_len = max(len(question_cols), len(answer_cols))
        for i in range(max_len):
            q_col = question_cols[i] if i < len(question_cols) else None
            a_col = answer_cols[i] if i < len(answer_cols) else None

            q_text = str(row_data[q_col]).strip() if q_col else ""
            a_text = str(row_data[a_col]).strip() if a_col else ""

            if q_text or a_text:
                lines.append(f"---------- {q_col or 'Question'}: {q_text}")
                lines.append(f"---------- {a_col or 'Answer'}: {a_text}")

        combined_qa = "\n".join(lines).strip()
        if combined_qa:
            existing_answers = df.at[idx, "answers"]
            if existing_answers:
                new_val = existing_answers + "\n" + combined_qa
            else:
                new_val = combined_qa
            df.at[idx, "answers"] = new_val

    # Now drop those dynamic question/answer columns
    cols_to_drop = question_cols + answer_cols
    if cols_to_drop:
        print("[DEBUG] Dropping question/answer columns:", cols_to_drop)
        df.drop(columns=cols_to_drop, inplace=True, errors="ignore")

    print("[DEBUG] Final columns after normalize_dataframe:", list(df.columns))
    return df


def parse_experiences_lines(experience_text):
    """
    Returns up to two company names from the user's experience input,
    no matter if it's "IBM; Apple" or multiline "The Instant Group : Manager"
    or a mixture.
    """
    experience_text = experience_text.strip()
    found_companies = []

    # If they typed something like "IBM; Apple", just split on semicolons:
    if ";" in experience_text:
        parts = [p.strip() for p in experience_text.split(";") if p.strip()]
        for p in parts:
            found_companies.append(p)
            if len(found_companies) == 2:
                break
        return found_companies
    
    # Otherwise, assume lines with possible "Company : Title"
    # (and maybe leading/trailing date info).
    # We'll look for lines that contain a colon, then treat the text before
    # the colon as the "company" if it’s not too short.
    lines = experience_text.splitlines()
    for line in lines:
        line = line.strip()
        if ":" in line:
            # e.g. "The Instant Group : Accounts Manager"
            lhs, rhs = line.split(":", maxsplit=1)
            lhs = lhs.strip()
            if lhs and len(lhs) > 2:  # Avoid picking empty or nonsense
                found_companies.append(lhs)
                if len(found_companies) == 2:
                    break
    
    return found_companies


def filter_ignored_questions(answers_str):
    """
    Returns a smaller answers_str with only the Q&As from non-ignored questions.
    The rest is omitted entirely.
    """
    blocks = answers_str.split("----------")
    filtered_lines = []
    pending_question_text = None

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        parts = block.split(":", maxsplit=1)
        if len(parts) < 2:
            continue

        label_text = parts[0].strip()
        content_text = parts[1].strip()

        # If the label is "Question #", store the question so the next "Answer #" line can match it
        if label_text.lower().startswith("question "):
            pending_question_text = content_text
        elif label_text.lower().startswith("answer "):
            # We only keep this Q&A if the question was NOT ignored
            if pending_question_text and not is_ignored_question(pending_question_text):
                filtered_lines.append(f"Question: {pending_question_text}")
                filtered_lines.append(f"Answer: {content_text}")
            pending_question_text = None
        else:
            # Some CSVs have the question text in 'label_text' directly,
            # and the answer in 'content_text'.
            # We only keep it if the question isn't ignored.
            if not is_ignored_question(label_text):
                filtered_lines.append(f"Question: {label_text}")
                filtered_lines.append(f"Answer: {content_text}")

    # Return the filtered Q&A as a single string
    return "\n".join(filtered_lines)


def process_applicants(
    csv_file,
    pdf_folder,
    check_dollar,
    check_percent,
    required_text,
    optional_text,
    related_text,
    exclude_answers=False
):
    df = read_csv_with_fallback(csv_file)
    df = normalize_dataframe(df)

    unallowed_phrases = load_local_fortune500_csv()

    required_list = [kw.strip() for kw in required_text.split("\n") if kw.strip()]
    optional_list = [kw.strip() for kw in optional_text.split("\n") if kw.strip()]
    related_list = [kw.strip() for kw in related_text.split("\n") if kw.strip()]

    pdf_exists_count = 0
    english_count = 0
    short_answers_okay_count = 0
    no_unallowed_count = 0
    keywords_count = 0
    final_pass_count = 0

    results = []

    for idx, row in df.iterrows():
        if "download" not in df.columns:
            print("No 'download' column found after normalization.")
            break

        filename = str(row["download"]).strip()
        file_path = os.path.join(pdf_folder, filename)
        if not os.path.isfile(file_path):
            continue
        pdf_exists_count += 1

        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            file_text = extract_pdf_text(file_path)
        elif ext == ".docx":
            file_text = extract_docx_text(file_path)
        else:
            print(f"Skipping row {idx}: unsupported file type -> {file_path}")
            continue

        if exclude_answers:
            answers_str = ""
        else:
            answers_str = str(row.get("answers", ""))
            answers_str = filter_ignored_questions(answers_str)

        combined_text = file_text + " " + answers_str if answers_str else file_text

        if is_english_text(combined_text):
            english_count += 1
        else:
            continue

        if not exclude_answers:
            if not has_two_or_more_short_answers(answers_str, min_words=20):
                short_answers_okay_count += 1
            else:
                continue
        else:
            short_answers_okay_count += 1

        # Unified experience parsing & F500 check
        experience_str = str(row.get("experience", "")).strip()
        companies_found = parse_experiences_lines(experience_str)

        # If parse_experiences_lines found 1–2 companies, we check those directly
        if companies_found:
            # If any of those parsed companies is in the Fortune 500 unallowed list => fail
            if any(comp in unallowed_phrases for comp in companies_found):
                continue  # or "row_dict['status'] = 'failed'; row_dict['fail_reason'] = ...; all_rows_data.append(row_dict); continue" if you’re using pass/fail
        else:
            # If we did NOT parse any company (the user typed something that doesn't match the semicolon/colon patterns),
            # we fallback to scanning the PDF text for >=2 unallowed matches
            count_f500, matched_f500 = count_unallowed_matches(file_text, unallowed_phrases)
            if count_f500 >= 2:
                continue  # same pass/fail logic as above

        no_unallowed_count += 1

        symbol_base_text = file_text if exclude_answers else (file_text + " " + answers_str)
        found_symbols = get_found_symbols(symbol_base_text, "", check_dollar, check_percent)
        if check_dollar and "$" not in found_symbols:
            continue
        if check_percent and "%" not in found_symbols:
            continue

        found_required, all_found_req = get_found_required_with_locations(
            file_text, answers_str if not exclude_answers else "", required_list, threshold=0.7
        )
        if not all_found_req:
            continue

        found_optional = get_found_optional_with_locations(
            file_text, answers_str if not exclude_answers else "", optional_list, threshold=0.7
        )

        if semantic_keyword_match(
            file_text, answers_str if not exclude_answers else "", related_list, threshold=0.7
        ):
            keywords_count += 1
        else:
            continue

        final_pass_count += 1

        row_dict = row.to_dict()
        row_dict["found_symbols"] = {
            symbol: ", ".join(places) for symbol, places in found_symbols.items()
        }
        row_dict["found_required"] = found_required
        row_dict["found_optional"] = found_optional

        results.append(row_dict)
    
    # All rows in a big DataFrame
    detailed_df = pd.DataFrame(results)
    detailed_df.to_csv("detailed_results.csv", index=False)
    print("[INFO] Wrote detailed_results.csv with pass/fail info.")

    filtered_df = pd.DataFrame(results)
    return (
        filtered_df,
        pdf_exists_count,
        english_count,
        short_answers_okay_count,
        no_unallowed_count,
        keywords_count,
        final_pass_count
    )


def get_found_symbols(pdf_text, answers_text, check_dollar, check_percent):
    """
    Stricter version: if user checks "Check for $ symbol", we only match
    actual '$' in the text (with digits after). 
    If user checks "Check for % symbol", we only match real percentages 
    like '42%', not just digits alone.

    We return a dict for each symbol: 
        {"$": ["pdf"], "%": ["pdf", "answers"]} 
    etc., just like before.
    """

    found_symbols = {}

    if check_dollar:
        # Pattern requires an actual '$' sign followed by digits 
        # (optionally with commas, or a decimal).
        # Example matches: "$100", "$3,450.50", etc.
        # If you want to allow more flexible formatting, adapt the pattern.
        money_pattern = r"\$\d{1,3}(,\d{3})*(\.\d+)?"
        # Explanation:
        #  \$          => literal dollar sign
        #  \d{1,3}     => 1-3 digits
        #  (,\d{3})*   => optional groups of a comma plus 3 digits
        #  (\.\d+)?    => optional decimal portion

        places = []
        if re.search(money_pattern, pdf_text):
            places.append("pdf")
        if re.search(money_pattern, answers_text):
            places.append("answers")

        if places:
            found_symbols["$"] = places

    if check_percent:
        # Pattern requires digits + optional decimal, then a literal '%'.
        # e.g. "42%", "3.5%", "100%", etc.
        percent_pattern = r"\d+(\.\d+)?%"
        places = []
        if re.search(percent_pattern, pdf_text):
            places.append("pdf")
        if re.search(percent_pattern, answers_text):
            places.append("answers")

        if places:
            found_symbols["%"] = places

    return found_symbols


def load_unallowed_phrases(file_path):
    phrases = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                phrase = line.strip()
                if phrase:
                    phrases.append(phrase)
    except Exception as e:
        print(f"Error loading {file_path}: {e}")
    return phrases


def count_unallowed_matches(pdf_text, unallowed_phrases):
    tokens_pdf = tokenize_to_words(pdf_text.lower())
    matched = []
    for phrase in unallowed_phrases:
        tokens_phrase = tokenize_to_words(phrase.lower())
        if phrase_in_tokens(tokens_phrase, tokens_pdf):
            matched.append(phrase)
    matched = list(set(matched))
    return len(matched), matched


def phrase_in_tokens(phrase_tokens, pdf_tokens, pdf_filename=None, phrase=None, row_index=None):
    n = len(phrase_tokens)
    if n == 0:
        return False
    for i in range(len(pdf_tokens) - n + 1):
        segment = pdf_tokens[i : i + n]
        if segment == phrase_tokens:
            return True
    return False


def tokenize_to_words(text):
    return re.findall(r"\w+", text)


def extract_pdf_text(pdf_path):
    text_content = []
    try:
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text() or ""
                text_content.append(page_text)
    except Exception as e:
        print(f"Error reading {pdf_path}: {e}")
        return ""
    return "\n".join(text_content)


def extract_docx_text(docx_path):
    try:
        text = docx2txt.process(docx_path)
        return text or ""
    except Exception as e:
        print(f"Error reading {docx_path}: {e}")
        return ""


def is_english_text(text, min_chars=50):
    text = text.strip()
    if len(text) < min_chars:
        return False
    try:
        return detect(text) == "en"
    except:
        return False


def is_ignored_question(question_text):
    q_lower = question_text.lower()
    if "check all that apply" in q_lower:
        return True
    yesno_starts = ["do you ", "are you ", "have you ", "did you "]
    for phrase in yesno_starts:
        if q_lower.startswith(phrase):
            return True
    if "how many" in q_lower:
        return True
    return False


def has_two_or_more_short_answers(answers_str, min_words=20):
    blocks = answers_str.split("----------")
    short_count = 0
    pending_question_text = None

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        parts = block.split(":", maxsplit=1)
        if len(parts) < 2:
            continue

        label_text = parts[0].strip()
        content_text = parts[1].strip()

        if label_text.lower().startswith("question "):
            pending_question_text = content_text
        elif label_text.lower().startswith("answer "):
            actual_question = pending_question_text or ""
            actual_answer = content_text
            if is_ignored_question(actual_question):
                continue
            word_count = len(actual_answer.split())
            if word_count < min_words:
                short_count += 1
                if short_count >= 2:
                    return True
            pending_question_text = None
        else:
            question_text = label_text
            answer_text = content_text
            if is_ignored_question(question_text):
                continue
            word_count = len(answer_text.split())
            if word_count < min_words:
                short_count += 1
                if short_count >= 2:
                    return True

    return short_count >= 2


def all_required_keywords_present(pdf_text, answers_text, required_list, threshold=0.7):
    combined_text = (pdf_text or "") + " " + (answers_text or "")
    doc = nlp(combined_text)
    for req_kw in required_list:
        req_doc = nlp(req_kw.lower())
        if not any(token.similarity(req_doc) >= threshold for token in doc):
            return False
    return True


def get_found_required_with_locations(pdf_text, answers_text, required_list, threshold=0.7):
    found_dict = {}
    doc_pdf = nlp(pdf_text or "")
    doc_answers = nlp(answers_text or "")
    missing_any = False
    for req_kw in required_list:
        req_kw_stripped = req_kw.strip()
        if not req_kw_stripped:
            continue
        req_doc = nlp(req_kw_stripped.lower())
        found_places = []
        if any(token.similarity(req_doc) >= threshold for token in doc_pdf):
            found_places.append("pdf")
        if any(token.similarity(req_doc) >= threshold for token in doc_answers):
            found_places.append("answers")
        if found_places:
            found_dict[req_kw] = found_places
        else:
            missing_any = True
    all_found = not missing_any
    return found_dict, all_found


def get_found_optional(pdf_text, answers_text, optional_list, threshold=0.7):
    combined_text = (pdf_text or "") + " " + (answers_text or "")
    doc = nlp(combined_text)
    found = []
    for opt_kw in optional_list:
        kw_doc = nlp(opt_kw.lower())
        if any(token.similarity(kw_doc) >= threshold for token in doc):
            found.append(opt_kw)
    return found


def get_found_optional_with_locations(pdf_text, answers_text, optional_list, threshold=0.7):
    found_dict = {}
    doc_pdf = nlp(pdf_text or "")
    doc_answers = nlp(answers_text or "")
    for opt_kw in optional_list:
        kw_stripped = opt_kw.strip()
        if not kw_stripped:
            continue
        opt_kw_doc = nlp(kw_stripped.lower())
        found_places = []
        if any(token.similarity(opt_kw_doc) >= threshold for token in doc_pdf):
            found_places.append("pdf")
        if any(token.similarity(opt_kw_doc) >= threshold for token in doc_answers):
            found_places.append("answers")
        if found_places:
            found_dict[opt_kw] = found_places
    return found_dict


def semantic_keyword_match(pdf_text, answers_text, user_keywords, threshold=0.7):
    combined_text = (pdf_text or "") + " " + (answers_text or "")
    doc = nlp(combined_text)
    kw_docs = [nlp(kw.strip()) for kw in user_keywords if kw.strip()]

    for token in doc:
        for kw_doc in kw_docs:
            sim = token.similarity(kw_doc)
            if sim >= threshold:
                return True
    return False


import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# After writing the CSV, read it and create a download button:
with open("detailed_results.csv", "rb") as file:
    st.download_button(
        label="Download Detailed Results CSV",
        data=file,
        file_name="detailed_results.csv",
        mime="text/csv"
    )

def get_gspread_credentials_from_streamlit_secrets():
    """Retrieve credentials from st.secrets to authorize gspread."""
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    service_account_info = dict(st.secrets["gcp_service_account"]) 
    creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
    return creds

def append_first_8_columns_to_google_sheet(filtered_df, job_title):
    """
    Appends up to 8 columns of 'filtered_df' to a Google Sheet tab named 'job_title'.

    1) Drops columns we never want:
       - 'download', 'found_symbols', 'found_required', 'found_optional', 'experience'
    2) Forces 'id' (if present) as the first column, 'answers' (if present) as the last column.
    3) Truncates the middle if there are more than 8 columns, ensuring 'id' and 'answers' remain.
    4) Fills NaN with '' and appends each row.
    5) Creates the worksheet if it doesn't exist (8 cols, 100 rows), appends a header row once.
    6) Prints debug info about the final columns.
    """
    # 1) Authorize using secrets
    creds = get_gspread_credentials_from_streamlit_secrets()
    gc = gspread.authorize(creds)

    # 2) Open your Google Spreadsheet by ID
    SPREADSHEET_ID = "11RLDHCyscViRceW8N_8I3okMcSKtHn-XPcJuPPNTeBE"
    sh = gc.open_by_key(SPREADSHEET_ID)

    # 3) Try to get the worksheet or create it
    try:
        worksheet = sh.worksheet(job_title)
        newly_created = False
    except gspread.exceptions.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=job_title, rows=100, cols=8)
        newly_created = True

    # 4) Drop unwanted columns
    sub_df = filtered_df.copy()
    drop_cols = ["download", "found_symbols", "found_required", "found_optional", "experience"]
    sub_df.drop(columns=drop_cols, errors="ignore", inplace=True)

    print("\n[DEBUG] Columns after dropping unwanted:", list(sub_df.columns))

    # 5) Reorder columns: 'id' first (if present), 'answers' last (if present)
    cols = list(sub_df.columns)
    id_exists = ("id" in cols)
    answers_exists = ("answers" in cols)

    if id_exists:
        cols.remove("id")
    if answers_exists:
        cols.remove("answers")

    proposed = []
    if id_exists:
        proposed.append("id")
    proposed.extend(cols)
    if answers_exists:
        proposed.append("answers")

    # 6) If we have more than 8 columns, keep 'id' front & 'answers' end, trim the middle
    def finalize_columns(col_order, has_id, has_ans):
        if len(col_order) <= 8:
            return col_order
        have_both = (has_id and has_ans)
        have_only_id = (has_id and not has_ans)
        have_only_answers = (has_ans and not has_id)
        
        if have_both:
            middle = col_order[1:-1]
            trimmed_middle = middle[:6]  # 1 for id, 6 for middle, 1 for answers => total 8
            return ["id"] + trimmed_middle + ["answers"]
        elif have_only_id:
            middle = col_order[1:]
            trimmed_middle = middle[:7]
            return ["id"] + trimmed_middle
        elif have_only_answers:
            middle = col_order[:-1]
            trimmed_middle = middle[:7]
            return trimmed_middle + ["answers"]
        else:
            return col_order[:8]

    final_cols = finalize_columns(proposed, id_exists, answers_exists)
    sub_df = sub_df[final_cols]

    sub_df = sub_df.fillna("")

    print("[DEBUG] Final columns for Google Sheet:", list(sub_df.columns))
    print("[DEBUG] Number of rows:", len(sub_df))

    # 7) If newly created, write headers first (assuming sub_df not empty)
    if newly_created and not sub_df.empty:
        headers = list(sub_df.columns)
        worksheet.append_row(headers, value_input_option="RAW")

    # 8) Append each row
    for _, row_data in sub_df.iterrows():
        row_values = row_data.tolist()
        worksheet.append_row(row_values, value_input_option="RAW")

    print(f"Appended {len(sub_df)} rows to worksheet '{job_title}' in your Google Sheet!")
